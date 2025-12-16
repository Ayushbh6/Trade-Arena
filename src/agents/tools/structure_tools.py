"""Market-structure read-only tools (Phase 8.3).

Deterministic tools for funding/OI/top-of-book so the Market-Structure trader can
detect extremes and liquidity conditions without guessing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ...data.mongo import jsonify, utc_now
from ...data.schemas import MARKET_SNAPSHOTS
from .context import ToolContext


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _mean(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _std(xs: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    if m is None:
        return None
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    if var < 0:
        return None
    return var ** 0.5


async def get_orderbook_top(
    *,
    symbols: List[str],
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Get latest top-of-book (bid/ask/spread) from the most recent stored snapshot."""
    ctx = context or ToolContext()
    if ctx.mongo is None:
        raise RuntimeError("Mongo is required for get_orderbook_top.")

    snapshot = ctx.snapshot
    if snapshot is None:
        data_run_id = ctx.data_run_id or ctx.run_id
        q: Dict[str, Any] = {"symbols": {"$in": symbols}}
        if data_run_id:
            q["run_id"] = data_run_id
        if ctx.as_of is not None:
            q["timestamp"] = {"$lte": ctx.as_of}
        await ctx.mongo.connect()
        col = ctx.mongo.collection(MARKET_SNAPSHOTS)
        snap = (
            await col.find(q).sort("timestamp", -1).limit(1).to_list(length=1)
        )
        snapshot = snap[0] if snap else None
    if snapshot is None:
        raise RuntimeError("No market snapshot available.")

    out: Dict[str, Any] = {
        "as_of": snapshot.get("timestamp"),
        "snapshot_ref": {
            "collection": MARKET_SNAPSHOTS,
            "id": snapshot.get("_id"),
            "run_id": snapshot.get("run_id"),
            "timestamp": snapshot.get("timestamp"),
        },
        "symbols": {},
    }
    per = snapshot.get("per_symbol") or {}
    for s in symbols:
        ps = per.get(s) or {}
        tob = ps.get("top_of_book") or {}
        out["symbols"][s] = {
            "bid": tob.get("bid"),
            "ask": tob.get("ask"),
            "spread": tob.get("spread"),
        }
    return jsonify(out)


async def get_funding_oi_history(
    *,
    symbols: List[str],
    lookback_hours: int = 48,
    max_points: int = 120,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Return a deterministic funding/OI time series from stored snapshots.

    This does NOT fetch live exchange data. It queries MongoDB market_snapshots
    and returns a compact time series of:
      (timestamp, mark_price, funding_rate, open_interest, spread)

    It also returns summary stats (mean/std/zscore) to help detect extremes.
    """
    ctx = context or ToolContext()
    if ctx.mongo is None:
        raise RuntimeError("Mongo is required for get_funding_oi_history.")

    hours = max(1, int(lookback_hours))
    points = max(1, min(int(max_points), 240))

    now = ctx.as_of or utc_now()
    cutoff = now - timedelta(hours=hours)
    as_of = ctx.as_of
    await ctx.mongo.connect()
    col = ctx.mongo.collection(MARKET_SNAPSHOTS)
    data_run_id = ctx.data_run_id or ctx.run_id
    q: Dict[str, Any] = {"timestamp": {"$gte": cutoff}, "symbols": {"$in": symbols}}
    if as_of is not None:
        q["timestamp"]["$lte"] = as_of
    if data_run_id:
        q["run_id"] = data_run_id
    cursor = (
        col.find(q)
        .sort("timestamp", -1)
        .limit(points)
    )
    docs = await cursor.to_list(length=points)
    docs = list(reversed(docs))  # oldest -> newest

    out: Dict[str, Any] = {
        "as_of": utc_now(),
        "cutoff": cutoff,
        "points": len(docs),
        "symbols": {},
        "meta": {
            "lookback_hours": hours,
            "max_points": points,
            "note": "Computed from stored snapshots; not live. Use as relative history within this run's data.",
        },
    }

    for sym in symbols:
        series: List[List[Any]] = []
        fundings: List[float] = []
        ois: List[float] = []
        for d in docs:
            ps = (d.get("per_symbol") or {}).get(sym) or {}
            tob = ps.get("top_of_book") or {}
            ts: Any = d.get("timestamp")
            if isinstance(ts, datetime):
                ts_out: Any = ts
            else:
                ts_out = ts
            mp = _safe_float(ps.get("mark_price"))
            fr = _safe_float(ps.get("funding_rate"))
            oi = _safe_float(ps.get("open_interest"))
            sp = _safe_float(tob.get("spread"))
            series.append([ts_out, mp, fr, oi, sp])
            if isinstance(fr, float):
                fundings.append(fr)
            if isinstance(oi, float):
                ois.append(oi)

        fr_mean = _mean(fundings)
        fr_std = _std(fundings)
        oi_mean = _mean(ois)
        oi_std = _std(ois)

        latest_fr = fundings[-1] if fundings else None
        latest_oi = ois[-1] if ois else None
        z_fr = None
        if latest_fr is not None and fr_mean is not None and fr_std and fr_std > 0:
            z_fr = (latest_fr - fr_mean) / fr_std
        z_oi = None
        if latest_oi is not None and oi_mean is not None and oi_std and oi_std > 0:
            z_oi = (latest_oi - oi_mean) / oi_std

        out["symbols"][sym] = {
            "format": "[timestamp, mark_price, funding_rate, open_interest, spread]",
            "series": series,
            "stats": {
                "funding_rate": {
                    "latest": latest_fr,
                    "mean": fr_mean,
                    "std": fr_std,
                    "zscore_latest": z_fr,
                    "min": min(fundings) if fundings else None,
                    "max": max(fundings) if fundings else None,
                },
                "open_interest": {
                    "latest": latest_oi,
                    "mean": oi_mean,
                    "std": oi_std,
                    "zscore_latest": z_oi,
                    "min": min(ois) if ois else None,
                    "max": max(ois) if ois else None,
                },
            },
        }

    return jsonify(out)


__all__ = ["get_orderbook_top", "get_funding_oi_history"]
