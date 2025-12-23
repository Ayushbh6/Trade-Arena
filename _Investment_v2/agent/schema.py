from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

class AgentOutput(BaseModel):
    thought: str = Field(..., description="Your step-by-step reasoning or plan for what you are about to do. MUST be detailed.")
    action: Literal["code", "final_answer"] = Field(..., description="The next step. Use 'code' to execute Python or 'final_answer' to provide the conclusion.")
    code: Optional[str] = Field(None, description="The Python code to execute if action is 'code'.")
    final_answer: Optional[Any] = Field(None, description="The final response. Can be string or structured object.")

class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class AgentEvent(BaseModel):
    type: Literal["thought", "code", "observation", "tool_call", "tool_result", "decision", "error", "info", "memory"]
    source: Literal["manager", "quant", "system"]
    content: Any
    metadata: Optional[Dict[str, Any]] = None
    usage: Optional[TokenUsage] = None
    timestamp: Optional[str] = None

# --- PHASE 2 SCHEMAS (STRUCTURED OUTPUTS) ---

from enum import Enum

class TradeAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

class MarketSignal(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"

class QuantReport(BaseModel):
    signal: MarketSignal
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(..., description="Detailed technical reasoning for the signal.")
    technical_indicators: Dict[str, Any] = Field(default_factory=dict, description="Key technical indicators (e.g., {'RSI': 70, 'SMA_50': 50000})")

class Plan(BaseModel):
    objective: str = Field(..., description="Manager's intent in one sentence.")
    assets: List[str] = Field(..., description="Assets to analyze, e.g., ['ETH/USDT'].")
    quant_question: str = Field(..., description="Explicit instruction for the Quant.")
    timeframes: List[str] = Field(default_factory=list, description="Timeframes to analyze, e.g., ['1h', '4h'].")
    constraints: Dict[str, Any] = Field(default_factory=dict, description="Risk constraints or notes.")
    expected_outputs: List[str] = Field(default_factory=list, description="Indicators/outputs needed, e.g., ['RSI', 'MACD'].")

class PortfolioDecision(BaseModel):
    action: TradeAction
    asset: str = Field(..., description="The asset symbol, e.g., 'BTC/USDT'")
    quantity: float = Field(..., description="Quantity to trade. 0.0 if HOLD.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Strategic reasoning for the decision.")
    strategy_used: str = Field(..., description="Name of the strategy applied.")

class AgentMemory(BaseModel):
    short_term_summary: str = Field(..., description="Concise narrative of the cycle.")
    active_hypotheses: List[str] = Field(default_factory=list)
    pending_orders: List[str] = Field(default_factory=list)
    next_steps: str = Field(..., description="Plan for the next cycle.")
