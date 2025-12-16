"""Trust scoring (Phase 9.1).

Trust is a slow-moving allocator score (weekly), distinct from per-cycle safety
gates. It is computed purely from Mongo facts:
- `pnl_reports`: realized performance/equity curve
- `audit_log`: deterministic governance signals (hard/soft violations)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import AGENT_STATES, AUDIT_LOG, PNL_REPORTS
from src.portfolio.metrics import calculate_max_drawdown, calculate_sharpe_ratio


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def week_bounds_utc(reference: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Return the current week bounds (Mon 00:00 UTC inclusive -> next Mon 00:00 UTC)."""
    now = _utc(reference or utc_now())
    # Monday=0 ... Sunday=6
    week_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=7)
    return week_start, week_end


def previous_week_bounds_utc(reference: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Return the previous week bounds (Mon->Mon UTC)."""
    week_start, _week_end = week_bounds_utc(reference)
    end = week_start
    start = end - timedelta(days=7)
    return start, end


@dataclass(frozen=True)
class WeeklyTrustStats:
    agent_id: str
    run_id: str
    start_ts: datetime
    end_ts: datetime
    start_equity: float
    end_equity: float
    return_pct: float
    sharpe: float
    max_drawdown_pct: float
    hard_violations: int
    soft_violations: int

    def to_dict(self) -> Dict[str, Any]:
        return jsonify(
            {
                "agent_id": self.agent_id,
                "run_id": self.run_id,
                "start_ts": self.start_ts,
                "end_ts": self.end_ts,
                "start_equity": self.start_equity,
                "end_equity": self.end_equity,
                "return_pct": self.return_pct,
                "sharpe": self.sharpe,
                "max_drawdown_pct": self.max_drawdown_pct,
                "hard_violations": self.hard_violations,
                "soft_violations": self.soft_violations,
            }
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _score_from_stats(stats: WeeklyTrustStats) -> Tuple[float, Dict[str, Any]]:
    """
    Map weekly stats to a trust score on [0, 100].

    Philosophy:
    - Reward risk-adjusted performance (return + Sharpe, drawdown-aware).
    - Penalize hard rule-breaking attempts even if vetoed (discipline).
    - Keep changes bounded; trust is slow-moving.
    """
    base = 50.0

    # Performance: returns (scaled) + Sharpe, drawdown penalty.
    ret_c = 20.0 * (stats.return_pct / 10.0)
    sharpe_c = 15.0 * (stats.sharpe / 2.0)
    dd_c = -15.0 * (stats.max_drawdown_pct / 10.0)
    perf = _clamp(ret_c + sharpe_c + dd_c, -35.0, 35.0)

    # Discipline: hard violations are expensive; soft violations are small.
    discipline = -(10.0 * stats.hard_violations + 2.0 * stats.soft_violations)
    discipline = _clamp(discipline, -40.0, 0.0)

    score = _clamp(base + perf + discipline, 0.0, 100.0)
    components = {
        "base": base,
        "performance": perf,
        "discipline": discipline,
        "inputs": stats.to_dict(),
    }
    return score, jsonify(components)


async def _fetch_weekly_equity_curve(
    *,
    mongo: MongoManager,
    run_id: str,
    agent_id: str,
    start: datetime,
    end: datetime,
) -> Tuple[List[datetime], List[float]]:
    col = mongo.collection(PNL_REPORTS)
    cursor = col.find({"run_id": run_id, "timestamp": {"$gte": start, "$lt": end}}).sort("timestamp", 1)
    docs = await cursor.to_list(length=10000)
    ts: List[datetime] = []
    eq: List[float] = []
    for d in docs or []:
        metrics = (d.get("agent_metrics") or {}).get(agent_id) or {}
        equity = metrics.get("total_equity")
        if equity is None:
            continue
        try:
            ts.append(_utc(d.get("timestamp") or utc_now()))
            eq.append(float(equity))
        except Exception:
            continue
    return ts, eq


async def _fetch_weekly_violation_counts(
    *,
    mongo: MongoManager,
    run_id: str,
    agent_id: str,
    start: datetime,
    end: datetime,
) -> Tuple[int, int]:
    """
    Count hard/soft violations from audit_log risk reports.

    We intentionally penalize hard-fail attempts even if the Manager vetoed.
    """
    col = mongo.collection(AUDIT_LOG)
    cursor = col.find(
        {
            "run_id": run_id,
            "timestamp": {"$gte": start, "$lt": end},
            "event_type": "risk_reports_ready",
        }
    ).sort("timestamp", 1)
    docs = await cursor.to_list(length=2000)

    hard = 0
    soft = 0
    for d in docs or []:
        payload = d.get("payload") or {}
        reports = payload.get("reports") or []
        if not isinstance(reports, list):
            continue
        for r in reports:
            if not isinstance(r, dict) or r.get("agent_id") != agent_id:
                continue
            try:
                hard += len(r.get("hard_violations") or [])
                soft += len(r.get("soft_violations") or [])
            except Exception:
                continue
    return hard, soft


async def compute_weekly_trust_stats(
    *,
    mongo: MongoManager,
    run_id: str,
    agent_id: str,
    start: datetime,
    end: datetime,
) -> Optional[WeeklyTrustStats]:
    await mongo.connect()
    start = _utc(start)
    end = _utc(end)

    ts, eq = await _fetch_weekly_equity_curve(mongo=mongo, run_id=run_id, agent_id=agent_id, start=start, end=end)
    if len(eq) < 2:
        return None

    start_equity = float(eq[0])
    end_equity = float(eq[-1])
    return_pct = 0.0
    if start_equity > 0:
        return_pct = ((end_equity - start_equity) / start_equity) * 100.0

    returns = pd.Series(eq).pct_change().dropna().tolist()
    sharpe = float(calculate_sharpe_ratio(returns))
    max_dd = float(calculate_max_drawdown(eq))

    hard, soft = await _fetch_weekly_violation_counts(
        mongo=mongo, run_id=run_id, agent_id=agent_id, start=start, end=end
    )

    return WeeklyTrustStats(
        agent_id=agent_id,
        run_id=run_id,
        start_ts=ts[0],
        end_ts=ts[-1],
        start_equity=start_equity,
        end_equity=end_equity,
        return_pct=return_pct,
        sharpe=sharpe,
        max_drawdown_pct=max_dd,
        hard_violations=hard,
        soft_violations=soft,
    )


async def update_weekly_trust_scores(
    *,
    mongo: MongoManager,
    run_id: str,
    start: datetime,
    end: datetime,
    default_trust: float = 50.0,
) -> Dict[str, Any]:
    """
    Compute + persist trust_score for all agents seen in pnl_reports for the window.

    Writes to `agent_states`:
    - trust_score
    - trust_components
    - trust_window (start/end)
    - trust_updated_at
    """
    await mongo.connect()

    # Discover agents from pnl_reports within the window.
    col = mongo.collection(PNL_REPORTS)
    doc = await col.find_one({"run_id": run_id, "timestamp": {"$gte": start, "$lt": end}}, sort=[("timestamp", 1)])
    agent_ids: List[str] = []
    if isinstance(doc, dict):
        agent_ids = sorted((doc.get("agent_metrics") or {}).keys())

    out: Dict[str, Any] = {"run_id": run_id, "start": start, "end": end, "updated": {}, "skipped": []}
    st_col = mongo.collection(AGENT_STATES)
    for agent_id in agent_ids:
        stats = await compute_weekly_trust_stats(mongo=mongo, run_id=run_id, agent_id=agent_id, start=start, end=end)
        if stats is None:
            out["skipped"].append(agent_id)
            continue
        score, components = _score_from_stats(stats)
        await st_col.update_one(
            {"agent_id": agent_id},
            {
                "$set": jsonify(
                    {
                        "agent_id": agent_id,
                        "trust_score": float(score),
                        "trust_components": components,
                        "trust_window": {"start": start, "end": end},
                        "trust_updated_at": utc_now(),
                    }
                )
            },
            upsert=True,
        )
        out["updated"][agent_id] = {"trust_score": float(score), "hard": stats.hard_violations, "soft": stats.soft_violations}

    # For any existing agent_states without a trust_score, set a default (best-effort).
    try:
        await st_col.update_many({"trust_score": {"$exists": False}}, {"$set": {"trust_score": default_trust}})
    except Exception:
        pass

    return jsonify(out)


async def load_trust_scores(
    *,
    mongo: MongoManager,
    agent_ids: List[str],
    default_trust: float = 50.0,
) -> Dict[str, float]:
    await mongo.connect()
    docs = await mongo.collection(AGENT_STATES).find({"agent_id": {"$in": agent_ids}}).to_list(length=200)
    out: Dict[str, float] = {a: float(default_trust) for a in agent_ids}
    for d in docs or []:
        aid = d.get("agent_id")
        if aid in out and d.get("trust_score") is not None:
            try:
                out[aid] = float(d["trust_score"])
            except Exception:
                continue
    return out


__all__ = [
    "previous_week_bounds_utc",
    "week_bounds_utc",
    "compute_weekly_trust_stats",
    "update_weekly_trust_scores",
    "load_trust_scores",
]

