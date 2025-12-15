"""Grounded ledger models (exact + low-drift).

Phase 7: The Ledger is the non-negotiable memory substrate that should remain
stable over long runs. It contains:
- Facts: deterministically rebuilt from MongoDB (source of truth).
- Soft state: LLM-maintained watchlist + lessons (may be updated by summarizer).

Mongo facts always override any conflicting LLM updates.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LessonVerdict(str, Enum):
    right = "right"
    wrong = "wrong"
    mixed = "mixed"
    unclear = "unclear"


class LedgerPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    symbol: str = Field(..., min_length=1)
    qty: float
    avg_entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float

    as_of: datetime = Field(default_factory=datetime.utcnow)
    source_ref: Optional[Dict[str, Any]] = Field(
        None, description="Mongo reference to the underlying positions document."
    )


class LedgerFacts(BaseModel):
    """Deterministic facts that must match MongoDB/exchange reality."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    capital_usdt: Optional[float] = None
    agent_budget_usdt: Optional[float] = None
    trust_score: Optional[float] = None

    positions: List[LedgerPosition] = Field(default_factory=list)

    # Minimal recent outcome ledger (deterministic summary; details in Mongo).
    recent_outcomes: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Last N outcomes derived from Mongo facts (proposal/decision/fills/pnl refs).",
    )

    as_of: datetime = Field(default_factory=datetime.utcnow)


class WatchlistItem(BaseModel):
    """LLM-maintained, bounded list of active hypotheses."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    item_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    thesis: str = Field(..., min_length=1)
    invalidation: Optional[str] = None
    horizon: Optional[str] = Field(None, description="E.g. 1h, 4h, 1d (freeform).")
    priority: int = Field(3, ge=1, le=5, description="1=low, 5=high")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    source_cycle_ids: List[str] = Field(default_factory=list)


class LessonItem(BaseModel):
    """LLM-maintained, bounded list of last lessons learned."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    item_id: str = Field(..., min_length=1)
    cycle_id: Optional[str] = None
    symbol: Optional[str] = None
    verdict: LessonVerdict = LessonVerdict.unclear
    lesson: str = Field(..., min_length=1)
    source_refs: List[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class Ledger(BaseModel):
    """Top-level ledger for an agent within a run."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)

    facts: LedgerFacts = Field(default_factory=LedgerFacts)
    watchlist: List[WatchlistItem] = Field(default_factory=list, max_length=50)
    lessons_last_5: List[LessonItem] = Field(default_factory=list, max_length=5)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(1, ge=1)


__all__ = [
    "LessonVerdict",
    "LedgerPosition",
    "LedgerFacts",
    "WatchlistItem",
    "LessonItem",
    "Ledger",
]

