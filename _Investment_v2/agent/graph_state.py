from typing import TypedDict, List, Optional, Dict, Any, Union
from agent.schema import AgentEvent, QuantReport, PortfolioDecision, AgentMemory, Plan

class MarketData(TypedDict):
    portfolio: Dict[str, Any]
    prices: Dict[str, Any]

class AgentState(TypedDict):
    """
    Represents the full state of the Agent Cycle.
    Passed between nodes in the State Graph.
    """
    # Inputs
    instruction: str
    
    # State Data
    messages: List[Dict[str, Any]]     # Chat history for Manager
    market_data: Optional[MarketData]  # Latest market info
    plan: Optional[Plan]               # Structured plan from Manager
    quant_report: Optional[QuantReport] # Structured output from Quant
    decision: Optional[PortfolioDecision] # Structured output from Manager
    memory: Optional[AgentMemory]      # End of cycle memory
    
    # Control Flow
    current_node: str                  # Current state name
    error: Optional[str]               # Last error (for retry logic)
    retry_count: int                   # To prevent infinite loops
    verbose: bool
    run_id: Optional[str]              # Graph run identifier
    session_id: Optional[str]          # Optional DB session id
    cycle_id: Optional[str]            # Optional DB cycle id
