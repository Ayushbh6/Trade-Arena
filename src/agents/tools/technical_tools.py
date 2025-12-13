"""Technical read-only tools."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...data.mongo import MongoManager, jsonify
from ...data.schemas import MARKET_SNAPSHOTS
from ...features.market_state import MarketStateBuilder
from .context import ToolContext
from .market_tools import _find_latest


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _summarize_candles(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candles:
        return {"bars": 0}
    closes = [_safe_float(c.get("close")) for c in candles]
    highs = [_safe_float(c.get("high")) for c in candles]
    lows = [_safe_float(c.get("low")) for c in candles]
    vols = [_safe_float(c.get("volume")) for c in candles]

    closes_f = [c for c in closes if isinstance(c, float)]
    highs_f = [h for h in highs if isinstance(h, float)]
    lows_f = [l for l in lows if isinstance(l, float)]
    vols_f = [v for v in vols if isinstance(v, float)]

    ret = None
    if len(closes_f) >= 2 and closes_f[0] and closes_f[-1]:
        try:
            ret = (closes_f[-1] / closes_f[0]) - 1.0
        except Exception:
            ret = None

    # realized vol: std of log returns
    rv = None
    if len(closes_f) >= 3:
        lrs: List[float] = []
        for a, b in zip(closes_f[:-1], closes_f[1:]):
            if a and b and a > 0 and b > 0:
                lrs.append(math.log(b / a))
        if len(lrs) >= 2:
            m = sum(lrs) / len(lrs)
            var = sum((x - m) ** 2 for x in lrs) / (len(lrs) - 1)
            rv = math.sqrt(max(var, 0.0))

    return {
        "bars": len(candles),
        "t_start_ms": candles[0].get("open_time_ms"),
        "t_end_ms": candles[-1].get("close_time_ms"),
        "last_close": closes_f[-1] if closes_f else None,
        "high": max(highs_f) if highs_f else None,
        "low": min(lows_f) if lows_f else None,
        "return": ret,
        "realized_vol": rv,
        "volume_sum": float(sum(vols_f)) if vols_f else None,
        "volume_last": vols_f[-1] if vols_f else None,
    }


async def get_candles(
    *,
    symbols: List[str],
    timeframes: List[str],
    lookback_bars: int = 60,
    detail_level: str = "compact",
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    if ctx.mongo is None:
        raise RuntimeError("Mongo is required for get_candles.")

    snapshot = await _find_latest(
        ctx.mongo, MARKET_SNAPSHOTS, {"symbols": {"$in": symbols}}
    )
    if snapshot is None:
        raise RuntimeError("No market snapshot available.")

    max_bars = 120
    bars = max(1, min(int(lookback_bars), max_bars))
    out: Dict[str, Any] = {
        "as_of": snapshot.get("timestamp"),
        "snapshot_ref": {
            "collection": MARKET_SNAPSHOTS,
            "id": snapshot.get("_id"),
            "run_id": snapshot.get("run_id"),
            "timestamp": snapshot.get("timestamp"),
        },
        "symbols": {},
        "meta": {
            "detail_level": detail_level,
            "lookback_bars_requested": lookback_bars,
            "lookback_bars_returned": bars,
            "truncated": bars != int(lookback_bars),
            "format_compact": "[t_ms,o,h,l,c,v]",
        },
    }
    for sym in symbols:
        sym_snap = (snapshot.get("per_symbol") or {}).get(sym) or {}
        candles_by_tf = sym_snap.get("candles") or {}
        out["symbols"][sym] = {}
        for tf in timeframes:
            candles = list(candles_by_tf.get(tf) or [])
            window = candles[-bars:]
            if detail_level == "full":
                out["symbols"][sym][tf] = {"format": "dict", "bars": window, "stats": _summarize_candles(window)}
            else:
                compact = [
                    [
                        c.get("open_time_ms"),
                        c.get("open"),
                        c.get("high"),
                        c.get("low"),
                        c.get("close"),
                        c.get("volume"),
                    ]
                    for c in window
                ]
                out["symbols"][sym][tf] = {"format": "ohlcv_6", "bars": compact, "stats": _summarize_candles(window)}

    return jsonify(out)


async def get_indicator_pack(
    *,
    symbols: List[str],
    timeframes: List[str],
    keys: Optional[List[str]] = None,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    if ctx.mongo is None:
        raise RuntimeError("Mongo is required for get_indicator_pack.")

    snapshot = await _find_latest(
        ctx.mongo, MARKET_SNAPSHOTS, {"symbols": {"$in": symbols}}
    )
    if snapshot is None:
        raise RuntimeError("No market snapshot available.")

    builder = ctx.market_state_builder or MarketStateBuilder()
    out: Dict[str, Any] = {
        "as_of": snapshot.get("timestamp"),
        "snapshot_ref": {
            "collection": MARKET_SNAPSHOTS,
            "id": snapshot.get("_id"),
            "run_id": snapshot.get("run_id"),
            "timestamp": snapshot.get("timestamp"),
        },
        "symbols": {},
        "meta": {"requested_keys": keys, "detail_level": "compact"},
    }

    default_subset = [
        "trend",
        "vol_regime",
        "rsi_14",
        "atr_14",
        "ema_20",
        "sma_20",
        "sma_60",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "realized_vol",
    ]

    for sym in symbols:
        sym_snap = (snapshot.get("per_symbol") or {}).get(sym) or {}
        state = builder.per_symbol_state(sym, sym_snap)
        out["symbols"][sym] = {}
        for tf in timeframes:
            tf_state = (state.get("timeframes") or {}).get(tf) or {}
            inds = tf_state.get("indicators") or {}
            if keys:
                picked = {k: inds.get(k) for k in keys if k in inds}
                if not picked and inds:
                    # If the model requested unknown keys, return a helpful compact default and a warning.
                    out["symbols"][sym][tf] = {
                        "_warning": "requested_keys_not_found; returning default compact subset",
                        "requested_keys": keys,
                        "returned_keys": [k for k in default_subset if k in inds],
                        "data": {k: inds.get(k) for k in default_subset if k in inds},
                    }
                else:
                    out["symbols"][sym][tf] = picked
            else:
                out["symbols"][sym][tf] = {k: inds.get(k) for k in default_subset if k in inds}

    return jsonify(out)


__all__ = ["get_candles", "get_indicator_pack"]
