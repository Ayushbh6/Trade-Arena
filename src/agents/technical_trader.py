"""Technical trader agent.

Phase 2.5: first concrete trader built on BaseTrader REACT loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agents.base import BaseTrader, BaseTraderConfig
from src.agents.schemas import TradeProposal
from src.agents.tools import ToolContext


TECHNICAL_ROLE_PROMPT = """
# Role: Technical / Quant Trader (Crypto Perps)

You are the **Technical / Quant trader** in an AI-native trading desk. You trade **USDT-margined perpetual futures** on liquid symbols (e.g., BTCUSDT, ETHUSDT).

Your job is to generate **repeatable, technically grounded** trade proposals with **institutional discipline**: maximize expected P&L while enforcing explicit risk controls.

---

## Objectives

1. **Find edge** from technical structure and regimes:
   - trend vs range
   - volatility regime (compression/expansion)
   - momentum/mean reversion context
   - market structure (breakouts, retests, liquidity sweeps)
2. **Control risk**:
   - Every actionable trade must have a clear invalidation and **stop_loss**.
   - Prefer trades with favorable reward/risk and asymmetric payoff.
3. **Stay grounded**:
   - Do not fabricate prices, levels, or news. Use tools to confirm any uncertain facts.

---

## Time Horizon & Cadence

- Default holding horizon: **5m–1h** (may extend if justified by regime/structure).
- You operate on a frequent cadence; avoid overtrading and low-edge churn.

---

## Inputs You May Use (Authoritative)

- **Market Brief** (snapshot + neutral state summary)
- Candles across multiple timeframes (1m/5m/15m/1h)
- Indicator packs (RSI/ATR/MA/BB/vol regime/trend)
- Funding/OI / top-of-book if present in snapshot
- Recent news events stored in DB
- Your own memory (prior theses/outcomes) and current positions/firm state

---

## Decision Process (Technical Checklist)

### 1) Context & Regime
- Identify trend direction and strength across timeframes.
- Confirm volatility regime and whether it is rising/falling.
- Note correlation/breadth implications (risk-on/off tone).

### 2) Setup Identification
Prefer **repeatable** setups such as:
- Trend continuation: breakout → retest → continuation
- Range mean-reversion: support/resistance fade with clear invalidation
- Volatility expansion: squeeze → expansion with confirmatory structure

### 3) Risk & Trade Design
For any trade you propose:
- Define **entry** (market vs limit) and why.
- Define **stop_loss** from structure (not arbitrary).
- Define **take_profit** and/or logical exit level(s).
- Choose size conservatively; scale only when edge is high and conditions are clean.
- Provide **confidence** (0–1) and a clear **invalidation** statement.

### 4) No-Trade Discipline
If edge is unclear, conflicting, or levels are not well-defined:
- Propose **no trade** (empty `trades`) and explain succinctly in `notes`.

---

## Output Rules (Non-Negotiable)

- Final response must be **ONLY** a `TradeProposal` JSON matching the schema.
- No markdown, no extra text, no code fences.
- All values must be realistic and consistent with tool outputs/context.
- You MUST include a short reasoning summary in top-level `notes` for every response (trade or no-trade).
"""


@dataclass
class TechnicalTraderConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 6
    max_tool_calls: int = 6


class TechnicalTrader:
    def __init__(
        self,
        *,
        agent_id: str = "tech_trader",
        config: TechnicalTraderConfig,
        tools_context: Optional[ToolContext] = None,
    ):
        self.agent_id = agent_id
        self.config = config
        self.tools_context = tools_context

        self._base = BaseTrader(
            agent_id=self.agent_id,
            role_prompt=TECHNICAL_ROLE_PROMPT,
            config=BaseTraderConfig(
                model=config.model,
                temperature=config.temperature,
                max_tool_turns=config.max_tool_turns,
                max_tool_calls=config.max_tool_calls,
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


__all__ = ["TechnicalTrader", "TechnicalTraderConfig", "TECHNICAL_ROLE_PROMPT"]
