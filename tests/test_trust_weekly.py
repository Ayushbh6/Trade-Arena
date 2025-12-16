"""Phase 9.1 weekly trust scoring (no-network; Mongo-only).

Run:
  python tests/test_trust_weekly.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.mongo import MongoManager, utc_now  # noqa: E402
from src.data.schemas import AGENT_STATES, AUDIT_LOG, PNL_REPORTS  # noqa: E402
from src.portfolio.trust import update_weekly_trust_scores  # noqa: E402


def _dt(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


async def main() -> None:
    print("== Phase 9.1 weekly trust scoring test ==")
    mongo = MongoManager()
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    run_id = "run_trust_weekly_test"
    agent_good = "tech_trader_1"
    agent_bad = "structure_trader_1"

    # Previous week window: Mon->Mon UTC (synthetic timestamps, real Mongo).
    start = _dt(2025, 1, 6)  # Monday
    end = start + timedelta(days=7)

    # Seed minimal agent_states budgets (trust will be computed).
    await mongo.collection(AGENT_STATES).update_one(
        {"agent_id": agent_good},
        {"$set": {"agent_id": agent_good, "role": "technical", "budget_usdt": 1000.0}},
        upsert=True,
    )
    await mongo.collection(AGENT_STATES).update_one(
        {"agent_id": agent_bad},
        {"$set": {"agent_id": agent_bad, "role": "structure", "budget_usdt": 1000.0}},
        upsert=True,
    )

    # Seed pnl_reports equity curve for both agents inside the week.
    reports = [
        (start + timedelta(hours=1), 1000.0, 1000.0),
        (start + timedelta(days=2), 1100.0, 980.0),
        (start + timedelta(days=6, hours=23), 1200.0, 990.0),
    ]
    for ts, eq_good, eq_bad in reports:
        await mongo.collection(PNL_REPORTS).insert_one(
            {
                "run_id": run_id,
                "cycle_id": f"cycle_{int(ts.timestamp())}",
                "timestamp": ts,
                "firm_metrics": {},
                "agent_metrics": {
                    agent_good: {"total_equity": eq_good},
                    agent_bad: {"total_equity": eq_bad},
                },
            }
        )

    # Seed one risk report event containing violations for agent_bad (even if manager vetoes, it counts).
    await mongo.collection(AUDIT_LOG).insert_one(
        {
            "run_id": run_id,
            "agent_id": "risk_engine",
            "timestamp": start + timedelta(days=3),
            "event_type": "risk_reports_ready",
            "payload": {
                "reports": [
                    {
                        "agent_id": agent_bad,
                        "hard_violations": [{"rule_id": "hard_test", "severity": "hard", "message": "hard fail"}],
                        "soft_violations": [
                            {"rule_id": "soft_1", "severity": "soft", "message": "soft warn"},
                            {"rule_id": "soft_2", "severity": "soft", "message": "soft warn"},
                        ],
                        "resize_suggestions": [],
                        "hard_fail": True,
                        "passed": False,
                        "timestamp": utc_now(),
                    }
                ]
            },
        }
    )

    res = await update_weekly_trust_scores(mongo=mongo, run_id=run_id, start=start, end=end)
    print("[OK] update_weekly_trust_scores:", res.get("updated"))

    good = await mongo.collection(AGENT_STATES).find_one({"agent_id": agent_good})
    bad = await mongo.collection(AGENT_STATES).find_one({"agent_id": agent_bad})
    assert good and bad, "expected both agent_states docs"
    assert "trust_score" in good and "trust_score" in bad, "trust_score not persisted"
    print("[OK] trust_score good=", good.get("trust_score"), "bad=", bad.get("trust_score"))

    assert float(good["trust_score"]) > float(bad["trust_score"]), "expected good trust_score > bad trust_score"
    assert float(bad["trust_score"]) < 50.0, "expected violations to penalize trust below neutral baseline"
    print("[PASS] Weekly trust scoring checks passed.")

    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
