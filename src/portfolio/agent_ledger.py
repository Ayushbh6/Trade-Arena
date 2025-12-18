"""Deterministic per-agent budget/margin ledger.

This ledger is strategy-neutral and derived from the approved order plan
and observed exchange positions (via Mongo `positions` attribution).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.data.mongo import MongoManager, utc_now, jsonify
from src.data.schemas import AGENT_LEDGERS, POSITIONS
from src.execution.schemas import OrderLeg, OrderPlan


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def estimate_required_margin_usdt(*, notional_usdt: float, leverage: Optional[float]) -> float:
    lev = float(leverage) if leverage and leverage > 0 else 1.0
    return float(notional_usdt) / lev


@dataclass(frozen=True)
class LedgerOpenItem:
    symbol: str
    trade_index: Optional[int]
    notional_usdt: float
    leverage: Optional[float]
    required_margin_usdt: float
    intent_id: Optional[str] = None
    client_order_id: Optional[str] = None


@dataclass
class AgentLedger:
    run_id: str
    agent_id: str
    firm_capital_usdt: float
    initial_budget_usdt: float
    reserved_margin_usdt: float
    available_budget_usdt: float
    open_items: List[LedgerOpenItem]

    def to_doc(self, *, cycle_id: Optional[str] = None) -> Dict[str, Any]:
        return jsonify(
            {
                "run_id": self.run_id,
                "cycle_id": cycle_id,
                "timestamp": utc_now(),
                "agent_id": self.agent_id,
                "firm_capital_usdt": self.firm_capital_usdt,
                "initial_budget_usdt": self.initial_budget_usdt,
                "reserved_margin_usdt": self.reserved_margin_usdt,
                "available_budget_usdt": self.available_budget_usdt,
                "open_items": [i.__dict__ for i in self.open_items],
            }
        )


class AgentLedgerManager:
    def __init__(self, *, mongo: Optional[MongoManager]):
        self.mongo = mongo

    async def _latest_ledgers(self, *, run_id: str) -> Dict[str, Dict[str, Any]]:
        if self.mongo is None:
            return {}
        await self.mongo.connect()
        col = self.mongo.collection(AGENT_LEDGERS)
        docs = await col.find({"run_id": run_id}).sort("timestamp", -1).limit(200).to_list(length=200)
        out: Dict[str, Dict[str, Any]] = {}
        for d in docs or []:
            aid = d.get("agent_id")
            if aid and aid not in out:
                out[str(aid)] = d
        return out

    async def compute_from_plan(
        self,
        *,
        run_id: str,
        plan: OrderPlan,
        firm_capital_usdt: float,
        per_agent_budget_usdt: Dict[str, float],
    ) -> Dict[str, AgentLedger]:
        ledgers: Dict[str, AgentLedger] = {}

        open_items_by_agent: Dict[str, Dict[Tuple[str, Optional[int]], LedgerOpenItem]] = {}
        for intent in plan.intents:
            if intent.leg != OrderLeg.entry:
                continue
            if not intent.agent_id:
                continue
            key = (intent.symbol, intent.trade_index)
            required = estimate_required_margin_usdt(notional_usdt=float(intent.notional_usdt), leverage=_safe_float(intent.leverage))
            item = LedgerOpenItem(
                symbol=str(intent.symbol),
                trade_index=int(intent.trade_index) if intent.trade_index is not None else None,
                notional_usdt=float(intent.notional_usdt),
                leverage=_safe_float(intent.leverage),
                required_margin_usdt=float(required),
                intent_id=str(intent.intent_id) if intent.intent_id else None,
                client_order_id=str(intent.client_order_id) if intent.client_order_id else None,
            )
            open_items_by_agent.setdefault(str(intent.agent_id), {})[key] = item

        for agent_id, budget in per_agent_budget_usdt.items():
            items = list((open_items_by_agent.get(agent_id) or {}).values())
            reserved = float(sum(i.required_margin_usdt for i in items))
            available = max(0.0, float(budget) - reserved)
            ledgers[agent_id] = AgentLedger(
                run_id=run_id,
                agent_id=agent_id,
                firm_capital_usdt=float(firm_capital_usdt),
                initial_budget_usdt=float(budget),
                reserved_margin_usdt=reserved,
                available_budget_usdt=available,
                open_items=items,
            )

        return ledgers

    async def reconcile_with_positions(
        self,
        *,
        run_id: str,
        ledgers: Dict[str, AgentLedger],
    ) -> Dict[str, AgentLedger]:
        """Drop open items for symbols that are no longer open per Mongo positions attribution."""
        if self.mongo is None:
            return ledgers
        await self.mongo.connect()
        pos_docs = await self.mongo.collection(POSITIONS).find({"run_id": run_id}).to_list(length=200)

        open_by_agent: Dict[str, set] = {}
        for p in pos_docs or []:
            aid = p.get("agent_owner")
            sym = p.get("symbol")
            qty = p.get("qty")
            if not aid or not sym:
                continue
            q = _safe_float(qty) or 0.0
            if abs(q) < 1e-12:
                continue
            open_by_agent.setdefault(str(aid), set()).add(str(sym))

        for agent_id, ledger in list(ledgers.items()):
            open_syms = open_by_agent.get(agent_id, set())
            if not open_syms:
                ledger.open_items = []
                ledger.reserved_margin_usdt = 0.0
                ledger.available_budget_usdt = float(ledger.initial_budget_usdt)
                continue
            kept = [i for i in ledger.open_items if i.symbol in open_syms]
            reserved = float(sum(i.required_margin_usdt for i in kept))
            ledger.open_items = kept
            ledger.reserved_margin_usdt = reserved
            ledger.available_budget_usdt = max(0.0, float(ledger.initial_budget_usdt) - reserved)

        return ledgers

    async def persist(
        self,
        *,
        run_id: str,
        cycle_id: Optional[str],
        ledgers: Dict[str, AgentLedger],
    ) -> None:
        if self.mongo is None:
            return
        await self.mongo.connect()
        col = self.mongo.collection(AGENT_LEDGERS)
        docs = [l.to_doc(cycle_id=cycle_id) for l in ledgers.values()]
        if not docs:
            return
        await col.insert_many(docs)


__all__ = ["AgentLedgerManager", "AgentLedger", "LedgerOpenItem", "estimate_required_margin_usdt"]
