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

from src.agents.manager import ManagerAgent, ManagerConfig  # noqa: F401
