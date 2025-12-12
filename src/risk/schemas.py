"""Pydantic contracts for risk & governance.

Risk components must be deterministic and strictly JSON-serializable.
These schemas are used for:
- Compliance reports produced by the rule/validator layer
- Structured-output JSON schema hints for the Manager (Phase 3.2+)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ViolationSeverity(str, Enum):
    hard = "hard"
    soft = "soft"


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    rule_id: str = Field(..., min_length=1, description="Stable rule identifier.")
    severity: ViolationSeverity = Field(..., description="hard or soft.")
    message: str = Field(..., min_length=1, description="Human-readable violation.")

    agent_id: Optional[str] = Field(None, description="Owning agent, if known.")
    symbol: Optional[str] = Field(None, description="Affected symbol, if applicable.")
    trade_index: Optional[int] = Field(
        None, ge=0, description="Index into TradeProposal.trades, if applicable."
    )

    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured context (must be JSON-serializable).",
    )


class ResizeSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    symbol: str = Field(..., min_length=1)
    trade_index: Optional[int] = Field(None, ge=0)

    suggested_size_usdt: Optional[float] = Field(
        None, gt=0, description="Suggested resized notional (USDT)."
    )
    suggested_size_mult: Optional[float] = Field(
        None,
        gt=0,
        le=1.0,
        description="Suggested size multiplier (<=1.0) when resizing down.",
    )
    suggested_leverage: Optional[float] = Field(
        None, gt=0, description="Suggested leverage after resize."
    )

    reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_has_resize(self) -> "ResizeSuggestion":
        if (
            self.suggested_size_usdt is None
            and self.suggested_size_mult is None
            and self.suggested_leverage is None
        ):
            raise ValueError(
                "at least one of suggested_size_usdt/suggested_size_mult/"
                "suggested_leverage must be provided"
            )
        return self


class ComplianceReport(BaseModel):
    """Deterministic compliance report for a single agent proposal."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    agent_id: str = Field(..., min_length=1)
    run_id: Optional[str] = None
    cycle_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    hard_violations: List[Violation] = Field(default_factory=list)
    soft_violations: List[Violation] = Field(default_factory=list)
    resize_suggestions: List[ResizeSuggestion] = Field(default_factory=list)

    hard_fail: bool = Field(
        False, description="True when hard_violations is non-empty."
    )
    passed: bool = Field(
        True,
        description="True when the proposal is eligible for approval/resize (no hard fails).",
    )
    notes: Optional[str] = Field(None, description="Optional compliance summary.")

    @model_validator(mode="after")
    def _sync_flags(self) -> "ComplianceReport":
        expected_hard_fail = len(self.hard_violations) > 0
        if self.hard_fail != expected_hard_fail:
            raise ValueError("hard_fail must match whether hard_violations is non-empty")

        expected_passed = not expected_hard_fail
        if self.passed != expected_passed:
            raise ValueError("passed must be false when hard_violations exist")

        return self


T = TypeVar("T", bound=BaseModel)


def export_json_schema(model: Type[T]) -> Dict[str, Any]:
    """Export JSON schema for structured output hints."""
    return model.model_json_schema()

