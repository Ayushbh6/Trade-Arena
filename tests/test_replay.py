"""Phase 10 replay integration test (authentic; network for original run only).

Run:
  python tests/test_replay.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys (to create the source run snapshot)
  - TAVILY_API_KEY (MacroTrader may call tavily_search in the source run)
  - OPENROUTER_API_KEY (agents run via OpenRouter)
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.mongo import MongoManager, utc_now  # noqa: E402
from src.data.schemas import AUDIT_LOG, MARKET_SNAPSHOTS, TRADE_PROPOSALS  # noqa: E402
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402
from src.orchestrator.replay import run_replay  # noqa: E402


async def main() -> None:
    print("== Phase 10 replay integration test ==")

    if not os.getenv("OPENROUTER_API_KEY"):
        print("[SKIP] OPENROUTER_API_KEY not set.")
        return

    cfg = load_config()
    mongo = MongoManager(db_name=os.getenv("MONGODB_DB", "investment_test"))
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    source_run_id = "test_replay_source_run"
    replay_run_id = "test_replay_replay_run"

    orch = Orchestrator(
        mongo=mongo,
        config=cfg,
        orchestrator_config=OrchestratorConfig(
            execute_testnet=False,  # do not place any orders
            memory_compression=False,
        ),
    )

    # 1) Create a single source cycle (authentic snapshot + LLM proposals/decision)
    await orch.run_cycle(run_id=source_run_id, cycle_id="cycle_replay_001")
    print("[OK] Source cycle executed.")

    # Find the cycle_start timestamp for the window.
    cs = await mongo.collection(AUDIT_LOG).find_one(
        {"run_id": source_run_id, "event_type": "cycle_start", "payload.cycle_id": "cycle_replay_001"}
    )
    assert cs and cs.get("timestamp"), "expected cycle_start audit event"
    start = cs["timestamp"] - timedelta(minutes=5)
    end = cs["timestamp"] + timedelta(minutes=30)

    # 2) Replay the same window using stored snapshot only (no new snapshots should be written)
    report = await run_replay(
        orchestrator=orch,
        source_run_id=source_run_id,
        replay_run_id=replay_run_id,
        start=start,
        end=end,
        model_overrides=None,
    )

    assert report.get("source_run_id") == source_run_id
    assert report.get("replay_run_id") == replay_run_id
    cycles = report.get("cycles") or []
    assert len(cycles) >= 1, "expected at least one replayed cycle"
    print("[OK] replay cycles:", len(cycles))

    # 3) Replay should not create market_snapshots under the replay run_id.
    snap_count = await mongo.collection(MARKET_SNAPSHOTS).count_documents({"run_id": replay_run_id})
    assert snap_count == 0, "replay must not write new market_snapshots"
    print("[OK] no replay snapshots created.")

    # 4) Replay should persist proposals under the replay run_id.
    prop_count = await mongo.collection(TRADE_PROPOSALS).count_documents({"run_id": replay_run_id})
    assert prop_count > 0, "expected replay trade_proposals to be persisted"
    print("[OK] replay proposals persisted:", prop_count)

    # 5) Report contains schema-level diffs (may be empty due to non-determinism).
    first = cycles[0]
    assert "proposals_diff" in first and "manager_decision_diff" in first
    print("[PASS] Replay integration checks passed.")

    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())

