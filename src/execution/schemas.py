"""Execution-layer schemas (no LLM involvement).

These models represent the *intent* to place orders, not the exchange-native
order payloads. The executor will translate intents to Binance Futures params
using live mark price + exchange filters.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderLeg(str, Enum):
    entry = "entry"
    stop_loss = "stop_loss"
    take_profit = "take_profit"


class ExecutionOrderType(str, Enum):
    market = "market"
    limit = "limit"
    stop_market = "stop_market"
    take_profit_market = "take_profit_market"


class OrderSide(str, Enum):
    buy = "BUY"
    sell = "SELL"


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    intent_id: str = Field(..., description="Stable internal identifier.")
    client_order_id: str = Field(
        ..., description="Stable exchange client order id (idempotency key)."
    )

    run_id: Optional[str] = None
    cycle_id: Optional[str] = None
    agent_id: Optional[str] = None
    trade_index: Optional[int] = Field(None, ge=0)

    symbol: str
    leg: OrderLeg
    side: OrderSide
    order_type: ExecutionOrderType

    notional_usdt: float = Field(..., gt=0, description="Desired notional in USDT.")
    leverage: Optional[float] = Field(None, gt=0, description="Desired leverage.")

    limit_price: Optional[float] = Field(None, gt=0)
    trigger_price: Optional[float] = Field(
        None, gt=0, description="Used for stop/take-profit market orders."
    )
    reduce_only: bool = Field(
        False, description="When true, executor must send reduceOnly/closePosition."
    )
    time_in_force: Optional[str] = Field(
        None, description="E.g. GTC for limit orders."
    )

    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_prices(self) -> "OrderIntent":
        if self.order_type == ExecutionOrderType.limit:
            if self.limit_price is None:
                raise ValueError("limit_price required when order_type=limit")
            if self.time_in_force is None:
                raise ValueError("time_in_force required when order_type=limit")
        else:
            if self.limit_price is not None:
                raise ValueError("limit_price must be omitted unless order_type=limit")
            if self.time_in_force is not None:
                raise ValueError("time_in_force must be omitted unless order_type=limit")

        if self.order_type in {
            ExecutionOrderType.stop_market,
            ExecutionOrderType.take_profit_market,
        }:
            if self.trigger_price is None:
                raise ValueError("trigger_price required for stop/take-profit market orders")
        else:
            if self.trigger_price is not None:
                raise ValueError("trigger_price must be omitted unless stop/take-profit order")

        if self.leg == OrderLeg.entry and self.reduce_only:
            raise ValueError("entry leg must not be reduce_only")

        if self.leg in {OrderLeg.stop_loss, OrderLeg.take_profit} and not self.reduce_only:
            raise ValueError("stop/take-profit legs must be reduce_only")

        return self


class OrderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: Optional[str] = None
    cycle_id: Optional[str] = None
    manager_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    intents: List[OrderIntent] = Field(default_factory=list)
    notes: Optional[str] = None


class ExecutionStatus(str, Enum):
    placed = "placed"
    already_exists = "already_exists"
    failed = "failed"
    skipped = "skipped"


class OrderExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    intent_id: str
    client_order_id: str
    symbol: str
    leg: OrderLeg
    status: ExecutionStatus
    exchange_order_id: Optional[int] = None
    error: Optional[str] = None


class ExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: Optional[str] = None
    cycle_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    results: List[OrderExecutionResult] = Field(default_factory=list)
    notes: Optional[str] = None


def make_client_order_id(
    *,
    run_id: Optional[str],
    cycle_id: Optional[str],
    agent_id: Optional[str],
    trade_index: Optional[int],
    leg: str,
    symbol: str,
) -> str:
    """Create a deterministic, short idempotency key suitable for Binance.

    Binance futures newClientOrderId has a maximum length; keep this <= 32 chars.
    """

    seed = f"{run_id}|{cycle_id}|{agent_id}|{trade_index}|{leg}|{symbol}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()  # nosec - non-crypto use
    return "o_" + digest[:28]
