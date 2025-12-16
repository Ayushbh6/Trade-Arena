"""LLM-based narrative summarizer for Phase 7.

This is used only to compress older history (narrative) and update *soft* ledger
sections:
- watchlist (LLM-maintained)
- lessons_last_5 (LLM-maintained)

Mongo facts are the source of truth and must never be modified by the summarizer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from Utils.openrouter import chat_completion_raw
from src.agents.memory.ledger import LessonItem, WatchlistItem
from src.agents.memory.ledger_updates import LedgerUpdate
from src.data.mongo import jsonify


class SummarizeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    new_narrative_summary: str = Field(..., min_length=1)
    # Spec: summarizer emits structured updates for the (soft) ledger sections.
    # Facts are sourced from Mongo and never updated by the summarizer.
    ledger_updates: List[LedgerUpdate] = Field(default_factory=list)

    # Back-compat: older implementation returned full lists; still accepted.
    watchlist: Optional[List[WatchlistItem]] = Field(default=None, max_length=50)
    lessons_last_5: Optional[List[LessonItem]] = Field(default=None, max_length=5)
    notes: Optional[str] = None


@dataclass(frozen=True)
class SummarizerConfig:
    model: str
    temperature: float = 0.0


def summarize_narrative(
    *,
    config: SummarizerConfig,
    agent_id: str,
    run_id: str,
    existing_narrative_summary: str,
    appended_old_transcript: str,
    current_watchlist: List[Dict[str, Any]],
    current_lessons_last_5: List[Dict[str, Any]],
) -> SummarizeResult:
    """Call an LLM to compress narrative and update soft ledger sections.

    This function is synchronous because the OpenRouter SDK wrapper is sync for
    structured outputs (and BaseTrader uses sync calls for LLM anyway).
    """
    schema = SummarizeResult.model_json_schema()

    system = {
        "role": "system",
        "content": (
            "You are a memory summarizer for a long-running trading agent.\n"
            "Task:\n"
            "- Compress older chat history into a compact narrative summary.\n"
            "- Propose structured ledger updates for ONLY the soft ledger sections: watchlist and lessons_last_5.\n"
            "Hard rules:\n"
            "- Do NOT modify any factual state like positions/balances/exposure.\n"
            "- Keep watchlist to <=50 items and lessons_last_5 to exactly the most relevant 5 or fewer.\n"
            "- Each watchlist item must have a stable item_id (string).\n"
            "- Each lesson must have an item_id and a clear 'lesson' sentence.\n"
            "- Use ledger_updates operations (op_type in {watchlist_upsert, watchlist_remove, lesson_upsert, lesson_remove}).\n"
            "- Output ONLY JSON matching the provided schema. No markdown. No extra keys.\n"
        ),
    }

    user_payload: Dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "existing_narrative_summary": existing_narrative_summary,
        "appended_old_transcript": appended_old_transcript,
        "current_soft_state": {
            "watchlist": current_watchlist,
            "lessons_last_5": current_lessons_last_5,
        },
    }

    messages = [
        system,
        {"role": "user", "content": json.dumps(jsonify(user_payload), ensure_ascii=False, default=str)},
    ]

    res = chat_completion_raw(
        messages=messages,
        model=config.model,
        output_schema=schema,
        schema_name="SummarizeResult",
        strict_json=True,
        temperature=config.temperature,
    )
    msg = res.choices[0].message
    content = getattr(msg, "content", None) or msg.get("content")  # type: ignore[union-attr]
    if not isinstance(content, str):
        raise RuntimeError("Summarizer returned non-text content")
    try:
        return SummarizeResult.model_validate_json(content)
    except Exception:
        # Fallback: tolerate some providers returning json_object but with minor issues.
        obj = json.loads(content, strict=False)
        return SummarizeResult.model_validate(obj)


__all__ = ["SummarizerConfig", "SummarizeResult", "summarize_narrative"]
