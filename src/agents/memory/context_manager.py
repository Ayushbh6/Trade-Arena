"""Context manager (sliding window + ledger + narrative).

Phase 7 goal: Build long-running prompts with:
- Pinned preamble (role + schema + safety) [not stored here]
- Current context (latest brief + firm state + last decision) [provided per call]
- Instant memory: raw recent QnA turns (bounded by tokens)
- Ledger: exact grounded memory (never compressed; facts rebuilt from Mongo)
- Narrative summary: compressible older history

This module defines the persistent ContextState contract and budgeting knobs.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .ledger import Ledger

try:  # pragma: no cover
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None


class TurnRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    role: TurnRole
    content: str = Field(..., min_length=1)

    cycle_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PromptBudget(BaseModel):
    """Token budgeting knobs for prompt construction."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    target_total_tokens: int = Field(80000, ge=1000)
    max_prompt_tokens: int = Field(75000, ge=1000)

    # Raw instant QnA token budget. Older raw turns are moved into narrative pool.
    max_instant_tokens: int = Field(40000, ge=1000)

    # Guardrails to prevent runaway loops.
    max_compression_rounds: int = Field(3, ge=0, le=10)


class ContextState(BaseModel):
    """Persisted per-(run_id, agent_id) memory state."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)

    ledger: Ledger
    narrative_summary: str = Field("", description="Compressed older history.")
    instant_turns: List[ConversationTurn] = Field(
        default_factory=list, description="Recent raw QnA turns (sliding window)."
    )

    budget: PromptBudget = Field(default_factory=PromptBudget)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(1, ge=1)


def new_context_state(*, run_id: str, agent_id: str, budget: Optional[PromptBudget] = None) -> ContextState:
    """Create a fresh ContextState with an empty ledger."""
    b = budget or PromptBudget()
    ledger = Ledger(run_id=run_id, agent_id=agent_id)
    return ContextState(run_id=run_id, agent_id=agent_id, ledger=ledger, budget=b)


def count_tokens(text: str) -> int:
    """Approximate token count for budgeting.

    Uses cl100k_base when tiktoken is available; otherwise falls back to a simple heuristic.
    """
    if not text:
        return 0
    if tiktoken is None:
        return max(1, len(text) // 4)
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def render_instant_transcript(turns: List[ConversationTurn]) -> str:
    """Render raw QnA turns into a plain text transcript for prompting."""
    lines: List[str] = []
    for t in turns:
        role = "User" if t.role == TurnRole.user else "Assistant"
        prefix = f"[{t.cycle_id}] " if t.cycle_id else ""
        lines.append(f"{prefix}{role}: {t.content}")
    return "\n".join(lines).strip()


def trim_instant_turns_to_budget(
    *,
    turns: List[ConversationTurn],
    max_tokens: int,
) -> Tuple[List[ConversationTurn], List[ConversationTurn]]:
    """Trim oldest turns until transcript fits within max_tokens.

    Returns (kept, dropped_oldest).
    """
    kept = list(turns)
    dropped: List[ConversationTurn] = []
    while kept and count_tokens(render_instant_transcript(kept)) > max_tokens:
        dropped.append(kept.pop(0))
    return kept, dropped


def render_ledger_for_prompt(ledger: Ledger) -> str:
    """Render ledger as compact JSON for prompt inclusion.

    This renderer may deterministically trim *soft* ledger sections to keep
    prompt size bounded. Mongo facts are still sourced elsewhere.
    """
    # Deterministic trimming for prompt safety (does not mutate the ledger object).
    payload = ledger.model_dump(mode="json")

    # Soft sections: bounded for prompt inclusion.
    watch = list(payload.get("watchlist") or [])
    lessons = list(payload.get("lessons_last_5") or [])
    facts = payload.get("facts") or {}
    pos = list((facts.get("positions") or [])) if isinstance(facts, dict) else []
    outcomes = list((facts.get("recent_outcomes") or [])) if isinstance(facts, dict) else []

    # Defaults: keep a reasonably rich but bounded view.
    payload["watchlist"] = watch[:20]
    payload["lessons_last_5"] = lessons[:5]
    if isinstance(facts, dict):
        facts["positions"] = pos[:20]
        facts["recent_outcomes"] = outcomes[:10]
        payload["facts"] = facts

    return json.dumps(payload, ensure_ascii=False, default=str)


def enforce_max_prompt_tokens(
    *,
    system_text: str,
    base_user_text: str,
    ledger_json: str,
    narrative_summary: str,
    instant_turns: List[ConversationTurn],
    max_prompt_tokens: int,
) -> Tuple[str, str, List[ConversationTurn]]:
    """Ensure total prompt stays under max_prompt_tokens by trimming memory blocks.

    Deterministic policy (in order):
    1) Drop oldest instant turns (move into narrative)
    2) If still too large, truncate narrative (keep tail)
    3) If still too large, shrink ledger *prompt view* (last resort)

    Ledger is treated as non-negotiable and is not modified here.
    """
    kept_turns = list(instant_turns)
    narrative = narrative_summary or ""
    ledger_view = ledger_json or ""

    def _total_tokens() -> int:
        # Approximate total tokens for the system + user content blocks.
        parts = [
            system_text or "",
            base_user_text or "",
            ledger_view,
            narrative or "",
            render_instant_transcript(kept_turns),
        ]
        return sum(count_tokens(p) for p in parts if p)

    # If fixed parts already exceed budget, caller must shrink market_brief (or pinned/system).
    if _total_tokens() > max_prompt_tokens and not kept_turns and not narrative and not ledger_view:
        raise ValueError("system_text + base_user_text exceed max_prompt_tokens")

    # First, drop oldest instant turns until under budget.
    while kept_turns and _total_tokens() > max_prompt_tokens:
        dropped = kept_turns.pop(0)
        prefix = f"[{dropped.cycle_id}] " if dropped.cycle_id else ""
        narrative = (narrative + "\n" if narrative else "") + f"{prefix}{dropped.role}: {dropped.content}"

    # If still too large, truncate narrative aggressively.
    # Keep the tail because it's closer to the recent context.
    if _total_tokens() > max_prompt_tokens and narrative:
        # Keep last ~5000 tokens worth of text (best-effort).
        # We truncate by characters with an iterative backoff.
        target = 5000
        s = narrative
        for _ in range(12):
            if count_tokens(s) <= target:
                break
            s = s[len(s) // 2 :]
        narrative = s

    # If still too large, shrink the ledger prompt view.
    # This does NOT mutate the stored Ledger; it only reduces what we include in the prompt.
    if _total_tokens() > max_prompt_tokens and ledger_view:
        # Replace with a minimal placeholder + a small tail snippet to preserve some context.
        tail = ledger_view[-4000:]
        ledger_view = json.dumps(
            {
                "_omitted": True,
                "reason": "ledger_too_large_for_prompt_budget",
                "note": "Ledger was truncated for prompt budgeting. Mongo facts remain the source of truth.",
                "kept_tail": tail,
            },
            ensure_ascii=False,
            default=str,
        )
        # If still too large (extreme case), drop tail.
        if _total_tokens() > max_prompt_tokens:
            ledger_view = json.dumps(
                {
                    "_omitted": True,
                    "reason": "ledger_too_large_for_prompt_budget",
                    "note": "Ledger omitted for prompt budgeting. Mongo facts remain the source of truth.",
                },
                ensure_ascii=False,
                default=str,
            )

    # If still too large, we cannot satisfy budget without shrinking base_user_text/system.
    if _total_tokens() > max_prompt_tokens:
        raise ValueError("Unable to enforce max_prompt_tokens; fixed prompt parts too large.")

    return ledger_view, narrative, kept_turns


__all__ = [
    "TurnRole",
    "ConversationTurn",
    "PromptBudget",
    "ContextState",
    "new_context_state",
    "count_tokens",
    "render_instant_transcript",
    "trim_instant_turns_to_budget",
    "render_ledger_for_prompt",
    "enforce_max_prompt_tokens",
]
