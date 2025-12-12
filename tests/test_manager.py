"""Authentic integration test for Manager agent.

Run:
  python tests/test_manager.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys
  - TAVILY_API_KEY
  - OPENROUTER_API_KEY
  - LLM_MODEL_TRADER_1 (or uses default)
  - LLM_MODEL_MANAGER (recommended: moonshotai/kimi-k2-thinking)
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
    print("== Manager integration test ==")
    cfg = load_config()
    trader_model = os.getenv("LLM_MODEL_TRADER_1") or "deepseek/deepseek-chat"
    manager_model = (
        os.getenv("LLM_MODEL_MANAGER_FAST")
        or os.getenv("LLM_MODEL_MANAGER")
        or cfg.models.manager_model_fast
        or cfg.models.manager_model
    )
    run_id = "test_manager_run"
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

    # Trader proposal (real LLM)
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

    start = time.perf_counter()
    proposal = await trader.decide(
        market_brief={
            "timestamp": full_brief.get("timestamp"),
            "symbols": ["BTCUSDT"],
            "note": "partial brief; call tools for details",
        },
        extra_instructions=extra,
    )
    print(f"[OK] Trader proposal parsed. trades={len(proposal.trades)} time_s={time.perf_counter()-start:.2f}")

    # Compliance report for trader output
    report = await validate_proposal(proposal, tools_context=tools_ctx, market_brief=full_brief)
    print("[OK] ComplianceReport for trader proposal computed.")

    # Add an intentionally hard-failing proposal to verify manager veto behavior.
    bad = TradeProposal(
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
        notes="Intentional hard-violation proposal for manager veto test.",
    )
    bad_report = await validate_proposal(bad, tools_context=tools_ctx, market_brief=full_brief)
    assert bad_report.hard_fail is True
    print("[OK] Hard-fail proposal/compliance report ready.")

    firm_state = await get_firm_state(context=tools_ctx)
    positions = await get_position_summary(agent_id="tech_trader", context=tools_ctx)
    positions_by_agent = {"tech_trader": positions}

    manager = ManagerAgent(
        manager_id="manager",
        config=ManagerConfig(model=manager_model, max_tool_calls=4, max_tool_turns=4),
        tools_context=tools_ctx,
    )

    mstart = time.perf_counter()
    decision = await manager.decide(
        proposals=[
            proposal.model_dump(mode="json"),
            bad.model_dump(mode="json"),
        ],
        compliance_reports=[
            report.model_dump(mode="json"),
            bad_report.model_dump(mode="json"),
        ],
        firm_state=firm_state,
        positions_by_agent=positions_by_agent,
        run_id=run_id,
        cycle_id=cycle_id,
        extra_instructions=(
            "Explicit rule reminder: any hard violation MUST be vetoed; soft violations MAY be resized.\n"
            "Do not approve any trade that has a hard violation.\n"
        ),
    )
    melapsed = time.perf_counter() - mstart

    print(f"[OK] ManagerDecision parsed. decisions={len(decision.decisions)} time_s={melapsed:.2f}")
    print(f"[OK] Manager tool calls made: {len(manager.last_tool_calls)} (limit=4)")
    for i, tc in enumerate(manager.last_tool_calls, 1):
        print(f"  {i}) {tc['name']} args={tc['args']}")

    print("\n== Full ManagerDecision JSON ==")
    print(decision.model_dump_json(indent=2))

    assert decision.notes and len(decision.notes.strip()) >= 10, "manager notes required"

    # Must veto hard-failing trade (trade_index=0 in the bad proposal) OR defer it explicitly.
    vetoed = [
        d
        for d in decision.decisions
        if d.symbol == "BTCUSDT"
        and (d.decision in {DecisionType.veto, DecisionType.defer})
        and (d.trade_index == 0 or d.trade_index is None)
    ]
    assert vetoed, "expected manager to veto/defer the hard-failing BTCUSDT trade"

    print("[PASS] Manager produced a schema-valid decision and respected hard-veto constraint.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] Manager test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)
