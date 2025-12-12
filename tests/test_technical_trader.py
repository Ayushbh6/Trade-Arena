"""Authentic integration test for TechnicalTrader agent.

Run:
  python tests/test_technical_trader.py

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

from src.agents.technical_trader import TechnicalTrader, TechnicalTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


async def main() -> None:
    print("== TechnicalTrader integration test ==")
    cfg = load_config()
    model = os.getenv("LLM_MODEL_TRADER_1") or "deepseek/deepseek-chat"
    run_id = "test_technical_trader_run"
    print(f"[INFO] Using model: {model}")

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id=run_id)
    snapshot = await ingestor.fetch_and_store_snapshot()
    builder = MarketStateBuilder()
    _full_market_brief = builder.build_market_brief(snapshot)
    print("[OK] Market snapshot stored for tools.")

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
        "The Market Brief you received is intentionally PARTIAL and may be stale.\n"
        "Constraints:\n"
        "- Do NOT fabricate any facts; use tools to fetch missing/uncertain info.\n"
        "- Use tools as needed, but do not exceed the tool call budget.\n"
        "- Then return ONLY the TradeProposal JSON.\n"
    )

    start = time.perf_counter()
    proposal = await trader.decide(
        market_brief={
            "timestamp": _full_market_brief.get("timestamp"),
            "symbols": ["BTCUSDT"],
            "note": "partial brief; call tools for details",
        },
        extra_instructions=extra,
    )
    elapsed = time.perf_counter() - start

    tcalls = trader.last_tool_calls
    print(f"[OK] Tool calls made: {len(tcalls)} (limit=6)")
    for i, tc in enumerate(tcalls, 1):
        print(f"  {i}) {tc['name']} args={tc['args']}")

    print(f"[OK] Proposal parsed. trades={len(proposal.trades)} time_s={elapsed:.2f}")
    if proposal.trades:
        print("[INFO] First trade snippet:", proposal.trades[0].model_dump(mode="json"))
    print("\n== Full TradeProposal JSON ==")
    print(proposal.model_dump_json(indent=2))

    assert len(tcalls) <= 6, "tool call cap exceeded"
    print("[PASS] TechnicalTrader produced a schema-valid proposal within tool budget.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] TechnicalTrader test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)
