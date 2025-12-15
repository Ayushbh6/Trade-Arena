"""Sanity test for Phase 7 token budgeting (no network).

Run:
  python tests/test_context_budgeting.py

This test validates that trimming logic drops oldest instant turns until the
instant transcript fits within the configured token budget.
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.context_manager import (  # noqa: E402
    ConversationTurn,
    TurnRole,
    count_tokens,
    render_instant_transcript,
    trim_instant_turns_to_budget,
)


def main() -> None:
    print("== Phase 7 context budgeting sanity test ==")

    turns = []
    for i in range(60):
        turns.append(
            ConversationTurn(
                role=TurnRole.user,
                content=("user turn " + str(i) + " ") * 20,
                cycle_id=f"c{i}",
            )
        )
        turns.append(
            ConversationTurn(
                role=TurnRole.assistant,
                content=("assistant turn " + str(i) + " ") * 20,
                cycle_id=f"c{i}",
            )
        )

    original = render_instant_transcript(turns)
    original_tokens = count_tokens(original)
    print(f"[INFO] original turns={len(turns)} tokens={original_tokens}")
    assert original_tokens > 1000, "expected large transcript"

    budget = max(200, original_tokens // 4)
    kept, dropped = trim_instant_turns_to_budget(turns=turns, max_tokens=budget)
    kept_tokens = count_tokens(render_instant_transcript(kept))
    print(f"[OK] kept turns={len(kept)} dropped={len(dropped)} kept_tokens={kept_tokens} budget={budget}")

    assert kept_tokens <= budget, "kept transcript must fit within budget"
    assert dropped, "expected some turns to be dropped"
    assert kept[0].cycle_id != turns[0].cycle_id, "expected oldest turns to be dropped first"

    print("[PASS] Context budgeting checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)

