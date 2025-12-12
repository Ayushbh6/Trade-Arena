"""Technical read-only tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...data.mongo import MongoManager, jsonify
from ...data.schemas import MARKET_SNAPSHOTS
from ...features.market_state import MarketStateBuilder
from .context import ToolContext
from .market_tools import _find_latest


async def get_candles(
    *,
    symbols: List[str],
    timeframes: List[str],
    lookback_bars: int = 180,
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

    out: Dict[str, Any] = {"as_of": snapshot.get("timestamp"), "symbols": {}}
    for sym in symbols:
        sym_snap = (snapshot.get("per_symbol") or {}).get(sym) or {}
        candles_by_tf = sym_snap.get("candles") or {}
        out["symbols"][sym] = {}
        for tf in timeframes:
            candles = list(candles_by_tf.get(tf) or [])
            out["symbols"][sym][tf] = candles[-lookback_bars:]

    return jsonify(out)


async def get_indicator_pack(
    *,
    symbols: List[str],
    timeframes: List[str],
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
    out: Dict[str, Any] = {"as_of": snapshot.get("timestamp"), "symbols": {}}

    for sym in symbols:
        sym_snap = (snapshot.get("per_symbol") or {}).get(sym) or {}
        state = builder.per_symbol_state(sym, sym_snap)
        out["symbols"][sym] = {}
        for tf in timeframes:
            tf_state = (state.get("timeframes") or {}).get(tf) or {}
            out["symbols"][sym][tf] = tf_state.get("indicators") or {}

    return jsonify(out)


__all__ = ["get_candles", "get_indicator_pack"]

