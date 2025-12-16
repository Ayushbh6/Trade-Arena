"""Weekly capital allocation (Phase 9.2).

Allocator operates on agent-level USDT notional caps ("budget_usdt") stored in
`agent_states`. Budgets are rebalanced weekly based on trust scores.

Design goals:
- Deterministic and auditable (no LLM involvement required).
- Bounded moves (avoid whipsaw): max +/- change per week.
- Floors/ceilings relative to baseline budget (from config).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.config import AppConfig, load_config
from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import AGENT_STATES


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class RebalancePolicy:
    baseline_budget_usdt: float
    min_budget_mult: float = 0.5
    max_budget_mult: float = 2.0
    max_weekly_change_pct: float = 0.25
    trust_floor: float = 1.0  # trust weights never below this

    @property
    def min_budget_usdt(self) -> float:
        return float(self.baseline_budget_usdt) * float(self.min_budget_mult)

    @property
    def max_budget_usdt(self) -> float:
        return float(self.baseline_budget_usdt) * float(self.max_budget_mult)


def _waterfill_allocate(
    *,
    agent_ids: List[str],
    weights: Dict[str, float],
    total: float,
    mins: Dict[str, float],
    maxs: Dict[str, float],
    max_iters: int = 50,
) -> Dict[str, float]:
    """Allocate `total` across agents with per-agent min/max and proportional weights."""
    remaining = float(total)
    alloc: Dict[str, float] = {a: 0.0 for a in agent_ids}
    active = set(agent_ids)

    # Seed mins first.
    for a in agent_ids:
        mn = float(mins[a])
        mx = float(maxs[a])
        mn = _clamp(mn, 0.0, mx)
        alloc[a] = mn
        remaining -= mn
        if abs(mx - mn) < 1e-9:
            active.discard(a)

    if remaining < -1e-6:
        # Infeasible (mins exceed total): scale mins down proportionally.
        scale = float(total) / max(1e-9, float(total) - remaining)
        for a in agent_ids:
            alloc[a] *= scale
        return alloc

    for _ in range(max_iters):
        if remaining <= 1e-9 or not active:
            break
        wsum = sum(max(0.0, float(weights.get(a, 0.0))) for a in active)
        if wsum <= 1e-9:
            # Equal split across remaining slack.
            wsum = float(len(active))
            for a in list(active):
                weights[a] = 1.0

        progressed = False
        for a in list(active):
            w = max(0.0, float(weights.get(a, 0.0)))
            share = remaining * (w / wsum) if wsum > 0 else 0.0
            cap = float(maxs[a]) - alloc[a]
            give = min(cap, share)
            if give > 0:
                alloc[a] += give
                remaining -= give
                progressed = True
            if (float(maxs[a]) - alloc[a]) <= 1e-9:
                active.discard(a)
        if not progressed:
            break

    # Final tiny normalization for rounding drift: distribute remainder to any agent with slack.
    if abs(remaining) > 1e-6:
        slack = [a for a in agent_ids if (float(maxs[a]) - alloc[a]) > 1e-9]
        if slack:
            per = remaining / float(len(slack))
            for a in slack:
                alloc[a] = _clamp(alloc[a] + per, float(mins[a]), float(maxs[a]))
    return alloc


def compute_rebalanced_budgets(
    *,
    current_budgets: Dict[str, float],
    trust_scores: Dict[str, float],
    policy: RebalancePolicy,
) -> Dict[str, float]:
    agent_ids = sorted(current_budgets.keys())
    total = sum(float(current_budgets[a]) for a in agent_ids)

    # Effective bounds: baseline min/max AND max weekly change.
    mins: Dict[str, float] = {}
    maxs: Dict[str, float] = {}
    weights: Dict[str, float] = {}
    for a in agent_ids:
        cur = float(current_budgets[a])
        change_min = cur * (1.0 - float(policy.max_weekly_change_pct))
        change_max = cur * (1.0 + float(policy.max_weekly_change_pct))
        mins[a] = max(float(policy.min_budget_usdt), change_min)
        maxs[a] = min(float(policy.max_budget_usdt), change_max)

        ts = float(trust_scores.get(a, 50.0))
        weights[a] = max(float(policy.trust_floor), ts)

    # If infeasible, relax change bounds first (keep baseline min/max).
    if sum(mins.values()) > total + 1e-6 or sum(maxs.values()) < total - 1e-6:
        mins = {a: float(policy.min_budget_usdt) for a in agent_ids}
        maxs = {a: float(policy.max_budget_usdt) for a in agent_ids}

    alloc = _waterfill_allocate(agent_ids=agent_ids, weights=weights, total=total, mins=mins, maxs=maxs)
    return {a: float(round(alloc[a], 8)) for a in agent_ids}


async def apply_rebalanced_budgets(
    *,
    mongo: MongoManager,
    run_id: str,
    trust_scores: Dict[str, float],
    policy: Optional[RebalancePolicy] = None,
    cfg: Optional[AppConfig] = None,
) -> Dict[str, Any]:
    await mongo.connect()
    cfg = cfg or load_config()
    policy = policy or RebalancePolicy(baseline_budget_usdt=float(cfg.risk.agent_budget_notional_usd))

    # Load existing budgets; default to baseline for any agent in trust_scores.
    agent_ids = sorted(trust_scores.keys())
    docs = await mongo.collection(AGENT_STATES).find({"agent_id": {"$in": agent_ids}}).to_list(length=500)
    budgets: Dict[str, float] = {a: float(policy.baseline_budget_usdt) for a in agent_ids}
    for d in docs or []:
        aid = d.get("agent_id")
        if aid in budgets and d.get("budget_usdt") is not None:
            try:
                budgets[aid] = float(d["budget_usdt"])
            except Exception:
                continue

    new_budgets = compute_rebalanced_budgets(current_budgets=budgets, trust_scores=trust_scores, policy=policy)
    now = utc_now()
    col = mongo.collection(AGENT_STATES)

    updated: Dict[str, Any] = {}
    for aid, nb in new_budgets.items():
        old = float(budgets.get(aid, policy.baseline_budget_usdt))
        await col.update_one(
            {"agent_id": aid},
            {
                "$set": jsonify(
                    {
                        "agent_id": aid,
                        "budget_usdt": float(nb),
                        "budget_prev_usdt": float(old),
                        "budget_updated_at": now,
                        "budget_update_reason": "weekly_trust_rebalance",
                        "last_rebalance_run_id": run_id,
                        "last_rebalance_at": now,
                        "last_trust_score_used": float(trust_scores.get(aid, 50.0)),
                        "rebalance_policy": {
                            "min_budget_mult": policy.min_budget_mult,
                            "max_budget_mult": policy.max_budget_mult,
                            "max_weekly_change_pct": policy.max_weekly_change_pct,
                            "baseline_budget_usdt": policy.baseline_budget_usdt,
                        },
                    }
                )
            },
            upsert=True,
        )
        updated[aid] = {"old": old, "new": float(nb)}

    return jsonify(
        {
            "run_id": run_id,
            "updated_at": now,
            "policy": {
                "baseline_budget_usdt": policy.baseline_budget_usdt,
                "min_budget_mult": policy.min_budget_mult,
                "max_budget_mult": policy.max_budget_mult,
                "max_weekly_change_pct": policy.max_weekly_change_pct,
            },
            "updated": updated,
            "total_before": sum(float(v) for v in budgets.values()),
            "total_after": sum(float(v) for v in new_budgets.values()),
        }
    )


__all__ = ["RebalancePolicy", "compute_rebalanced_budgets", "apply_rebalanced_budgets"]

