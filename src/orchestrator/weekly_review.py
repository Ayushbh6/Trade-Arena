"""Weekly review runner (Phase 9).

Does two deterministic steps for a given run_id:
1) Compute/update weekly trust scores from Mongo facts (pnl_reports + audit_log).
2) Apply weekly budget rebalance based on trust scores (agent_states budget_usdt).

Designed to be triggered either:
- via CLI (`run.py --weekly-review`)
- via an automated scheduler job (APScheduler in main_loop.py)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from src.config import AppConfig, load_config
from src.data.audit import AuditContext, AuditManager
from src.data.locks import acquire_lock, release_lock
from src.data.mongo import MongoManager, jsonify, utc_now
from src.portfolio.allocation import RebalancePolicy, apply_rebalanced_budgets
from src.portfolio.trust import previous_week_bounds_utc, update_weekly_trust_scores


@dataclass(frozen=True)
class WeeklyReviewConfig:
    lock_ttl_seconds: int = 900
    min_budget_mult: float = 0.5
    max_budget_mult: float = 2.0
    max_weekly_change_pct: float = 0.25


async def run_weekly_review(
    *,
    mongo: MongoManager,
    run_id: str,
    cfg: Optional[AppConfig] = None,
    review_cfg: Optional[WeeklyReviewConfig] = None,
    reference_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    cfg = cfg or load_config()
    review_cfg = review_cfg or WeeklyReviewConfig()

    await mongo.connect()
    await mongo.ensure_indexes()

    start, end = previous_week_bounds_utc(reference_time or utc_now())
    owner = os.getenv("HOSTNAME") or "weekly_review"

    lock = await acquire_lock(mongo=mongo, lock_name=f"weekly_review:{run_id}:{end.date().isoformat()}", owner=owner, ttl_seconds=review_cfg.lock_ttl_seconds)
    if not lock.acquired:
        return jsonify({"ok": False, "error": "lock_not_acquired", "lock": lock.__dict__})

    audit = AuditManager(mongo)
    ctx = AuditContext(run_id=run_id, agent_id="weekly_review")
    try:
        await audit.log(
            "weekly_review_start",
            {"run_id": run_id, "window": {"start": start, "end": end}},
            ctx=ctx,
        )

        trust_res = await update_weekly_trust_scores(mongo=mongo, run_id=run_id, start=start, end=end)
        await audit.log("weekly_trust_updated", {"result": trust_res}, ctx=ctx)

        trust_scores = {aid: float(info["trust_score"]) for aid, info in (trust_res.get("updated") or {}).items()}
        policy = RebalancePolicy(
            baseline_budget_usdt=float(cfg.risk.agent_budget_notional_usd),
            min_budget_mult=float(review_cfg.min_budget_mult),
            max_budget_mult=float(review_cfg.max_budget_mult),
            max_weekly_change_pct=float(review_cfg.max_weekly_change_pct),
        )
        rebalance_res = await apply_rebalanced_budgets(mongo=mongo, run_id=run_id, trust_scores=trust_scores, policy=policy, cfg=cfg)
        await audit.log("weekly_rebalance_applied", {"result": rebalance_res}, ctx=ctx)

        await audit.log("weekly_review_end", {"ok": True}, ctx=ctx)
        return jsonify({"ok": True, "window": {"start": start, "end": end}, "trust": trust_res, "rebalance": rebalance_res})
    finally:
        await release_lock(mongo=mongo, lock_name=f"weekly_review:{run_id}:{end.date().isoformat()}", owner=owner)


__all__ = ["WeeklyReviewConfig", "run_weekly_review"]

