"""Phase 9.2 weekly rebalance (no-network; Mongo-only).

Run:
  python tests/test_rebalance_budgets.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AGENT_STATES  # noqa: E402
from src.portfolio.allocation import RebalancePolicy, apply_rebalanced_budgets  # noqa: E402


async def main() -> None:
    print("== Phase 9.2 weekly rebalance test ==")
    mongo = MongoManager()
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    cfg = load_config()
    baseline = float(cfg.risk.agent_budget_notional_usd)
    policy = RebalancePolicy(baseline_budget_usdt=baseline, min_budget_mult=0.5, max_budget_mult=2.0, max_weekly_change_pct=0.25)

    run_id = "run_rebalance_test"
    a1, a2, a3 = "agent_a", "agent_b", "agent_c"

    # Seed current budgets.
    await mongo.collection(AGENT_STATES).update_one({"agent_id": a1}, {"$set": {"agent_id": a1, "role": "test", "budget_usdt": baseline}}, upsert=True)
    await mongo.collection(AGENT_STATES).update_one({"agent_id": a2}, {"$set": {"agent_id": a2, "role": "test", "budget_usdt": baseline}}, upsert=True)
    await mongo.collection(AGENT_STATES).update_one({"agent_id": a3}, {"$set": {"agent_id": a3, "role": "test", "budget_usdt": baseline}}, upsert=True)

    trust_scores = {a1: 90.0, a2: 50.0, a3: 10.0}
    res = await apply_rebalanced_budgets(mongo=mongo, run_id=run_id, trust_scores=trust_scores, policy=policy, cfg=cfg)
    print("[OK] apply_rebalanced_budgets total_before/after:", res.get("total_before"), res.get("total_after"))

    d1 = await mongo.collection(AGENT_STATES).find_one({"agent_id": a1})
    d2 = await mongo.collection(AGENT_STATES).find_one({"agent_id": a2})
    d3 = await mongo.collection(AGENT_STATES).find_one({"agent_id": a3})
    assert d1 and d2 and d3
    b1 = float(d1.get("budget_usdt") or 0)
    b2 = float(d2.get("budget_usdt") or 0)
    b3 = float(d3.get("budget_usdt") or 0)
    print("[OK] budgets:", {a1: b1, a2: b2, a3: b3})

    # Expect ordering by trust.
    assert b1 >= b2 >= b3, "expected higher trust -> higher or equal budget"

    # Enforce weekly change cap (~25%) from baseline.
    assert b1 <= baseline * 1.25 + 1e-6
    assert b3 >= baseline * 0.75 - 1e-6

    # Totals preserved (allow tiny float drift).
    assert abs((b1 + b2 + b3) - (3 * baseline)) < 1e-3

    print("[PASS] Weekly rebalance checks passed.")
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())

