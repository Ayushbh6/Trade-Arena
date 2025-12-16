"""Agent package.

Phase 2 introduces core contracts (schemas), base traders, and tools.
"""

from src.agents.schemas import (  # noqa: F401
    DecisionType,
    OrderType,
    Side,
    Timeframe,
    TradeAction,
    TradeIdea,
    TradeProposal,
    ManagerDecision,
    DecisionItem,
)

try:  # pragma: no cover
    # Optional convenience exports (may require heavier deps like OpenRouter SDK).
    from src.agents.manager import ManagerAgent, ManagerConfig  # noqa: F401
except Exception:  # pragma: no cover
    ManagerAgent = None  # type: ignore[assignment]
    ManagerConfig = None  # type: ignore[assignment]
