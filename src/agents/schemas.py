"""Pydantic contracts for trader/manager agents.

All agent I/O must be strictly JSON-serializable and validated here.
These schemas are used both for runtime validation and for structured-output
JSON schema hints to OpenRouter models.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Side(str, Enum):
    long = "long"
    short = "short"


class OrderType(str, Enum):
    market = "market"
    limit = "limit"


class Timeframe(str, Enum):
    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    h1 = "1h"
    h4 = "4h"
    d1 = "1d"


class TradeAction(str, Enum):
    open = "open"
    add = "add"
    reduce = "reduce"
    close = "close"


class TradeIdea(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    symbol: str = Field(..., description="Exchange symbol, e.g., BTCUSDT")
    side: Side = Field(..., description="long or short")
    action: TradeAction = Field(..., description="open/add/reduce/close")

    size_usdt: float = Field(
        ...,
        gt=0,
        description="Notional size in USDT for this action.",
    )
    leverage: Optional[float] = Field(
        None,
        gt=0,
        description="Per-position leverage. Omit to use default.",
    )

    order_type: OrderType = Field(OrderType.market, description="market or limit")
    limit_price: Optional[float] = Field(
        None, gt=0, description="Required if order_type=limit."
    )

    stop_loss: Optional[float] = Field(
        None, gt=0, description="Stop loss price (optional)."
    )
    take_profit: Optional[float] = Field(
        None, gt=0, description="Take profit price (optional)."
    )

    time_horizon: Optional[Timeframe] = Field(
        None, description="Expected holding horizon."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Conviction 0-1.",
    )

    rationale: str = Field(..., min_length=1, description="Reasoning/thesis.")
    invalidation: Optional[str] = Field(
        None, description="What would invalidate this trade."
    )
    tags: List[str] = Field(default_factory=list, description="Optional tags.")

    @model_validator(mode="after")
    def _validate_limit_price(self) -> "TradeIdea":
        if self.order_type == OrderType.limit and self.limit_price is None:
            raise ValueError("limit_price required when order_type=limit")
        if self.order_type == OrderType.market and self.limit_price is not None:
            raise ValueError("limit_price must be omitted when order_type=market")
        return self


class TradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    agent_id: str = Field(..., description="Trader agent identifier.")
    run_id: Optional[str] = Field(None, description="Orchestrator run id.")
    cycle_id: Optional[str] = Field(
        None, description="Cadence cycle identifier for this proposal."
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="UTC timestamp."
    )

    trades: List[TradeIdea] = Field(
        ..., min_length=0, description="One or more trade ideas."
    )
    notes: Optional[str] = Field(
        None, description="Optional extra notes for manager."
    )


class DecisionType(str, Enum):
    approve = "approve"
    resize = "resize"
    veto = "veto"
    defer = "defer"


class DecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    agent_id: Optional[str] = Field(
        None, description="Owning agent_id for the proposal that this decision refers to."
    )
    trade_index: Optional[int] = Field(
        None, ge=0, description="Index into that agent's TradeProposal.trades (if applicable)."
    )
    symbol: str
    decision: DecisionType
    approved_size_usdt: Optional[float] = Field(
        None, gt=0, description="Final size after resize."
    )
    approved_leverage: Optional[float] = Field(
        None, gt=0, description="Final leverage after resize."
    )
    notes: Optional[str] = None


class TrustDelta(BaseModel):
    """Manager-suggested trust delta (informational only; allocator decides)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    agent_id: str = Field(..., description="Agent identifier.")
    delta: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Suggested trust delta in normalized units [-1, 1].",
    )
    reason: str = Field(..., min_length=1, description="Short justification.")


class ManagerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    manager_id: str = Field(..., description="Manager/CIO agent id.")
    run_id: Optional[str] = None
    cycle_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    decisions: List[DecisionItem] = Field(default_factory=list)
    notes: Optional[str] = Field(
        None, description="Manager reasoning summary for approvals/vetoes/resizes."
    )
    firm_notes: Optional[str] = None
    trust_deltas: List[TrustDelta] = Field(
        default_factory=list,
        description="Optional trust delta suggestions for the weekly allocator (informational only).",
    )


T = TypeVar("T", bound=BaseModel)


def export_json_schema(model: Type[T]) -> Dict[str, Any]:
    """Export JSON schema for structured output hints."""
    return model.model_json_schema()
