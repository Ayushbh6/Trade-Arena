"""Tool registry for REACT agents.

Registry returns OpenRouter-compatible tool definitions and bound callables.
No external frameworks; tools are simple (mostly async) Python functions.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .context import ToolContext
from .market_tools import get_firm_state, get_market_brief, get_position_summary, query_memory
from .news_tools import get_recent_news, tavily_search
from .portfolio_tools import rebalance_budgets
from .structure_tools import get_funding_oi_history, get_orderbook_top
from .technical_tools import get_candles, get_indicator_pack


ToolFn = Callable[..., Union[Dict[str, Any], Awaitable[Dict[str, Any]]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: ToolFn

    def openrouter_def(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _bind_context(fn: ToolFn, ctx: ToolContext) -> ToolFn:
    if "context" not in inspect.signature(fn).parameters:
        return fn

    async def _awrap(**kwargs: Any) -> Dict[str, Any]:
        res = fn(context=ctx, **kwargs)  # type: ignore[misc]
        if inspect.isawaitable(res):
            return await res  # type: ignore[return-value]
        return res  # type: ignore[return-value]

    def _wrap(**kwargs: Any) -> Awaitable[Dict[str, Any]]:
        return _awrap(**kwargs)

    return _wrap


def build_tool_specs(
    context: Optional[ToolContext] = None,
    *,
    allowed_tools: Optional[List[str]] = None,
) -> List[ToolSpec]:
    ctx = context or ToolContext()
    specs = [
        ToolSpec(
            name="get_market_brief",
            description="Get a compact, strategy-neutral Market Brief for symbols.",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "lookback_minutes": {"type": "integer", "minimum": 1, "default": 240},
                    "detail_level": {
                        "type": "string",
                        "default": "compact",
                        "description": "compact returns a smaller brief suitable for LLM context; full returns richer payload.",
                        "enum": ["compact", "full"],
                    },
                    "allow_live_fetch": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, fetch fresh data from Binance when no DB snapshot exists.",
                    },
                },
                "required": ["symbols"],
            },
            func=_bind_context(get_market_brief, ctx),
        ),
        ToolSpec(
            name="get_candles",
            description="Fetch recent OHLCV candles from latest snapshot.",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "timeframes": {"type": "array", "items": {"type": "string"}},
                    "lookback_bars": {"type": "integer", "minimum": 1, "default": 60},
                    "detail_level": {
                        "type": "string",
                        "default": "compact",
                        "description": "compact returns [t_ms,o,h,l,c,v] arrays + stats; full returns candle dicts.",
                        "enum": ["compact", "full"],
                    },
                },
                "required": ["symbols", "timeframes"],
            },
            func=_bind_context(get_candles, ctx),
        ),
        ToolSpec(
            name="get_indicator_pack",
            description="Get precomputed indicator packs per symbol/timeframe.",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "timeframes": {"type": "array", "items": {"type": "string"}},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional allowlist of indicator keys to return (to keep payload small).",
                    },
                },
                "required": ["symbols", "timeframes"],
            },
            func=_bind_context(get_indicator_pack, ctx),
        ),
        ToolSpec(
            name="get_recent_news",
            description=(
                "Read cached/previously-stored news from MongoDB (news_events) for symbols. "
                "No web access; returns only what is already persisted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "lookback_hours": {"type": "integer", "minimum": 1, "default": 24},
                    "max_items": {"type": "integer", "minimum": 1, "default": 20},
                },
                "required": ["symbols"],
            },
            func=_bind_context(get_recent_news, ctx),
        ),
        ToolSpec(
            name="tavily_search",
            description=(
                "Run a live Tavily web/news search (network) for a custom query. "
                "This fetches fresh results and also persists normalized items into MongoDB news_events "
                "(when Mongo is configured), so they can be replayed/audited later."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "default": 8},
                    "recency_hours": {"type": "integer", "minimum": 1, "default": 24},
                },
                "required": ["query"],
            },
            func=_bind_context(tavily_search, ctx),
        ),
        ToolSpec(
            name="get_position_summary",
            description="Get agent's current positions (may be empty pre-execution).",
            parameters={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
            func=_bind_context(get_position_summary, ctx),
        ),
        ToolSpec(
            name="get_firm_state",
            description="Get firm-level risk, capital, and budgets (defaults if unavailable).",
            parameters={"type": "object", "properties": {}},
            func=_bind_context(get_firm_state, ctx),
        ),
        ToolSpec(
            name="query_memory",
            description="Semantic search over agent's past proposals/decisions/logs.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "query": {"type": "string"},
                    "lookback_days": {"type": "integer", "minimum": 1, "default": 7},
                    "max_items": {"type": "integer", "minimum": 1, "default": 20},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "symbols": {"type": "array", "items": {"type": "string"}},
                            "event_types": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["agent_id", "query"],
            },
            func=_bind_context(query_memory, ctx),
        ),
        ToolSpec(
            name="get_orderbook_top",
            description="Get latest bid/ask/spread (top-of-book) from the most recent stored market snapshot.",
            parameters={
                "type": "object",
                "properties": {"symbols": {"type": "array", "items": {"type": "string"}}},
                "required": ["symbols"],
            },
            func=_bind_context(get_orderbook_top, ctx),
        ),
        ToolSpec(
            name="get_funding_oi_history",
            description=(
                "Get a compact time series of (timestamp, mark_price, funding_rate, open_interest, spread) "
                "from stored market snapshots (NOT live). Includes summary stats (mean/std/zscore)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "lookback_hours": {"type": "integer", "minimum": 1, "default": 48},
                    "max_points": {"type": "integer", "minimum": 1, "default": 120},
                },
                "required": ["symbols"],
            },
            func=_bind_context(get_funding_oi_history, ctx),
        ),
        ToolSpec(
            name="rebalance_budgets",
            description=(
                "Weekly governance tool (Manager only): deterministically rebalance agent budget_usdt caps "
                "based on a provided performance/trust table. Updates Mongo agent_states and writes an audit event."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "performance_table": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent_id": {"type": "string"},
                                "trust_score": {"type": "number", "minimum": 0, "maximum": 100},
                            },
                            "required": ["agent_id", "trust_score"],
                            "additionalProperties": True,
                        },
                    },
                    "min_budget_mult": {"type": "number", "minimum": 0.0, "default": 0.5},
                    "max_budget_mult": {"type": "number", "minimum": 0.0, "default": 2.0},
                    "max_weekly_change_pct": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.25},
                },
                "required": ["performance_table"],
            },
            func=_bind_context(rebalance_budgets, ctx),
        ),
    ]

    if allowed_tools is None:
        return specs

    allow = set(allowed_tools)
    return [s for s in specs if s.name in allow]


def build_openrouter_tools(
    context: Optional[ToolContext] = None,
    *,
    allowed_tools: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    return [spec.openrouter_def() for spec in build_tool_specs(context, allowed_tools=allowed_tools)]


def build_tool_dispatch(
    context: Optional[ToolContext] = None,
    *,
    allowed_tools: Optional[List[str]] = None,
) -> Dict[str, ToolFn]:
    return {spec.name: spec.func for spec in build_tool_specs(context, allowed_tools=allowed_tools)}


__all__ = [
    "ToolContext",
    "ToolSpec",
    "build_tool_specs",
    "build_openrouter_tools",
    "build_tool_dispatch",
    "get_orderbook_top",
    "get_funding_oi_history",
    "rebalance_budgets",
]
