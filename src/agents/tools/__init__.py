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


def build_tool_specs(context: Optional[ToolContext] = None) -> List[ToolSpec]:
    ctx = context or ToolContext()
    return [
        ToolSpec(
            name="get_market_brief",
            description="Get a compact, strategy-neutral Market Brief for symbols.",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "lookback_minutes": {"type": "integer", "minimum": 1, "default": 240},
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
                    "lookback_bars": {"type": "integer", "minimum": 1, "default": 180},
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
                },
                "required": ["symbols", "timeframes"],
            },
            func=_bind_context(get_indicator_pack, ctx),
        ),
        ToolSpec(
            name="get_recent_news",
            description="Get recent news events stored in Mongo for symbols.",
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
            description="Run a Tavily news search (network). Prefer get_recent_news first.",
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
    ]


def build_openrouter_tools(context: Optional[ToolContext] = None) -> List[Dict[str, Any]]:
    return [spec.openrouter_def() for spec in build_tool_specs(context)]


def build_tool_dispatch(context: Optional[ToolContext] = None) -> Dict[str, ToolFn]:
    return {spec.name: spec.func for spec in build_tool_specs(context)}


__all__ = [
    "ToolContext",
    "ToolSpec",
    "build_tool_specs",
    "build_openrouter_tools",
    "build_tool_dispatch",
]

