"""Sanity test for MongoDB layer and audit/LLM logging.

Run: python tests/test_mongo.py
Requires a local MongoDB reachable via MONGODB_URI or MONGODB_URL.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from bson import ObjectId

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG, LLM_CALLS, MARKET_SNAPSHOTS  # noqa: E402


async def main() -> None:
    mgr = MongoManager(db_name="investment_test")
    try:
        await mgr.connect()
    except Exception as e:
        print(f"[FAIL] Could not connect to MongoDB: {e}")
        raise

    await mgr.ensure_indexes()

    # Insert a market snapshot
    snap_id = await mgr.insert_one(
        MARKET_SNAPSHOTS,
        {
            "run_id": "test_run",
            "timestamp": datetime.now(timezone.utc),
            "symbols": ["BTCUSDT"],
            "per_symbol": {"BTCUSDT": {"last": 100.0}},
        },
    )
    snap = await mgr.find_one(MARKET_SNAPSHOTS, {"_id": ObjectId(snap_id)})
    if not snap:
        raise RuntimeError("Inserted market_snapshot not found")
    print(f"[OK] market_snapshot inserted id={snap_id}")

    # Log an LLM call with rich metadata
    llm_id = await mgr.log_llm_call(
        provider="openrouter",
        model="deepseek/deepseek-chat",
        messages=[{"role": "system", "content": "hi"}],
        response={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        run_id="test_run",
        agent_id="trader_test",
        trace_id="trace_1",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        timing={"latency_s": 0.2},
        request_params={"temperature": 0.1},
        tool_calls=[],
        extra={"note": "unit smoke"},
    )
    llm_doc = await mgr.find_one(LLM_CALLS, {"_id": ObjectId(llm_id)})
    if not llm_doc:
        raise RuntimeError("Inserted llm_call not found")
    print(f"[OK] llm_call inserted id={llm_id}")

    # Audit log should have a llm_call mirror event
    audit_doc = await mgr.collection(AUDIT_LOG).find_one({"event_type": "llm_call", "metadata.model": "deepseek/deepseek-chat"})
    if not audit_doc:
        raise RuntimeError("Expected llm_call event in audit_log")
    print("[OK] audit_log mirror event found")

    await mgr.close()
    print("[PASS] Mongo layer sanity checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)
