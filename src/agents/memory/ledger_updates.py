"""Structured ledger updates for Phase 7 summarizer.

Spec intent (Plan/ai-native-trader-comp.md):
- Summarizer returns `ledger_updates` + `new_narrative_summary`.
- Ledger facts are source-of-truth from MongoDB and MUST NOT be modified here.

Implementation scope:
- Updates apply only to soft ledger sections:
  - watchlist
  - lessons_last_5
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Annotated, Dict, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .ledger import Ledger, LessonItem, WatchlistItem


class WatchlistUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    op_type: Literal["watchlist_upsert"]
    item: WatchlistItem


class WatchlistRemove(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    op_type: Literal["watchlist_remove"]
    item_id: str = Field(..., min_length=1)


class LessonUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    op_type: Literal["lesson_upsert"]
    item: LessonItem


class LessonRemove(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    op_type: Literal["lesson_remove"]
    item_id: str = Field(..., min_length=1)


LedgerUpdate = Annotated[
    Union[WatchlistUpsert, WatchlistRemove, LessonUpsert, LessonRemove],
    Field(discriminator="op_type"),
]


def _upsert_by_id(items: List[BaseModel], item_id: str, new_item: BaseModel) -> List[BaseModel]:
    out: List[BaseModel] = []
    replaced = False
    for it in items:
        if getattr(it, "item_id", None) == item_id:
            out.append(new_item)
            replaced = True
        else:
            out.append(it)
    if not replaced:
        out.append(new_item)
    return out


def _remove_by_id(items: List[BaseModel], item_id: str) -> List[BaseModel]:
    return [it for it in items if getattr(it, "item_id", None) != item_id]


def apply_ledger_updates(*, ledger: Ledger, updates: List[LedgerUpdate]) -> Dict[str, Any]:
    """Apply soft-ledger updates in-place; never touch `ledger.facts`.

    Returns a small stats dict for optional logging/debugging.
    """
    stats = {
        "watchlist_upserts": 0,
        "watchlist_removes": 0,
        "lesson_upserts": 0,
        "lesson_removes": 0,
        "ignored": 0,
    }

    now = datetime.utcnow()

    watch: List[WatchlistItem] = list(ledger.watchlist or [])
    lessons: List[LessonItem] = list(ledger.lessons_last_5 or [])

    for u in updates or []:
        op = getattr(u, "op_type", None)
        if op == "watchlist_upsert":
            item = u.item  # type: ignore[attr-defined]
            # Ensure timestamps move forward for updated items.
            item.updated_at = now  # type: ignore[misc]
            watch = _upsert_by_id(watch, item.item_id, item)  # type: ignore[arg-type]
            stats["watchlist_upserts"] += 1
            continue
        if op == "watchlist_remove":
            item_id = u.item_id  # type: ignore[attr-defined]
            watch = _remove_by_id(watch, item_id)  # type: ignore[arg-type]
            stats["watchlist_removes"] += 1
            continue
        if op == "lesson_upsert":
            item = u.item  # type: ignore[attr-defined]
            lessons = _upsert_by_id(lessons, item.item_id, item)  # type: ignore[arg-type]
            stats["lesson_upserts"] += 1
            continue
        if op == "lesson_remove":
            item_id = u.item_id  # type: ignore[attr-defined]
            lessons = _remove_by_id(lessons, item_id)  # type: ignore[arg-type]
            stats["lesson_removes"] += 1
            continue
        stats["ignored"] += 1

    # Enforce invariants (bounded).
    ledger.watchlist = list(watch)[-50:]  # type: ignore[misc]
    ledger.lessons_last_5 = list(lessons)[-5:]  # type: ignore[misc]

    ledger.updated_at = now  # type: ignore[misc]
    return stats


__all__ = [
    "LedgerUpdate",
    "WatchlistUpsert",
    "WatchlistRemove",
    "LessonUpsert",
    "LessonRemove",
    "apply_ledger_updates",
]
