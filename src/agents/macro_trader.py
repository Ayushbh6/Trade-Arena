"""Macro / News / Narrative trader agent (Phase 8.1).

This trader focuses on higher-conviction, narrative and macro-driven setups.
It is expected to use Tavily searches thoughtfully (within tool budget) and
ground any claims via tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agents.base import BaseTrader, BaseTraderConfig
from src.agents.schemas import TradeProposal
from src.agents.tools import ToolContext


MACRO_ROLE_PROMPT = """
# Role: Macro / News / Narrative Trader (Crypto Perps)

You are the **Macro/News trader** in an AI-native trading desk. You trade **USDT-margined perpetual futures** on liquid symbols (e.g., BTCUSDT, ETHUSDT, SOLUSDT).

Your job is to produce **fewer, higher-conviction** trade proposals driven by:
- macro and risk-on/off regime shifts
- narrative momentum (ETF/regs/major headlines)
- catalysts (scheduled events, major releases, major policy/central-bank signals)
- cross-asset cues that plausibly affect crypto

You MUST stay grounded in tool outputs. If you cannot confirm a claim using available tools, do not trade.

---

## How to Use News Tools (Important)

There are two news-related tools, and they are NOT the same:

1) `get_recent_news`:
   - Reads **cached** news already stored in MongoDB (`news_events`).
   - **No web access**. It can be stale if nothing has been ingested recently.
   - Use it to quickly recall what is already persisted for audit/replay.

2) `tavily_search`:
   - Performs a **live web/news search** via Tavily (network call).
   - Use it to discover fresh macro narratives and cross-check sources.
   - Search results are **persisted** into MongoDB (`news_events`) when Mongo is configured,
     so they become part of the system-of-record.

Guidance for `tavily_search`:
- Start broad (1 query), then narrow (1–2 follow-ups) based on results.
- Prefer precise queries: include timeframe ("last 24 hours"), topic ("risk-off", "Fed", "ETF flows"), and asset ("Bitcoin", "Ethereum").
- Cross-check: do not trade based on a single headline.

---

## Time Horizon & Discipline

- Default horizon: **1h–3d** (justify in your proposal).
- Avoid overtrading. If edge/catalyst clarity is low, output **no trade** (`trades=[]`).

---

## Output Rules (Non-Negotiable)

- Final response must be **ONLY** a `TradeProposal` JSON matching the schema.
- No markdown, no extra text, no code fences.
- You MUST include a short reasoning summary in top-level `notes` for every response.
"""


@dataclass
class MacroTraderConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 6
    max_tool_calls: int = 6
    enable_phase7_context: bool = False
    enable_phase7_compression: bool = False
    phase7_summarizer_model: Optional[str] = None


class MacroTrader:
    def __init__(
        self,
        *,
        agent_id: str = "macro_trader",
        config: MacroTraderConfig,
        tools_context: Optional[ToolContext] = None,
        allowed_tools: Optional[list[str]] = None,
    ):
        self.agent_id = agent_id
        self.config = config
        self.tools_context = tools_context

        if allowed_tools is None:
            allowed_tools = [
                "get_market_brief",
                "get_recent_news",
                "tavily_search",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ]

        self._base = BaseTrader(
            agent_id=self.agent_id,
            role_prompt=MACRO_ROLE_PROMPT,
            config=BaseTraderConfig(
                model=config.model,
                temperature=config.temperature,
                max_tool_turns=config.max_tool_turns,
                max_tool_calls=config.max_tool_calls,
                enable_phase7_context=config.enable_phase7_context,
                enable_phase7_compression=config.enable_phase7_compression,
                phase7_summarizer_model=config.phase7_summarizer_model,
                allowed_tools=allowed_tools,
            ),
            tools_context=tools_context,
        )

    @property
    def last_tool_calls(self):
        return self._base.last_tool_calls

    @property
    def last_messages(self):
        return self._base.last_messages

    async def decide(
        self,
        *,
        market_brief: Dict[str, Any],
        firm_state: Optional[Dict[str, Any]] = None,
        position_summary: Optional[Dict[str, Any]] = None,
        memory_snippet: Optional[str] = None,
        extra_instructions: Optional[str] = None,
    ) -> TradeProposal:
        return await self._base.decide(
            market_brief=market_brief,
            firm_state=firm_state,
            position_summary=position_summary,
            memory_snippet=memory_snippet,
            extra_instructions=extra_instructions,
        )


__all__ = ["MacroTrader", "MacroTraderConfig", "MACRO_ROLE_PROMPT"]

