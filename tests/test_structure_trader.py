"""Authentic integration test for StructureTrader agent (Phase 8.3).

Run:
  python tests/test_structure_trader.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys (for snapshot ingest)
  - OPENROUTER_API_KEY
  - LLM_MODEL_TRADER_4 (or falls back to LLM_MODEL_TRADER_1 / deepseek)
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.structure_trader import StructureTrader, StructureTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


async def main() -> None:
    print("== StructureTrader integration test ==")
    cfg = load_config()
    model = (
        os.getenv("LLM_MODEL_TRADER_4")
        or os.getenv("LLM_MODEL_TRADER_1")
        or "deepseek/deepseek-chat"
    )
    run_id = "test_structure_trader_run"
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

    trader = StructureTrader(
        agent_id="structure_trader_test",
        config=StructureTraderConfig(model=model, max_tool_calls=6, max_tool_turns=6),
        tools_context=tools_ctx,
    )

    extra = (
        "You are evaluating funding/OI and liquidity-based opportunities for BTCUSDT and ETHUSDT.\n"
        "You MUST call get_funding_oi_history and get_orderbook_top at least once.\n"
        "Then decide: propose a trade or no-trade.\n"
        "Return ONLY the TradeProposal JSON.\n"
    )

    start = time.perf_counter()
    proposal = await trader.decide(
        market_brief={
            "run_id": run_id,
            "timestamp": full_market_brief.get("timestamp"),
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "note": "partial brief; use structure tools for funding/OI history + top-of-book",
        },
        extra_instructions=extra,
    )
    elapsed = time.perf_counter() - start

    tcalls = trader.last_tool_calls
    print(f"[OK] Tool calls made: {len(tcalls)} (limit=6)")
    for i, tc in enumerate(tcalls, 1):
        print(f"  {i}) {tc['name']} args={tc['args']}")

    assert len(tcalls) <= 6, "tool call cap exceeded"
    assert any(c.get("name") == "get_funding_oi_history" for c in tcalls), "expected get_funding_oi_history call"
    assert any(c.get("name") == "get_orderbook_top" for c in tcalls), "expected get_orderbook_top call"

    # Verify tool_call audit events were written.
    n = await mongo.collection(AUDIT_LOG).count_documents({"run_id": run_id, "event_type": "tool_call"})
    print(f"[OK] tool_call audit events: {n}")
    assert n >= 2, "expected at least 2 tool_call audit events"

    print(f"[OK] Proposal parsed. trades={len(proposal.trades)} time_s={elapsed:.2f}")
    print("\n== Full TradeProposal JSON ==")
    print(proposal.model_dump_json(indent=2))
    print("[PASS] StructureTrader produced schema-valid output and used deterministic structure tools.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] StructureTrader test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)

