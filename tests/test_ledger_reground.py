"""Integration sanity test for Phase 7 ledger re-grounding.

Run:
  python tests/test_ledger_reground.py

Requires:
  - Local MongoDB reachable via MONGODB_URI / MONGODB_URL

No OpenRouter key required.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.reground import rebuild_ledger_facts_from_mongo  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AGENT_STATES, MANAGER_DECISIONS, POSITIONS, TRADE_PROPOSALS  # noqa: E402


async def main() -> None:
    print("== Phase 7 ledger re-grounding integration test ==")
    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    now = datetime.now(timezone.utc)
    run_id = "test_run_ledger_reground"
    agent_id = "tech_trader_ledger_reground_test"
    cycle_id = "cycle_test_ledger_1"

    # Cleanup for repeatability.
    await mongo.collection(AGENT_STATES).delete_many({"agent_id": agent_id})
    await mongo.collection(POSITIONS).delete_many({"run_id": run_id, "agent_owner": agent_id})
    await mongo.collection(TRADE_PROPOSALS).delete_many({"run_id": run_id, "agent_id": agent_id})
    await mongo.collection(MANAGER_DECISIONS).delete_many({"run_id": run_id, "cycle_id": cycle_id})

    await mongo.collection(AGENT_STATES).insert_one(
        {
            "agent_id": agent_id,
            "role": "technical",
            "budget_usdt": 12345.0,
            "trust_score": 0.7,
            "timestamp": now,
        }
    )

    await mongo.collection(POSITIONS).insert_one(
        {
            "run_id": run_id,
            "cycle_id": cycle_id,
            "timestamp": now,
            "symbol": "BTCUSDT",
            "qty": 0.01,
            "avg_entry_price": 50000.0,
            "mark_price": 50500.0,
            "unrealized_pnl": 5.0,
            "leverage": 2.0,
            "agent_owner": agent_id,
        }
    )

    proposal_id = await mongo.insert_one(
        TRADE_PROPOSALS,
        {
            "run_id": run_id,
            "cycle_id": cycle_id,
            "timestamp": now,
            "agent_id": agent_id,
            "trades": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "action": "open",
                    "size_usdt": 1000.0,
                    "leverage": 2.0,
                    "order_type": "market",
                    "limit_price": None,
                    "stop_loss": 49000.0,
                    "take_profit": 52000.0,
                    "time_horizon": "1h",
                    "confidence": 0.6,
                    "rationale": "test proposal",
                    "invalidation": "test",
                    "tags": [],
                }
            ],
            "notes": "test",
        },
    )
    print(f"[OK] Inserted proposal id={proposal_id}")

    decision_id = await mongo.insert_one(
        MANAGER_DECISIONS,
        {
            "run_id": run_id,
            "cycle_id": cycle_id,
            "timestamp": now,
            "manager_id": "manager_test",
            "decisions": [
                {
                    "agent_id": agent_id,
                    "trade_index": 0,
                    "symbol": "BTCUSDT",
                    "decision": "approve",
                    "approved_size_usdt": None,
                    "approved_leverage": None,
                    "notes": "ok",
                }
            ],
            "notes": "test decision",
            "firm_notes": None,
        },
    )
    print(f"[OK] Inserted decision id={decision_id}")

    facts = await rebuild_ledger_facts_from_mongo(mongo=mongo, run_id=run_id, agent_id=agent_id, max_outcomes=5)
    assert facts.agent_budget_usdt == 12345.0
    assert facts.trust_score == 0.7
    assert facts.positions and facts.positions[0].symbol == "BTCUSDT"
    assert facts.recent_outcomes and facts.recent_outcomes[0].get("cycle_id") == cycle_id

    print("[PASS] Ledger facts re-grounding checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)

