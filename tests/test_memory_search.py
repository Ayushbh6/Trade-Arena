"""Integration sanity test for semantic memory search.

Run:
  python tests/test_memory_search.py

Requires:
  - Local MongoDB reachable via MONGODB_URI / MONGODB_URL
  - OPENROUTER_API_KEY in env/.env (for embeddings)

This test writes a few real memory docs to investment_test DB, then runs
query_memory to validate semantic retrieval. No fakes/mocks.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.tools import ToolContext, build_tool_dispatch  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG  # noqa: E402


async def main() -> None:
    print("== Semantic memory search integration test ==")
    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    now = datetime.now(timezone.utc)
    # Use a dedicated test agent_id to avoid colliding with real history.
    agent_id = "tech_trader_memory_test"
    run_id = "test_memory_run"

    # Insert a couple of audit events to search over.
    await mongo.insert_one(
        AUDIT_LOG,
        {
            "timestamp": now,
            "event_type": "trade_proposal",
            "agent_id": agent_id,
            "run_id": run_id,
            "payload": {
                "symbol": "BTCUSDT",
                "side": "long",
                "action": "open",
                "summary": "Breakout retest on 15m with rising RSI; looking for continuation.",
                "test_marker": run_id,
            },
        },
    )
    await mongo.insert_one(
        AUDIT_LOG,
        {
            "timestamp": now,
            "event_type": "trade_proposal",
            "agent_id": agent_id,
            "run_id": run_id,
            "payload": {
                "symbol": "ETHUSDT",
                "side": "short",
                "action": "open",
                "summary": "Fade spike into resistance; trend down on 1h.",
                "test_marker": run_id,
            },
        },
    )
    print("[OK] Inserted memory docs.")

    ctx = ToolContext(mongo=mongo, run_id=run_id)
    dispatch = build_tool_dispatch(ctx)

    res = await dispatch["query_memory"](
        agent_id=agent_id,
        query="BTC breakout long retest",
        lookback_days=3,
        max_items=5,
        filters={"symbols": ["BTCUSDT"], "event_types": ["trade_proposal"]},
    )
    matches = res.get("matches") or []
    print(f"[OK] query_memory returned {len(matches)} matches.")
    for m in matches:
        print(f"- score={m.get('score'):.3f} source={m.get('source')} symbol={m.get('symbol')}")
        print("  summary:", (m.get("summary") or "")[:160])

    assert matches, "expected at least one semantic match"
    assert matches[0].get("symbol") in ("BTCUSDT", None)

    print("[PASS] Semantic memory search checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)
