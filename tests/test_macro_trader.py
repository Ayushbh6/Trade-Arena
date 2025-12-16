"""Authentic integration test for MacroTrader agent (Phase 8.1).

Run:
  python tests/test_macro_trader.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys (for snapshot ingest)
  - TAVILY_API_KEY
  - OPENROUTER_API_KEY
  - LLM_MODEL_TRADER_3 (or falls back to LLM_MODEL_TRADER_1 / deepseek)
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.macro_trader import MacroTrader, MacroTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import NEWS_EVENTS  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


async def main() -> None:
    print("== MacroTrader integration test ==")
    cfg = load_config()
    model = (
        os.getenv("LLM_MODEL_TRADER_3")
        or os.getenv("LLM_MODEL_TRADER_1")
        or "deepseek/deepseek-chat"
    )
    run_id = "test_macro_trader_run"
    print(f"[INFO] Using model: {model}")

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id=run_id)
    snapshot = await ingestor.fetch_and_store_snapshot()
    builder = MarketStateBuilder()
    full_market_brief = builder.build_market_brief(snapshot)
    print("[OK] Market snapshot stored for tools.")

    tools_ctx = ToolContext(
        mongo=mongo,
        config=cfg,
        market_state_builder=builder,
        news_connector=None,
        run_id=run_id,
    )

    trader = MacroTrader(
        agent_id="macro_trader_test",
        config=MacroTraderConfig(model=model, max_tool_calls=6, max_tool_turns=6),
        tools_context=tools_ctx,
    )

    extra = (
        "You are evaluating macro/narrative-driven trades for BTCUSDT and ETHUSDT.\n"
        "You MUST call tavily_search at least once to gather fresh macro/news context.\n"
        "Then decide: propose a trade or no-trade.\n"
        "Return ONLY the TradeProposal JSON.\n"
    )

    start = time.perf_counter()
    proposal = await trader.decide(
        market_brief={
            "run_id": run_id,
            "timestamp": full_market_brief.get("timestamp"),
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "note": "partial brief; use tools for details + news",
        },
        extra_instructions=extra,
    )
    elapsed = time.perf_counter() - start

    tcalls = trader.last_tool_calls
    print(f"[OK] Tool calls made: {len(tcalls)} (limit=6)")
    for i, tc in enumerate(tcalls, 1):
        print(f"  {i}) {tc['name']} args={tc['args']}")

    assert len(tcalls) <= 6, "tool call cap exceeded"
    assert any(c.get("name") == "tavily_search" for c in tcalls), "expected at least one tavily_search call"

    # Verify Tavily tool persisted results into news_events for this run_id.
    col = mongo.collection(NEWS_EVENTS)
    persisted = await col.count_documents({"run_id": run_id, "source": "tavily"})
    print(f"[OK] Persisted news_events for run_id={run_id}: {persisted}")
    assert persisted > 0, "expected tavily_search results to be persisted to news_events"

    print(f"[OK] Proposal parsed. trades={len(proposal.trades)} time_s={elapsed:.2f}")
    print("\n== Full TradeProposal JSON ==")
    print(proposal.model_dump_json(indent=2))
    print("[PASS] MacroTrader produced schema-valid output and persisted Tavily news.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] MacroTrader test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)

