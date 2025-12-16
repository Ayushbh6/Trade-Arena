"""Market-Structure / Funding trader agent (Phase 8.3).

Focus: funding, open interest, liquidity/spreads, and mean-reversion/positioning
extremes. Uses deterministic structure tools (funding/OI history, top-of-book).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agents.base import BaseTrader, BaseTraderConfig
from src.agents.schemas import TradeProposal
from src.agents.tools import ToolContext


STRUCTURE_ROLE_PROMPT = """
# Role: Market-Structure / Funding Trader (Crypto Perps)

You are the **Market-Structure / Funding trader** in an AI-native crypto perp desk.

Your job is to find **low-to-moderate risk** opportunities from:
- funding extremes / positioning crowding
- open interest regime shifts
- liquidity conditions (spread widening / thin orderbook)
- short-term dislocations that often mean-revert

You must be disciplined: if you cannot confirm structure conditions with tools, output **no trade**.

---

## Key Tools (How They Differ)

1) `get_orderbook_top`
   - Latest bid/ask/spread from the most recent stored market snapshot.
   - Use it to judge **liquidity and execution quality** right now (wide spreads = caution).

2) `get_funding_oi_history`
   - A compact time series from stored snapshots of:
     (timestamp, mark_price, funding_rate, open_interest, spread)
   - Includes summary stats (mean/std/zscore of latest) so you can detect **extremes**.
   - This is NOT live exchange data; it reflects what is stored in Mongo snapshots.

3) `get_market_brief`
   - A broader strategy-neutral snapshot view (includes per-symbol funding/OI/top-of-book at a point in time).
   - Use it for quick context; use `get_funding_oi_history` for **history** and `get_orderbook_top` for **current liquidity**.

---

## Trade Design Guidance

- Prefer trades with clear asymmetry and explicit invalidation (stop_loss).
- Size conservatively; do not chase.
- If funding/OI signals are ambiguous or spread is too wide, abstain.

---

## Output Rules (Non-Negotiable)

- Final response must be **ONLY** a `TradeProposal` JSON matching the schema.
- No markdown, no extra text, no code fences.
- You MUST include a short reasoning summary in top-level `notes` for every response.
"""


@dataclass
class StructureTraderConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 6
    max_tool_calls: int = 6
    enable_phase7_context: bool = False
    enable_phase7_compression: bool = False
    phase7_summarizer_model: Optional[str] = None


class StructureTrader:
    def __init__(
        self,
        *,
        agent_id: str = "structure_trader",
        config: StructureTraderConfig,
        tools_context: Optional[ToolContext] = None,
        allowed_tools: Optional[list[str]] = None,
    ):
        self.agent_id = agent_id
        self.config = config
        self.tools_context = tools_context

        if allowed_tools is None:
            allowed_tools = [
                "get_market_brief",
                "get_funding_oi_history",
                "get_orderbook_top",
                "get_candles",
                "get_indicator_pack",
                "get_position_summary",
                "get_firm_state",
                "query_memory",
            ]

        self._base = BaseTrader(
            agent_id=self.agent_id,
            role_prompt=STRUCTURE_ROLE_PROMPT,
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


__all__ = ["StructureTrader", "StructureTraderConfig", "STRUCTURE_ROLE_PROMPT"]

