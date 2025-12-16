"""News-related read-only tools."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...config import load_config
from ...data.mongo import jsonify, utc_now
from ...data.news_connector import TavilyNewsConnector
from ...data.schemas import NEWS_EVENTS
from .context import ToolContext


async def tavily_search(
    *,
    query: str,
    max_results: int = 8,
    recency_hours: int = 24,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    if bool(ctx.replay_mode):
        raise RuntimeError("tavily_search is disabled in replay_mode (no network).")
    connector = ctx.news_connector
    if connector is None:
        cfg = ctx.config or load_config()
        connector = TavilyNewsConnector.from_app_config(cfg, mongo=ctx.mongo, run_id=ctx.run_id)
    results = connector.search(query, max_results=max_results, recency_hours=recency_hours)

    persisted = 0
    if ctx.mongo is not None:
        try:
            # Normalize and persist for audit/replay as system-of-record.
            # We do not attempt to fabricate symbol tagging here; keep symbols empty unless
            # upstream ingestion provided explicit associations.
            docs = connector.normalize_results(results, symbols=[])
            await ctx.mongo.connect()
            for d in docs:
                await ctx.mongo.insert_one(NEWS_EVENTS, d)
                persisted += 1
        except Exception:
            # Persistence is best-effort; the tool result is still returned.
            persisted = 0

    return jsonify({"query": query, "results": results, "persisted_news_events": persisted})


async def get_recent_news(
    *,
    symbols: List[str],
    lookback_hours: int = 24,
    max_items: int = 20,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    now = ctx.as_of or utc_now()
    if ctx.mongo is None:
        return {"as_of": now, "symbols": {s: [] for s in symbols}}

    cutoff = now - timedelta(hours=lookback_hours)
    await ctx.mongo.connect()
    col = ctx.mongo.collection(NEWS_EVENTS)
    data_run_id = ctx.data_run_id or ctx.run_id
    q: Dict[str, Any] = {"timestamp": {"$gte": cutoff, "$lte": now}, "symbols": {"$in": symbols}}
    if data_run_id:
        q["run_id"] = data_run_id
    cursor = (
        col.find(q)
        .sort("timestamp", -1)
        .limit(max_items)
    )
    docs = await cursor.to_list(length=max_items)

    grouped: Dict[str, List[Dict[str, Any]]] = {s: [] for s in symbols}
    for d in docs:
        syms = d.get("symbols") or []
        item = {
            "timestamp": d.get("timestamp"),
            "title": d.get("title"),
            "url": d.get("url"),
            "summary": d.get("summary"),
            "source": d.get("source", "unknown"),
        }
        for s in syms:
            if s in grouped:
                grouped[s].append(item)

    return jsonify({"as_of": now, "symbols": grouped})


__all__ = ["tavily_search", "get_recent_news"]
