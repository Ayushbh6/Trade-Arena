from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any

class AgentOutput(BaseModel):
    thought: str = Field(..., description="Your step-by-step reasoning or plan for what you are about to do. MUST be detailed.")
    action: Literal["code", "final_answer"] = Field(..., description="The next step. Use 'code' to execute Python or 'final_answer' to provide the conclusion.")
    code: Optional[str] = Field(None, description="The Python code to execute if action is 'code'.")
    final_answer: Optional[str] = Field(None, description="The final response to the user if action is 'final_answer'.")

class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class AgentEvent(BaseModel):
    type: Literal["thought", "code", "observation", "tool_call", "tool_result", "decision", "error", "info", "memory"]
    source: Literal["manager", "quant", "system"]
    content: str
    metadata: Optional[Dict[str, Any]] = None
    usage: Optional[TokenUsage] = None
