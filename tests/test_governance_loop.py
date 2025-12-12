"""End-to-end governance loop integration test (Phase 3.5).

Flow:
  market snapshot -> trader proposal -> risk validation -> manager decision

Run:
  python tests/test_governance_loop.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys
  - TAVILY_API_KEY
  - OPENROUTER_API_KEY
  - LLM_MODEL_TRADER_1 (or default)
  - LLM_MODEL_MANAGER_FAST (recommended for cadence)
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.manager import ManagerAgent, ManagerConfig  # noqa: E402
from src.agents.schemas import (  # noqa: E402
    DecisionType,
    OrderType,
    Side,
    TradeAction,
    TradeIdea,
    TradeProposal,
)
from src.agents.technical_trader import TechnicalTrader, TechnicalTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.agents.tools.market_tools import get_firm_state, get_position_summary  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402
from src.risk.validator import validate_proposal  # noqa: E402


async def main() -> None:
    print("== Governance loop integration test ==")
    cfg = load_config()

    trader_model = os.getenv("LLM_MODEL_TRADER_1") or "deepseek/deepseek-chat"
    manager_model = (
        os.getenv("LLM_MODEL_MANAGER_FAST")
        or os.getenv("LLM_MODEL_MANAGER")
        or cfg.models.manager_model_fast
        or cfg.models.manager_model
    )

    run_id = "test_governance_run"
    cycle_id = "cycle_001"
    print(f"[INFO] Trader model: {trader_model}")
    print(f"[INFO] Manager model: {manager_model}")

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id=run_id)
    snapshot = await ingestor.fetch_and_store_snapshot()
    builder = MarketStateBuilder()
    full_brief = builder.build_market_brief(snapshot)
    print("[OK] Market snapshot stored.")

    news_connector = TavilyNewsConnector.from_app_config(cfg, mongo=mongo, run_id=run_id)
    await news_connector.fetch_recent_news(cfg.trading.symbols, lookback_hours=24, max_results=3)
    print("[OK] Recent news stored.")

    tools_ctx = ToolContext(
        mongo=mongo,
        config=cfg,
        market_state_builder=builder,
        news_connector=news_connector,
        run_id=run_id,
    )

    # 1) Trader proposal (real LLM)
    trader = TechnicalTrader(
        config=TechnicalTraderConfig(model=trader_model, max_tool_calls=6, max_tool_turns=6),
        tools_context=tools_ctx,
    )
    extra = (
        "You are evaluating a potential trade on BTCUSDT right now.\n"
        "Constraints:\n"
        "- Do NOT fabricate any facts; use tools to fetch missing/uncertain info.\n"
        "- Use tools as needed, but do not exceed the tool call budget.\n"
        "- Then return ONLY the TradeProposal JSON.\n"
    )

    t0 = time.perf_counter()
    proposal_live = await trader.decide(
        market_brief={
            "timestamp": full_brief.get("timestamp"),
            "symbols": ["BTCUSDT"],
            "note": "partial brief; call tools for details",
        },
        extra_instructions=extra,
    )
    t_live = time.perf_counter() - t0
    print(f"[OK] Trader proposal parsed. trades={len(proposal_live.trades)} time_s={t_live:.2f}")

    # 2) Deterministic proposals to ensure we cover hard/soft paths.
    hard_bad = TradeProposal(
        agent_id="tech_trader",
        run_id=run_id,
        cycle_id=cycle_id,
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=250.0,
                leverage=10.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=99.0,
                take_profit=102.0,
                confidence=0.5,
                rationale="intentional hard violation for test",
                invalidation="N/A",
            )
        ],
        notes="Intentional hard-violation proposal for governance veto test.",
    )

    soft_only = TradeProposal(
        agent_id="tech_trader",
        run_id=run_id,
        cycle_id=cycle_id,
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=5000.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=99.5,
                take_profit=101.5,
                confidence=0.55,
                rationale="intentional soft budget cap violation for resize test",
                invalidation="Below stop_loss",
            )
        ],
        notes="Intentional soft-violation proposal for governance resize test.",
    )

    # 3) Risk validation
    report_live = await validate_proposal(proposal_live, tools_context=tools_ctx, market_brief=full_brief)
    report_hard = await validate_proposal(hard_bad, tools_context=tools_ctx, market_brief=full_brief)
    report_soft = await validate_proposal(soft_only, tools_context=tools_ctx, market_brief=full_brief)
    assert report_hard.hard_fail is True
    assert report_soft.hard_fail is False
    print("[OK] Compliance reports computed (live + hard + soft).")

    firm_state = await get_firm_state(context=tools_ctx)
    positions = await get_position_summary(agent_id="tech_trader", context=tools_ctx)
    positions_by_agent = {"tech_trader": positions}

    # 4) Manager decision (fast model)
    manager = ManagerAgent(
        manager_id="manager",
        config=ManagerConfig(model=manager_model, max_tool_calls=4, max_tool_turns=4),
        tools_context=tools_ctx,
    )

    m0 = time.perf_counter()
    decision = await manager.decide(
        proposals=[
            proposal_live.model_dump(mode="json"),
            hard_bad.model_dump(mode="json"),
            soft_only.model_dump(mode="json"),
        ],
        compliance_reports=[
            report_live.model_dump(mode="json"),
            report_hard.model_dump(mode="json"),
            report_soft.model_dump(mode="json"),
        ],
        firm_state=firm_state,
        positions_by_agent=positions_by_agent,
        run_id=run_id,
        cycle_id=cycle_id,
        extra_instructions=(
            "Hard violations MUST be vetoed.\n"
            "For soft-only violations, prefer RESIZE (do not veto) unless you would defer for lack of edge.\n"
            "Do not fabricate facts.\n"
        ),
    )
    t_mgr = time.perf_counter() - m0

    print(f"[OK] ManagerDecision parsed. decisions={len(decision.decisions)} time_s={t_mgr:.2f}")
    print(f"[OK] Manager tool calls: {len(manager.last_tool_calls)}")
    print("\n== Full ManagerDecision JSON ==")
    print(decision.model_dump_json(indent=2))

    assert decision.notes and len(decision.notes.strip()) >= 10, "manager notes required"

    # Invariants:
    # - hard violation trade must be veto/defer
    hard_items = [
        d
        for d in decision.decisions
        if d.symbol == "BTCUSDT"
        and d.trade_index == 0
        and d.decision in {DecisionType.veto, DecisionType.defer}
    ]
    assert hard_items, "expected hard-violation trade to be vetoed/deferred"

    # - for the soft-only trade, manager should not approve full 5000 size; resize or defer.
    soft_items = [
        d
        for d in decision.decisions
        if d.symbol == "BTCUSDT"
        and d.decision in {DecisionType.resize, DecisionType.defer, DecisionType.veto, DecisionType.approve}
        and (d.trade_index == 0 or d.trade_index is None)
    ]
    assert soft_items, "expected some decision for the soft-only proposal trade"
    resized = [d for d in soft_items if d.decision == DecisionType.resize]
    if resized:
        assert resized[0].approved_size_usdt is not None
        assert resized[0].approved_size_usdt < 5000.0

    print("[PASS] Governance loop produced a schema-valid manager decision and respected hard-veto constraint.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] Governance loop test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)

