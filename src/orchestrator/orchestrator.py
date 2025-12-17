"""Single-cycle orchestrator (Phase 5.1).

This module runs one end-to-end cycle:
  snapshot -> market brief -> traders (parallel) -> risk -> manager -> plan -> execute -> sync positions -> portfolio update

Notes:
- Traders receive a thin brief and are expected to use tools.
- Execution is testnet-only (safety belt enforced by config/binance client).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from src.agents.manager import ManagerAgent, ManagerConfig
from src.agents.schemas import ManagerDecision, TradeProposal, DecisionType
from src.agents.macro_trader import MacroTrader, MacroTraderConfig
from src.agents.structure_trader import StructureTrader, StructureTraderConfig
from src.agents.technical_trader import TechnicalTrader, TechnicalTraderConfig
from src.agents.tools import ToolContext
from src.agents.tools.market_tools import get_firm_state
from src.config import AppConfig, load_config
from src.data.audit import AuditContext, AuditManager
from src.data.market_data import MarketDataIngestor
from src.data.mongo import MongoManager, jsonify
from src.data.schemas import MANAGER_DECISIONS, TRADE_PROPOSALS, PNL_REPORTS
from src.execution.binance_client import BinanceFuturesClient
from src.execution.executor import ExecutorConfig, OrderExecutor
from src.execution.planner import OrderPlanError, build_order_plan
from src.execution.position_tracker import PositionTracker
from src.execution.schemas import ExecutionStatus
from src.features.market_state import MarketStateBuilder
from src.portfolio.portfolio import PortfolioManager
from src.portfolio.reporting import ReportingEngine
from src.portfolio.trust import load_trust_scores
from src.risk.validator import validate_proposal


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_run_id() -> str:
    return _utc_now().strftime("run_%Y%m%d_%H%M%S")


def _default_cycle_id() -> str:
    return _utc_now().strftime("cycle_%Y%m%d_%H%M%S")


def _thin_market_brief(full: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
    per_symbol = full.get("per_symbol") or {}
    thin_per: Dict[str, Any] = {}
    for s in symbols:
        sym = per_symbol.get(s) or {}
        thin_per[s] = {
            "last_price": sym.get("last_price"),
            "spread": sym.get("spread"),
            "vol_regime": sym.get("regime", {}).get("volatility"),
            "trend_regime": sym.get("regime", {}).get("trend"),
        }

    return {
        "timestamp": full.get("timestamp"),
        "symbols": symbols,
        "neutral_summary": full.get("neutral_summary"),
        "per_symbol": thin_per,
        "note": "Thin brief. Use tools (get_market_brief/get_candles/get_indicator_pack/query_memory) to fetch details.",
    }


def _summarize_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract a small summary of tool outputs (errors/truncation only)."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        name = m.get("name")
        content = m.get("content") or ""
        try:
            obj = json.loads(content)
        except Exception:
            continue

        if isinstance(obj, dict) and obj.get("error"):
            out.append({"tool": name, "status": "error", "error": obj.get("error")})
            continue

        if isinstance(obj, dict) and obj.get("truncated") is True:
            note = obj.get("note")
            out.append({"tool": name, "status": "truncated", "note": note})
            continue
    return out


def _execution_status_from_report(report: Any) -> str:
    """Summarize an ExecutionReport into a simple status string."""
    try:
        results = list(getattr(report, "results", []) or [])
    except Exception:
        return "unknown"
    if not results:
        return "skipped"
    statuses = [r.status for r in results if getattr(r, "status", None) is not None]
    if any(s == ExecutionStatus.failed for s in statuses):
        return "failed"
    if any(s in {ExecutionStatus.placed, ExecutionStatus.already_exists} for s in statuses):
        return "success"
    return "skipped"


def _parse_agent_from_client_id(client_order_id: str) -> Optional[str]:
    """
    Parses agent_id from 'o_{hash}'.
    Since hashing destroys info, we cannot reverse it.
    However, we need agent attribution.
    
    CRITICAL FIX: The current make_client_order_id hashes the agent_id.
    We cannot reverse it.
    
    Workaround for MVP:
    We will rely on the Executor's OrderPlan which maps client_order_id -> agent_id.
    But here we only have the fill from Binance.
    
    Better approach:
    The OrderPlan knows the mapping. We should cache it or pass it to the portfolio update step.
    
    Actually, for MVP, we'll try to match the trade's symbol/side/time to the proposal.
    But that's brittle.
    
    Wait, the 'client_order_id' IS returned by Binance in the trade history.
    If we can't reverse the hash, we can't know the agent.
    
    Plan B:
    We don't need to reverse the hash if we stored the mapping "client_order_id -> agent_id" 
    during execution planning.
    
    The Orchestrator builds the plan. It can build a lookup map.
    """
    # Placeholder: requires lookup map passed from plan
    return None


@dataclass(frozen=True)
class OrchestratorConfig:
    # Total wall-clock budget for each trader/manager task (covers multi-turn tool calls).
    trader_timeout_s: float = 180.0
    manager_timeout_s: float = 180.0
    execute_testnet: bool = True
    extra_trader_instructions: Optional[str] = None
    # Phase 7: enable persistent memory (raw QnA + narrative summary + grounded ledger)
    # and allow LLM-based narrative compression.
    memory_compression: bool = False
    summarizer_model: str = "google/gemini-2.5-flash-lite"
    # Phase 12: trader filtering
    enabled_traders: Optional[List[str]] = None


@dataclass(frozen=True)
class CycleResult:
    run_id: str
    cycle_id: str
    snapshot_id: Optional[str]
    proposals: List[TradeProposal]
    manager_decision: Optional[ManagerDecision]
    order_plan_intents: int
    execution_status: str


class Orchestrator:
    def __init__(
        self,
        *,
        mongo: MongoManager,
        portfolio_manager: Optional[PortfolioManager] = None,
        reporting_engine: Optional[ReportingEngine] = None,
        config: Optional[AppConfig] = None,
        orchestrator_config: Optional[OrchestratorConfig] = None,
    ):
        self.mongo = mongo
        self.portfolio_manager = portfolio_manager
        self.reporting_engine = reporting_engine
        self.config = config or load_config()
        self.orchestrator_config = orchestrator_config or OrchestratorConfig()

    def _models(self) -> Tuple[str, str, str, str, str]:
        trader_1 = os.getenv("LLM_MODEL_TRADER_1") or "deepseek/deepseek-chat"
        trader_2 = os.getenv("LLM_MODEL_TRADER_2") or trader_1
        trader_3 = os.getenv("LLM_MODEL_TRADER_3") or trader_1
        trader_4 = os.getenv("LLM_MODEL_TRADER_4") or trader_1
        manager = (
            os.getenv("LLM_MODEL_MANAGER_FAST")
            or os.getenv("LLM_MODEL_MANAGER")
            or self.config.models.manager_model_fast
            or self.config.models.manager_model
        )
        return trader_1, trader_2, trader_3, trader_4, manager

    async def _persist_trade_proposal(self, proposal: TradeProposal) -> Optional[str]:
        doc = jsonify(proposal.model_dump(mode="json"))
        if not doc.get("run_id"):
            doc["run_id"] = proposal.run_id
        if not doc.get("cycle_id"):
            doc["cycle_id"] = proposal.cycle_id
        return await self.mongo.insert_one(TRADE_PROPOSALS, doc)

    async def _persist_manager_decision(self, decision: ManagerDecision) -> Optional[str]:
        doc = jsonify(decision.model_dump(mode="json"))
        return await self.mongo.insert_one(MANAGER_DECISIONS, doc)

    async def _persist_pnl_report(self, report: Dict[str, Any]) -> Optional[str]:
        return await self.mongo.insert_one(PNL_REPORTS, report)

    async def _run_trader(
        self,
        *,
        trader: Any,
        thin_brief: Dict[str, Any],
        firm_state: Dict[str, Any],
        cycle_id: str,
    ) -> TradeProposal:
        extra = (
            "You are a tool-calling agent. You are EXPECTED to use tools aggressively.\n"
            "Guidance:\n"
            "- Use up to your tool-call budget to gather facts before proposing any trade.\n"
            "- Prefer no-trade if facts are insufficient or edge is unclear.\n"
            "- Output ONLY TradeProposal JSON.\n"
        )
        if self.orchestrator_config.extra_trader_instructions:
            extra = extra + "\n" + self.orchestrator_config.extra_trader_instructions.strip() + "\n"
        proposal = await trader.decide(
            market_brief=thin_brief,
            firm_state=firm_state,
            extra_instructions=extra,
        )
        proposal.run_id = thin_brief.get("run_id") or proposal.run_id
        proposal.cycle_id = cycle_id
        proposal.agent_id = trader.agent_id
        # Do not trust model-provided timestamps; set receipt time deterministically.
        proposal.timestamp = _utc_now()
        return proposal

    async def run_cycle(
        self,
        *,
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
    ) -> CycleResult:
        await self.mongo.connect()
        await self.mongo.ensure_indexes()

        run_id = run_id or _default_run_id()
        cycle_id = cycle_id or _default_cycle_id()
        
        # Track start time for trade filtering (add a small buffer)
        cycle_start_ms = int((time.time() - 5) * 1000)

        audit = AuditManager(self.mongo)
        audit_ctx = AuditContext(run_id=run_id, agent_id="orchestrator")
        await audit.log("cycle_start", {"cycle_id": cycle_id}, ctx=audit_ctx)

        # 1) Build snapshot + full brief
        ingestor = MarketDataIngestor.from_app_config(self.config, mongo=self.mongo, run_id=run_id)
        snapshot = await ingestor.fetch_and_store_snapshot()
        snapshot_id = snapshot.get("_id")
        builder = MarketStateBuilder()
        full_brief = builder.build_market_brief(snapshot)
        await audit.log(
            "market_snapshot_ready",
            {"cycle_id": cycle_id, "snapshot_ref": str(snapshot_id) if snapshot_id else None},
            ctx=audit_ctx,
        )

        symbols = list(self.config.trading.symbols)
        thin_brief = _thin_market_brief(full_brief, symbols)
        thin_brief["run_id"] = run_id
        thin_brief["cycle_id"] = cycle_id

        tools_ctx = ToolContext(
            mongo=self.mongo,
            config=self.config,
            market_state_builder=builder,
            news_connector=None,
            run_id=run_id,
        )

        # 2) Firm state for all downstream steps
        firm_state_dict = await asyncio.wait_for(get_firm_state(context=tools_ctx), timeout=15.0)

        trader_model_1, trader_model_2, trader_model_3, trader_model_4, manager_model = self._models()
        await audit.log(
            "models_selected",
            {
                "cycle_id": cycle_id,
                "trader_models": {
                    "tech_trader_1": trader_model_1,
                    "tech_trader_2": trader_model_2,
                    "macro_trader_1": trader_model_3,
                    "structure_trader_1": trader_model_4,
                },
                "manager_model": manager_model,
            },
            ctx=audit_ctx,
        )

        # 3) Run traders in parallel
        enable_phase7 = bool(self.orchestrator_config.memory_compression)
        summarizer_model = (
            os.getenv("LLM_MODEL_SUMMARIZER")
            or os.getenv("LLM_MODEL_SUMMARISER")
            or self.orchestrator_config.summarizer_model
        )
        trader_1 = TechnicalTrader(
            agent_id="tech_trader_1",
            config=TechnicalTraderConfig(
                model=trader_model_1,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ],
        )
        trader_2 = TechnicalTrader(
            agent_id="tech_trader_2",
            config=TechnicalTraderConfig(
                model=trader_model_2,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ],
        )

        macro_1 = MacroTrader(
            agent_id="macro_trader_1",
            config=MacroTraderConfig(
                model=trader_model_3,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_recent_news",
                "tavily_search",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ],
        )

        structure_1 = StructureTrader(
            agent_id="structure_trader_1",
            config=StructureTraderConfig(
                model=trader_model_4,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_funding_oi_history",
                "get_orderbook_top",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ],
        )

        # Initialize portfolios if needed
        if self.portfolio_manager:
            budget_notional = float(self.config.risk.agent_budget_notional_usd)
            if trader_1.agent_id not in self.portfolio_manager.portfolios:
                self.portfolio_manager.initialize_agent(trader_1.agent_id, budget_notional)
            if trader_2.agent_id not in self.portfolio_manager.portfolios:
                self.portfolio_manager.initialize_agent(trader_2.agent_id, budget_notional)
            if macro_1.agent_id not in self.portfolio_manager.portfolios:
                self.portfolio_manager.initialize_agent(macro_1.agent_id, budget_notional)
            if structure_1.agent_id not in self.portfolio_manager.portfolios:
                self.portfolio_manager.initialize_agent(structure_1.agent_id, budget_notional)

        # Ensure firm budgets map aligns with actual agent_ids used in this run.
        budgets = dict(firm_state_dict.get("agent_budgets") or {})
        budgets[trader_1.agent_id] = float(self.config.risk.agent_budget_notional_usd)
        budgets[trader_2.agent_id] = float(self.config.risk.agent_budget_notional_usd)
        budgets[macro_1.agent_id] = float(self.config.risk.agent_budget_notional_usd)
        budgets[structure_1.agent_id] = float(self.config.risk.agent_budget_notional_usd)
        firm_state_dict["agent_budgets"] = budgets
        firm_state_dict["capital_usdt"] = float(sum(budgets.values())) if budgets else firm_state_dict.get("capital_usdt")

        async def _safe_trader(trader: Any, *, model: str) -> TradeProposal:
            try:
                return await asyncio.wait_for(
                    self._run_trader(
                        trader=trader,
                        thin_brief=thin_brief,
                        firm_state=firm_state_dict,
                        cycle_id=cycle_id,
                    ),
                    timeout=self.orchestrator_config.trader_timeout_s,
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                await audit.log(
                    "trader_error",
                    {
                        "cycle_id": cycle_id,
                        "agent_id": trader.agent_id,
                        "model": model,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "error_repr": repr(e),
                    },
                    ctx=audit_ctx,
                )
                return TradeProposal(
                    agent_id=trader.agent_id,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    timestamp=_utc_now(),
                    trades=[],
                    notes=f"Trader error; forced no-trade. error={e}",
                )

        traders: List[Tuple[Any, str]] = [
            (trader_1, trader_model_1),
            (trader_2, trader_model_2),
            (macro_1, trader_model_3),
            (structure_1, trader_model_4),
        ]
        
        # Phase 12: Filter traders if enabled_traders is specified
        if self.orchestrator_config.enabled_traders:
            enabled_set = set(self.orchestrator_config.enabled_traders)
            filtered_traders = [(t, m) for (t, m) in traders if t.agent_id in enabled_set]
            skipped_traders = [t.agent_id for (t, _m) in traders if t.agent_id not in enabled_set]
            traders = filtered_traders
            if skipped_traders:
                await audit.log(
                    "traders_filtered",
                    {
                        "cycle_id": cycle_id,
                        "enabled": list(enabled_set),
                        "skipped": skipped_traders,
                    },
                    ctx=audit_ctx,
                )
        
        async def _run_one(trader: Any, *, model: str) -> Tuple[TradeProposal, str]:
            p = await _safe_trader(trader, model=model)
            return p, model

        proposals: List[TradeProposal] = []
        tasks = [asyncio.create_task(_run_one(t, model=m)) for (t, m) in traders]
        for fut in asyncio.as_completed(tasks):
            p, model = await fut
            proposals.append(p)
            await audit.log(
                "trader_proposal_ready",
                {
                    "cycle_id": cycle_id,
                    "agent_id": p.agent_id,
                    "model": model,
                    "proposal": p.model_dump(mode="json"),
                },
                ctx=audit_ctx,
            )
            await self._persist_trade_proposal(p)
        await audit.log(
            "tool_results_summary_traders",
            {
                "cycle_id": cycle_id,
                "agents": {t.agent_id: _summarize_tool_messages(t.last_messages) for (t, _m) in traders},
            },
            ctx=audit_ctx,
        )
        await audit.log(
            "tool_usage_traders",
            {
                "cycle_id": cycle_id,
                "agents": {
                    t.agent_id: {"tool_calls": len(t.last_tool_calls), "tools": [c.get("name") for c in t.last_tool_calls]}
                    for (t, _m) in traders
                },
            },
            ctx=audit_ctx,
        )
        await audit.log(
            "trader_proposals_ready",
            {"cycle_id": cycle_id, "proposals": [p.model_dump(mode="json") for p in proposals]},
            ctx=audit_ctx,
        )

        # 4) Risk validation (deterministic) + manager decision
        compliance_reports = [
            await validate_proposal(p, tools_context=tools_ctx, firm_state=firm_state_dict, market_brief=full_brief)
            for p in proposals
        ]
        await audit.log(
            "risk_reports_ready",
            {"cycle_id": cycle_id, "reports": [r.model_dump(mode="json") for r in compliance_reports]},
            ctx=audit_ctx,
        )

        manager = ManagerAgent(
            manager_id="manager",
            config=ManagerConfig(model=manager_model, max_tool_calls=4, max_tool_turns=4),
            tools_context=tools_ctx,
        )
        manager_extra = (
            "For EVERY proposal trade you decide on, set DecisionItem.agent_id and DecisionItem.trade_index.\n"
            "trade_index is the index into that agent's TradeProposal.trades array.\n"
            "Hard violations MUST be vetoed.\n"
            "Output ONLY ManagerDecision JSON.\n"
        )

        manager_decision: Optional[ManagerDecision] = None
        try:
            trust_scores = await load_trust_scores(
                mongo=self.mongo,
                agent_ids=[p.agent_id for p in proposals],
                default_trust=50.0,
            )
            await audit.log(
                "trust_scores_loaded",
                {"cycle_id": cycle_id, "trust_scores": trust_scores},
                ctx=audit_ctx,
            )
            manager_decision = await asyncio.wait_for(
                manager.decide(
                    proposals=[p.model_dump(mode="json") for p in proposals],
                    compliance_reports=[r.model_dump(mode="json") for r in compliance_reports],
                    trust_scores=trust_scores,
                    firm_state=firm_state_dict,
                    positions_by_agent=None,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    extra_instructions=manager_extra,
                ),
                timeout=self.orchestrator_config.manager_timeout_s,
            )
            # Do not trust model-provided timestamps or ids; normalize deterministically.
            manager_decision.manager_id = manager.manager_id
            manager_decision.run_id = run_id
            manager_decision.cycle_id = cycle_id
            manager_decision.timestamp = _utc_now()
            # Execution safety: if manager forgot agent_id/trade_index for an approve/resize, defer it.
            for item in manager_decision.decisions:
                if item.decision in {DecisionType.approve, DecisionType.resize} and (
                    item.agent_id is None or item.trade_index is None
                ):
                    item.decision = DecisionType.defer
                    item.approved_size_usdt = None
                    item.approved_leverage = None
                    item.notes = (item.notes or "") + " [SYSTEM: missing agent_id/trade_index; deferred for safety]"
            await audit.log(
                "tool_usage_manager",
                {
                    "cycle_id": cycle_id,
                    "agent_id": manager.manager_id,
                    "tool_calls": len(manager.last_tool_calls),
                    "tools": [c.get("name") for c in manager.last_tool_calls],
                },
                ctx=audit_ctx,
            )
            await self._persist_manager_decision(manager_decision)
            await audit.log(
                "manager_decision_ready",
                {"cycle_id": cycle_id, "decision": manager_decision.model_dump(mode="json")},
                ctx=audit_ctx,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            await audit.log(
                "manager_error",
                {
                    "cycle_id": cycle_id,
                    "model": manager_model,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "error_repr": repr(e),
                },
                ctx=audit_ctx,
            )

        # 5) Build plan + execute
        order_plan_intents = 0
        execution_status = "skipped"
        plan = None
        
        # Mapping to resolve attribution later: client_order_id -> agent_id
        order_id_to_agent: Dict[str, str] = {}
        
        if manager_decision is not None:
            try:
                plan = build_order_plan(proposals=proposals, manager_decision=manager_decision)
                order_plan_intents = len(plan.intents)
                for intent in plan.intents:
                    if intent.agent_id:
                        order_id_to_agent[intent.client_order_id] = intent.agent_id
                        
                await audit.log(
                    "order_plan_ready",
                    {"cycle_id": cycle_id, "intents": [i.model_dump(mode="json") for i in plan.intents]},
                    ctx=audit_ctx,
                )
            except OrderPlanError as e:
                await audit.log(
                    "order_plan_error",
                    {"cycle_id": cycle_id, "error": str(e)},
                    ctx=audit_ctx,
                )

        report = None # To hold execution report if any

        if self.orchestrator_config.execute_testnet and order_plan_intents > 0 and plan is not None:
            try:
                await audit.log(
                    "execution_started",
                    {
                        "cycle_id": cycle_id,
                        "order_plan_intents": order_plan_intents,
                    },
                    ctx=audit_ctx,
                )
                client = BinanceFuturesClient(
                    testnet=self.config.binance.testnet,
                    base_url=self.config.binance.base_url,
                    recv_window=self.config.binance.recv_window,
                    allow_mainnet=self.config.binance.allow_mainnet,
                    audit_mgr=self.mongo,
                    run_id=run_id,
                    agent_id="execution",
                )
                executor = OrderExecutor(
                    mongo=self.mongo,
                    client=client,
                    config=ExecutorConfig(),
                )
                if plan is not None:
                    report = await executor.execute_plan(plan)
                    execution_status = _execution_status_from_report(report)
                    await audit.log(
                        "execution_report",
                        {"cycle_id": cycle_id, "report": report.model_dump(mode="json")},
                        ctx=audit_ctx,
                    )
                    await audit.log(
                        "execution_complete",
                        {
                            "cycle_id": cycle_id,
                            "status": execution_status,
                            "results": [r.model_dump(mode="json") for r in (report.results or [])],
                        },
                        ctx=audit_ctx,
                    )

                    tracker = PositionTracker(mongo=self.mongo, client=client)
                    await tracker.sync_positions(run_id=run_id, cycle_id=cycle_id, symbols=symbols)
                    await audit.log(
                        "positions_synced",
                        {"cycle_id": cycle_id},
                        ctx=audit_ctx,
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                execution_status = f"error:{e}"
                await audit.log(
                    "execution_error",
                    {"cycle_id": cycle_id, "error": str(e)},
                    ctx=audit_ctx,
                )

        # --- Portfolio Update (Runs every cycle) ---
        if self.portfolio_manager and self.reporting_engine:
            try:
                # 1. Process Fills (if execution happened)
                if report and self.orchestrator_config.execute_testnet:
                    # Re-instantiate client if needed (scope issue? no, we need client to fetch trades)
                    # Ideally client should be available.
                    # If we executed, we have 'client' variable in scope? NO, it was in inner block.
                    # We need to instantiate client again or move instantiation out.
                    # Instantiating client is cheap (just config).
                    client = BinanceFuturesClient(
                        testnet=self.config.binance.testnet,
                        base_url=self.config.binance.base_url,
                        recv_window=self.config.binance.recv_window,
                        allow_mainnet=self.config.binance.allow_mainnet,
                        audit_mgr=self.mongo,
                        run_id=run_id,
                        agent_id="execution",
                    )
                    
                    traded_symbols = {i.symbol for i in plan.intents} if plan else set()
                    for sym in traded_symbols:
                        try:
                            recent_trades = client.get_recent_trades(symbol=sym, start_time=cycle_start_ms)
                            
                            # Build map: exchange_order_id -> agent_id from the REPORT
                            exchange_id_to_agent = {}
                            if report and report.results:
                                for res in report.results:
                                    if res.exchange_order_id and res.client_order_id:
                                        ag_id = order_id_to_agent.get(res.client_order_id)
                                        if ag_id:
                                            exchange_id_to_agent[res.exchange_order_id] = ag_id
                            
                            for t in recent_trades:
                                tid = t.get("orderId")
                                agent_id = exchange_id_to_agent.get(tid)
                                
                                if agent_id:
                                    self.portfolio_manager.on_fill(
                                        agent_id=agent_id,
                                        symbol=t["symbol"],
                                        side=t["side"],
                                        quantity=float(t["qty"]),
                                        price=float(t["price"]),
                                        fee=float(t.get("commission", 0))
                                    )
                        except Exception as ex:
                            await audit.log("portfolio_trade_fetch_error", {"symbol": sym, "error": str(ex)}, ctx=audit_ctx)

                # 2. Mark to Market (Always)
                current_prices = {s: d["last_price"] for s, d in thin_brief.get("per_symbol", {}).items() if d.get("last_price")}
                self.portfolio_manager.mark_to_market(current_prices)
                
                # 3. Generate Report (Always)
                pnl_report = self.reporting_engine.generate_cycle_report(run_id, cycle_id)
                await self._persist_pnl_report(pnl_report)
                await audit.log("pnl_report_generated", {"report": pnl_report}, ctx=audit_ctx)
                await audit.log(
                    "pnl_updated",
                    {
                        "cycle_id": cycle_id,
                        "firm_metrics": pnl_report.get("firm_metrics"),
                        "agent_metrics": pnl_report.get("agent_metrics"),
                    },
                    ctx=audit_ctx,
                )

            except Exception as e:
                await audit.log("portfolio_update_error", {"error": str(e)}, ctx=audit_ctx)

        await audit.log(
            "cycle_end",
            {
                "cycle_id": cycle_id,
                "order_plan_intents": order_plan_intents,
                "execution_status": execution_status,
                "t_s": time.time(),
            },
            ctx=audit_ctx,
        )

        return CycleResult(
            run_id=run_id,
            cycle_id=cycle_id,
            snapshot_id=str(snapshot_id) if snapshot_id else None,
            proposals=proposals,
            manager_decision=manager_decision,
            order_plan_intents=order_plan_intents,
            execution_status=str(execution_status),
        )

    async def run_cycle_from_snapshot(
        self,
        *,
        source_run_id: str,
        snapshot: Dict[str, Any],
        run_id: str,
        cycle_id: str,
        models: Optional[Dict[str, str]] = None,
    ) -> CycleResult:
        """Replay a single cycle using an already-stored snapshot (Phase 10.2).

        - Never fetches live market/news data.
        - Skips execution and portfolio/P&L updates (decision replay only).
        """
        await self.mongo.connect()
        await self.mongo.ensure_indexes()

        audit = AuditManager(self.mongo)
        audit_ctx = AuditContext(run_id=run_id, agent_id="replay")
        await audit.log(
            "replay_cycle_start",
            {"cycle_id": cycle_id, "source_run_id": source_run_id, "source_snapshot_id": str(snapshot.get("_id"))},
            ctx=audit_ctx,
        )

        builder = MarketStateBuilder()
        full_brief = builder.build_market_brief(snapshot)
        symbols = list(self.config.trading.symbols)
        thin_brief = _thin_market_brief(full_brief, symbols)
        thin_brief["run_id"] = run_id
        thin_brief["cycle_id"] = cycle_id

        tools_ctx = ToolContext(
            mongo=self.mongo,
            config=self.config,
            market_state_builder=builder,
            news_connector=None,
            run_id=run_id,
            data_run_id=source_run_id,
            as_of=snapshot.get("timestamp"),
            snapshot=snapshot,
            replay_mode=True,
        )

        firm_state_dict = await asyncio.wait_for(get_firm_state(context=tools_ctx), timeout=15.0)

        # Prefer per-cycle original model selection if provided; otherwise fall back to env defaults.
        trader_model_1, trader_model_2, trader_model_3, trader_model_4, manager_model = self._models()
        if models:
            trader_model_1 = models.get("tech_trader_1") or trader_model_1
            trader_model_2 = models.get("tech_trader_2") or trader_model_2
            trader_model_3 = models.get("macro_trader_1") or trader_model_3
            trader_model_4 = models.get("structure_trader_1") or trader_model_4
            manager_model = models.get("manager") or manager_model

        await audit.log(
            "replay_models_selected",
            {
                "cycle_id": cycle_id,
                "source_run_id": source_run_id,
                "trader_models": {
                    "tech_trader_1": trader_model_1,
                    "tech_trader_2": trader_model_2,
                    "macro_trader_1": trader_model_3,
                    "structure_trader_1": trader_model_4,
                },
                "manager_model": manager_model,
            },
            ctx=audit_ctx,
        )

        enable_phase7 = bool(self.orchestrator_config.memory_compression)
        summarizer_model = (
            os.getenv("LLM_MODEL_SUMMARIZER")
            or os.getenv("LLM_MODEL_SUMMARISER")
            or self.orchestrator_config.summarizer_model
        )

        trader_1 = TechnicalTrader(
            agent_id="tech_trader_1",
            config=TechnicalTraderConfig(
                model=trader_model_1,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
            ],
        )
        trader_2 = TechnicalTrader(
            agent_id="tech_trader_2",
            config=TechnicalTraderConfig(
                model=trader_model_2,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
            ],
        )
        macro_1 = MacroTrader(
            agent_id="macro_trader_1",
            config=MacroTraderConfig(
                model=trader_model_3,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_recent_news",
                "get_position_summary",
                "get_firm_state",
            ],
        )
        structure_1 = StructureTrader(
            agent_id="structure_trader_1",
            config=StructureTraderConfig(
                model=trader_model_4,
                max_tool_calls=6,
                max_tool_turns=6,
                enable_phase7_context=enable_phase7,
                enable_phase7_compression=enable_phase7,
                phase7_summarizer_model=summarizer_model,
            ),
            tools_context=tools_ctx,
            allowed_tools=[
                "get_market_brief",
                "get_funding_oi_history",
                "get_orderbook_top",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
            ],
        )

        async def _safe_trader(trader: Any, *, model: str) -> TradeProposal:
            try:
                return await asyncio.wait_for(
                    self._run_trader(
                        trader=trader,
                        thin_brief=thin_brief,
                        firm_state=firm_state_dict,
                        cycle_id=cycle_id,
                    ),
                    timeout=self.orchestrator_config.trader_timeout_s,
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                await audit.log(
                    "replay_trader_error",
                    {
                        "cycle_id": cycle_id,
                        "agent_id": trader.agent_id,
                        "model": model,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "error_repr": repr(e),
                    },
                    ctx=audit_ctx,
                )
                return TradeProposal(
                    agent_id=trader.agent_id,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    timestamp=_utc_now(),
                    trades=[],
                    notes=f"Replay trader error; forced no-trade. error={e}",
                )

        traders: List[Tuple[Any, str]] = [
            (trader_1, trader_model_1),
            (trader_2, trader_model_2),
            (macro_1, trader_model_3),
            (structure_1, trader_model_4),
        ]
        proposals: List[TradeProposal] = list(
            await asyncio.gather(*[_safe_trader(t, model=m) for (t, m) in traders])
        )
        await audit.log(
            "replay_trader_proposals_ready",
            {"cycle_id": cycle_id, "proposals": [p.model_dump(mode="json") for p in proposals]},
            ctx=audit_ctx,
        )
        for p in proposals:
            await self._persist_trade_proposal(p)

        compliance_reports = [
            await validate_proposal(p, tools_context=tools_ctx, firm_state=firm_state_dict, market_brief=full_brief)
            for p in proposals
        ]
        await audit.log(
            "replay_risk_reports_ready",
            {"cycle_id": cycle_id, "reports": [r.model_dump(mode="json") for r in compliance_reports]},
            ctx=audit_ctx,
        )

        manager = ManagerAgent(
            manager_id="manager",
            config=ManagerConfig(model=manager_model, max_tool_calls=4, max_tool_turns=4),
            tools_context=tools_ctx,
        )

        manager_decision: Optional[ManagerDecision] = None
        try:
            trust_scores = await load_trust_scores(
                mongo=self.mongo,
                agent_ids=[p.agent_id for p in proposals],
                default_trust=50.0,
            )
            manager_decision = await asyncio.wait_for(
                manager.decide(
                    proposals=[p.model_dump(mode="json") for p in proposals],
                    compliance_reports=[r.model_dump(mode="json") for r in compliance_reports],
                    trust_scores=trust_scores,
                    firm_state=firm_state_dict,
                    positions_by_agent=None,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    extra_instructions="Replay run: skip execution; output ONLY ManagerDecision JSON.",
                ),
                timeout=self.orchestrator_config.manager_timeout_s,
            )
            manager_decision.manager_id = manager.manager_id
            manager_decision.run_id = run_id
            manager_decision.cycle_id = cycle_id
            manager_decision.timestamp = _utc_now()
            await self._persist_manager_decision(manager_decision)
            await audit.log(
                "replay_manager_decision_ready",
                {"cycle_id": cycle_id, "decision": manager_decision.model_dump(mode="json")},
                ctx=audit_ctx,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            await audit.log(
                "replay_manager_error",
                {
                    "cycle_id": cycle_id,
                    "model": manager_model,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "error_repr": repr(e),
                },
                ctx=audit_ctx,
            )

        await audit.log(
            "replay_cycle_end",
            {
                "cycle_id": cycle_id,
                "source_run_id": source_run_id,
                "source_snapshot_id": str(snapshot.get("_id")),
            },
            ctx=audit_ctx,
        )

        return CycleResult(
            run_id=run_id,
            cycle_id=cycle_id,
            snapshot_id=str(snapshot.get("_id")) if snapshot.get("_id") else None,
            proposals=proposals,
            manager_decision=manager_decision,
            order_plan_intents=0,
            execution_status="replay_skipped",
        )


__all__ = ["Orchestrator", "OrchestratorConfig", "CycleResult"]
