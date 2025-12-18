from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

class PortfolioSnapshot(BaseModel):
    total_usdt: float
    positions: Dict[str, float] # e.g. {"BTC": 0.1}
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AgentMemory(BaseModel):
    """The structured memory passed between cycles."""
    short_term_summary: str = Field(..., description="A concise narrative of what happened in the last cycle.")
    active_hypotheses: List[str] = Field(..., description="List of current market theories being tested.")
    pending_orders: List[str] = Field(default=[], description="Orders that were placed but not yet confirmed filled (if any).")
    next_steps: str = Field(..., description="What the agent plans to do in the next cycle.")
    
class CycleLog(BaseModel):
    """Record of a single execution cycle."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    cycle_number: int
    start_time: datetime = Field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    events: List[Dict[str, Any]] = [] # The raw AgentEvents
    memory_generated: Optional[AgentMemory] = None
    portfolio_after: Optional[PortfolioSnapshot] = None

class TradingSession(BaseModel):
    """Represents a continuous run (e.g., a day)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    start_time: datetime = Field(default_factory=datetime.utcnow)
    status: str = "active" # active, stopped, completed
    config: Dict[str, Any] = {}
    initial_balance: float = 0.0
    current_balance: float = 0.0
