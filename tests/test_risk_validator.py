"""Authentic integration test for risk validator.

Run:
  python tests/test_risk_validator.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys
  - TAVILY_API_KEY
  - OPENROUTER_API_KEY
  - LLM_MODEL_TRADER_1 (or uses default configured in src/config.py)
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.schemas import (  # noqa: E402
    OrderType,
    Side,
    TradeAction,
    TradeIdea,
    TradeProposal,
)
from src.agents.technical_trader import TechnicalTrader, TechnicalTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402
from src.risk.validator import validate_proposal  # noqa: E402


async def main() -> None:
    print("== Risk validator integration test ==")
    cfg = load_config()
    model = os.getenv("LLM_MODEL_TRADER_1") or "deepseek/deepseek-chat"
    run_id = "test_risk_validator_run"
    print(f"[INFO] Using model: {model}")

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

    trader = TechnicalTrader(
        config=TechnicalTraderConfig(model=model, max_tool_calls=6, max_tool_turns=6),
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
    elapsed = time.perf_counter() - start

    print(f"[OK] Trader returned proposal. trades={len(proposal.trades)} time_s={elapsed:.2f}")
    if trader.last_tool_calls:
        print(f"[OK] Tool calls made: {len(trader.last_tool_calls)}")
        for i, tc in enumerate(trader.last_tool_calls, 1):
            print(f"  {i}) {tc['name']} args={tc['args']}")

    report = await validate_proposal(
        proposal,
        tools_context=tools_ctx,
        firm_state=None,
        market_brief=None,
        allow_live_fetch=True,
    )

    print("[OK] ComplianceReport produced.")
    print(report.model_dump_json(indent=2))

    # Basic invariants
    assert report.agent_id == proposal.agent_id
    assert report.hard_fail == (len(report.hard_violations) > 0)
    assert report.passed is (not report.hard_fail)

    # Deterministic hard-fail example using the same validator inputs
    bad = TradeProposal(
        agent_id="tech_trader",
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
                rationale="intentional hard violation",
            )
        ],
    )
    bad_report = await validate_proposal(bad, tools_context=tools_ctx, market_brief=full_brief)
    assert bad_report.hard_fail is True
    assert any(v.rule_id == "firm.max_leverage_per_position" for v in bad_report.hard_violations)
    print("[OK] Hard-fail leverage check verified.")

    print("[PASS] Risk validator checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] Risk validator test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)

