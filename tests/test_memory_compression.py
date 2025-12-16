"""Authentic integration test for Phase 7 narrative compression (network).

Run:
  python tests/test_memory_compression.py

Requires:
  - OPENROUTER_API_KEY (for summarizer call)

This test is intentionally "real" (no mocks). If OPENROUTER_API_KEY is missing,
it will skip.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.summarizer import SummarizerConfig, summarize_narrative  # noqa: E402


def main() -> None:
    print("== Phase 7 narrative compression integration test ==")
    if not os.getenv("OPENROUTER_API_KEY"):
        print("[SKIP] OPENROUTER_API_KEY not set.")
        return

    model = (
        os.getenv("LLM_MODEL_SUMMARIZER")
        or os.getenv("LLM_MODEL_MANAGER_FAST")
        or os.getenv("LLM_MODEL_MANAGER")
        or os.getenv("LLM_MODEL_TRADER_1")
        or "deepseek/deepseek-chat"
    )
    cfg = SummarizerConfig(model=model, temperature=0.0)

    existing = "Earlier: agent mostly stayed flat due to choppy conditions."
    appended = "\n".join(
        [
            "User: What did we learn from the BTC breakout attempt?",
            "Assistant: We entered too early; better wait for retest confirmation.",
            "User: What is on the watchlist now?",
            "Assistant: BTCUSDT (breakout-retest), ETHUSDT (range fade).",
        ]
        * 20
    )

    res = summarize_narrative(
        config=cfg,
        agent_id="tech_trader_test",
        run_id="run_test",
        existing_narrative_summary=existing,
        appended_old_transcript=appended,
        current_watchlist=[],
        current_lessons_last_5=[],
    )

    assert res.new_narrative_summary and isinstance(res.new_narrative_summary, str)
    assert isinstance(res.ledger_updates, list)
    # Back-compat: some models may also return full lists; if present, they must respect caps.
    if res.watchlist is not None:
        assert isinstance(res.watchlist, list)
        assert len(res.watchlist) <= 50
    if res.lessons_last_5 is not None:
        assert isinstance(res.lessons_last_5, list)
        assert len(res.lessons_last_5) <= 5

    print("[OK] new_narrative_summary length:", len(res.new_narrative_summary))
    print("[OK] ledger_updates:", len(res.ledger_updates))
    print("[OK] watchlist items:", len(res.watchlist or []))
    print("[OK] lessons_last_5:", len(res.lessons_last_5 or []))
    print("[PASS] Narrative compression checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
