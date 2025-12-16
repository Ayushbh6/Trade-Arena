"""Market-related read-only tools."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ...config import load_config
from ...data.market_data import MarketDataIngestor
from ...data.mongo import MongoManager, jsonify, utc_now
from ...data.schemas import MARKET_SNAPSHOTS, NEWS_EVENTS, POSITIONS, ORDERS, AGENT_STATES
from ...features.market_state import MarketStateBuilder
from .context import ToolContext


async def _find_latest(
    mongo: MongoManager,
    collection: str,
    query: Dict[str, Any],
    *,
    as_of: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    q = dict(query)
    if as_of is not None:
        ts = q.get("timestamp")
        if isinstance(ts, dict):
            ts = dict(ts)
            ts.setdefault("$lte", as_of)
            q["timestamp"] = ts
        else:
            q["timestamp"] = {"$lte": as_of}
    if hasattr(mongo, "find_latest"):
        return await mongo.find_latest(collection, q)  # type: ignore[attr-defined]
    await mongo.connect()
    col = mongo.collection(collection)
    cursor = col.find(q).sort("timestamp", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def get_market_brief(
    *,
    symbols: List[str],
    lookback_minutes: int = 240,
    detail_level: str = "compact",
    allow_live_fetch: bool = False,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    cfg = ctx.config or load_config()
    builder = ctx.market_state_builder or MarketStateBuilder()

    snapshot: Optional[Dict[str, Any]] = None
    if ctx.snapshot is not None:
        snapshot = ctx.snapshot
    if ctx.mongo is not None:
        data_run_id = ctx.data_run_id or ctx.run_id
        q: Dict[str, Any] = {"symbols": {"$in": symbols}}
        if data_run_id:
            q["run_id"] = data_run_id
        snapshot = await _find_latest(
            ctx.mongo,
            MARKET_SNAPSHOTS,
            q,
            as_of=ctx.as_of,
        )

    if snapshot is None:
        if not allow_live_fetch:
            raise RuntimeError(
                "No market snapshot available and allow_live_fetch=false."
            )
        ingestor = MarketDataIngestor.from_app_config(cfg, mongo=ctx.mongo, run_id=ctx.run_id)
        snapshot = ingestor.build_snapshot()
        if ctx.mongo is not None:
            await ctx.mongo.insert_one(MARKET_SNAPSHOTS, snapshot)

    full_brief = builder.build_market_brief(snapshot)

    if detail_level == "full":
        brief = full_brief
    else:
        # Compact deterministic brief for LLM context:
        # - keep per-symbol mark/funding/oi/spread
        # - keep a small per-timeframe indicator subset (already compact in get_indicator_pack defaults)
        # - omit correlation matrix (can be very large)
        per_symbol_full = full_brief.get("per_symbol") or {}
        compact_per: Dict[str, Any] = {}
        for sym in symbols:
            ps = per_symbol_full.get(sym) or {}
            tf_full = ps.get("timeframes") or {}
            compact_tfs: Dict[str, Any] = {}
            for tf, tf_state in tf_full.items():
                ind = (tf_state.get("indicators") or {}) if isinstance(tf_state, dict) else {}
                subset = [
                    "trend",
                    "vol_regime",
                    "rsi_14",
                    "atr_14",
                    "bb_width",
                    "ema_20",
                    "sma_50",
                ]
                compact_tfs[tf] = {
                    "last_close": tf_state.get("last_close") if isinstance(tf_state, dict) else None,
                    "return_last_bar": tf_state.get("return_last_bar") if isinstance(tf_state, dict) else None,
                    "indicators": {k: ind.get(k) for k in subset if k in ind},
                }
            tob = ps.get("top_of_book") or {}
            compact_per[sym] = {
                "mark_price": ps.get("mark_price"),
                "funding_rate": ps.get("funding_rate"),
                "open_interest": ps.get("open_interest"),
                "top_of_book": {
                    "bid": tob.get("bid"),
                    "ask": tob.get("ask"),
                    "spread": tob.get("spread"),
                },
                "timeframes": compact_tfs,
            }

        breadth = ((full_brief.get("market_metrics") or {}).get("breadth")) if isinstance(full_brief.get("market_metrics"), dict) else None
        movers = []
        try:
            # movers are embedded in neutral_summary; keep breadth only.
            movers = []
        except Exception:
            movers = []

        brief = {
            "timestamp": full_brief.get("timestamp"),
            "run_id": full_brief.get("run_id"),
            "symbols": symbols,
            "snapshot_ref": {
                "collection": MARKET_SNAPSHOTS,
                "id": snapshot.get("_id"),
                "run_id": snapshot.get("run_id"),
                "timestamp": snapshot.get("timestamp"),
            },
            "per_symbol": compact_per,
            "market_metrics": {"breadth": breadth, "top_movers": movers},
            "events": {"news": []},
            "neutral_summary": full_brief.get("neutral_summary"),
            "meta": {"detail_level": "compact"},
        }

    # Attach recent news (if available)
    if ctx.mongo is not None:
        now = ctx.as_of or utc_now()
        cutoff = now - timedelta(minutes=lookback_minutes)
        data_run_id = ctx.data_run_id or ctx.run_id
        await ctx.mongo.connect()
        col = ctx.mongo.collection(NEWS_EVENTS)
        news_q: Dict[str, Any] = {
            "timestamp": {"$gte": cutoff, "$lte": now},
            "symbols": {"$in": symbols},
        }
        if data_run_id:
            news_q["run_id"] = data_run_id
        cursor = (
            col.find(news_q)
            .sort("timestamp", -1)
            .limit(20)
        )
        news_docs = await cursor.to_list(length=20)
        brief.setdefault("events", {})["news"] = [
            {
                "timestamp": d.get("timestamp"),
                "title": d.get("title"),
                "url": d.get("url"),
                "summary": d.get("summary"),
                "symbols": d.get("symbols", []),
                "source": d.get("source", "unknown"),
            }
            for d in news_docs
        ]

    return jsonify(brief)


async def get_position_summary(
    *,
    agent_id: str,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    if ctx.mongo is None:
        return {"agent_id": agent_id, "positions": [], "last_orders": []}

    await ctx.mongo.connect()
    pos_col = ctx.mongo.collection(POSITIONS)
    ord_col = ctx.mongo.collection(ORDERS)

    data_run_id = ctx.data_run_id or ctx.run_id
    pos_q: Dict[str, Any] = {"agent_owner": agent_id}
    ord_q: Dict[str, Any] = {"agent_owner": agent_id}
    if data_run_id:
        pos_q["run_id"] = data_run_id
        ord_q["run_id"] = data_run_id
    positions = await pos_col.find(pos_q).to_list(length=50)
    last_orders = (
        await ord_col.find(ord_q)
        .sort("timestamp", -1)
        .limit(10)
        .to_list(length=10)
    )

    return jsonify(
        {"agent_id": agent_id, "positions": positions or [], "last_orders": last_orders or []}
    )


async def get_firm_state(
    *,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    cfg = ctx.config or load_config()

    firm: Dict[str, Any] = {
        "capital_usdt": cfg.risk.agent_budget_notional_usd * max(
            1, len(cfg.models.trader_models) or 1
        ),
        "total_notional_usdt": 0.0,
        "drawdown_pct": 0.0,
        "risk_limits": cfg.risk.__dict__,
        "agent_budgets": {
            k: cfg.risk.agent_budget_notional_usd for k in (cfg.models.trader_models or {})
        },
    }

    if ctx.mongo is None:
        return jsonify(firm)

    await ctx.mongo.connect()
    # Best-effort aggregation from agent_states if present.
    data_run_id = ctx.data_run_id or ctx.run_id
    q: Dict[str, Any] = {}
    if data_run_id:
        q["run_id"] = data_run_id
    states = await ctx.mongo.collection(AGENT_STATES).find(q).to_list(length=50)
    if states:
        budgets: Dict[str, Any] = {}
        for s in states:
            aid = s.get("agent_id")
            if aid:
                budgets[aid] = s.get("budget_usdt", cfg.risk.agent_budget_notional_usd)
        firm["agent_budgets"] = budgets or firm["agent_budgets"]

    return jsonify(firm)


async def query_memory(
    *,
    agent_id: str,
    query: str,
    lookback_days: int = 7,
    max_items: int = 20,
    filters: Optional[Dict[str, Any]] = None,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    if bool(ctx.replay_mode):
        raise RuntimeError("query_memory is disabled in replay_mode (no embeddings/network).")
    if ctx.mongo is None:
        return {
            "agent_id": agent_id,
            "query": query,
            "lookback_days": lookback_days,
            "matches": [],
        }
    from ..memory.search_backend import MongoEmbeddingBackend

    backend = MongoEmbeddingBackend(
        mongo=ctx.mongo,
        embedding_model=None,
        api_key=None,
    )
    return await backend.search(
        agent_id=agent_id,
        query=query,
        lookback_days=lookback_days,
        max_items=max_items,
        filters=filters,
    )


__all__ = [
    "get_market_brief",
    "get_position_summary",
    "get_firm_state",
    "query_memory",
]
