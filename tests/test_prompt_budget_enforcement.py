"""Sanity test for full prompt max token enforcement (no network).

Run:
  python tests/test_prompt_budget_enforcement.py

This validates the deterministic trimming policy used to keep prompts under
max_prompt_tokens across system + base user + ledger + narrative + instant turns.
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.context_manager import (  # noqa: E402
    ConversationTurn,
    TurnRole,
    count_tokens,
    enforce_max_prompt_tokens,
    render_instant_transcript,
)


def main() -> None:
    print("== Phase 7 full prompt budget enforcement sanity test ==")

    # Keep fixed blocks reasonably small; in production the orchestrator should
    # supply a thin/compact market brief. This test focuses on trimming memory.
    system_text = "SYSTEM " + ("rules " * 200)
    base_user_text = "MARKET_BRIEF " + ("data " * 400)
    ledger_json = '{"ledger":"x"}' * 5000
    narrative = "NARRATIVE " + ("old " * 20000)

    turns = []
    for i in range(120):
        turns.append(ConversationTurn(role=TurnRole.user, content=("u " * 200), cycle_id=f"c{i}"))
        turns.append(ConversationTurn(role=TurnRole.assistant, content=("a " * 200), cycle_id=f"c{i}"))

    max_prompt_tokens = 7500
    before = sum(
        count_tokens(p)
        for p in [
            system_text,
            base_user_text,
            ledger_json,
            narrative,
            render_instant_transcript(turns),
        ]
    )
    print(f"[INFO] before_tokens={before} max_prompt_tokens={max_prompt_tokens}")
    assert before > max_prompt_tokens

    new_ledger_json, new_narrative, kept_turns = enforce_max_prompt_tokens(
        system_text=system_text,
        base_user_text=base_user_text,
        ledger_json=ledger_json,
        narrative_summary=narrative,
        instant_turns=turns,
        max_prompt_tokens=max_prompt_tokens,
    )

    after = sum(
        count_tokens(p)
        for p in [
            system_text,
            base_user_text,
            new_ledger_json,
            new_narrative,
            render_instant_transcript(kept_turns),
        ]
        if p
    )
    print(f"[OK] after_tokens={after} kept_turns={len(kept_turns)}")
    assert after <= max_prompt_tokens, "prompt must be under max tokens after enforcement"
    assert len(kept_turns) < len(turns), "expected some instant turns to be trimmed"
    assert new_narrative, "narrative should remain non-empty (even if truncated)"

    print("[PASS] Full prompt budget enforcement checks passed.")

    # Configuration-error case: if fixed prompt parts exceed budget, enforcement must fail.
    try:
        enforce_max_prompt_tokens(
            system_text=("x " * 2000),
            base_user_text=("y " * 2000),
            ledger_json="",
            narrative_summary="",
            instant_turns=[],
            max_prompt_tokens=100,
        )
        raise AssertionError("expected ValueError for impossible prompt budget")
    except ValueError:
        print("[PASS] Impossible-budget case correctly raises ValueError.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
