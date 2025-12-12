"""Market-related read-only tools."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...config import load_config
from ...data.market_data import MarketDataIngestor
from ...data.mongo import MongoManager, jsonify, utc_now
from ...data.schemas import MARKET_SNAPSHOTS, NEWS_EVENTS, POSITIONS, ORDERS, AGENT_STATES
from ...features.market_state import MarketStateBuilder
from .context import ToolContext


async def _find_latest(
    mongo: MongoManager, collection: str, query: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if hasattr(mongo, "find_latest"):
        return await mongo.find_latest(collection, query)  # type: ignore[attr-defined]
    await mongo.connect()
    col = mongo.collection(collection)
    cursor = col.find(query).sort("timestamp", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def get_market_brief(
    *,
    symbols: List[str],
    lookback_minutes: int = 240,
    allow_live_fetch: bool = False,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    ctx = context or ToolContext()
    cfg = ctx.config or load_config()
    builder = ctx.market_state_builder or MarketStateBuilder()

    snapshot: Optional[Dict[str, Any]] = None
    if ctx.mongo is not None:
        snapshot = await _find_latest(
            ctx.mongo,
            MARKET_SNAPSHOTS,
            {"symbols": {"$in": symbols}},
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

    brief = builder.build_market_brief(snapshot)

    # Attach recent news (if available)
    if ctx.mongo is not None:
        cutoff = utc_now() - timedelta(minutes=lookback_minutes)
        await ctx.mongo.connect()
        col = ctx.mongo.collection(NEWS_EVENTS)
        cursor = (
            col.find({"timestamp": {"$gte": cutoff}, "symbols": {"$in": symbols}})
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

    positions = await pos_col.find({"agent_owner": agent_id}).to_list(length=50)
    last_orders = (
        await ord_col.find({"agent_owner": agent_id})
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
    states = await ctx.mongo.collection(AGENT_STATES).find({}).to_list(length=50)
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
