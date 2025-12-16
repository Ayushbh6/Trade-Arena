"""Sanity test for Phase 7 ledger_updates application (no network)."""

import os
import sys
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.ledger import Ledger, LedgerFacts, LessonItem, WatchlistItem
from src.agents.memory.ledger_updates import (
    LessonRemove,
    LessonUpsert,
    WatchlistRemove,
    WatchlistUpsert,
    apply_ledger_updates,
)


def main() -> None:
    print("== Phase 7 ledger_updates sanity test ==")

    ledger = Ledger(run_id="run_test", agent_id="agent_test")
    ledger.facts = LedgerFacts(capital_usdt=123.0)

    # Seed soft state.
    ledger.watchlist = [
        WatchlistItem(item_id="w1", symbol="BTCUSDT", thesis="Breakout retest", priority=4)
    ]
    ledger.lessons_last_5 = [LessonItem(item_id="l1", lesson="Wait for confirmation.")]

    updates = [
        WatchlistUpsert(op_type="watchlist_upsert", item=WatchlistItem(item_id="w2", symbol="ETHUSDT", thesis="Range fade", priority=3)),
        WatchlistRemove(op_type="watchlist_remove", item_id="w1"),
        LessonUpsert(op_type="lesson_upsert", item=LessonItem(item_id="l2", lesson="Don’t chase green candles.")),
        LessonRemove(op_type="lesson_remove", item_id="l1"),
    ]

    stats = apply_ledger_updates(ledger=ledger, updates=updates)

    assert ledger.facts.capital_usdt == 123.0
    assert [w.item_id for w in ledger.watchlist] == ["w2"]
    assert [l.item_id for l in ledger.lessons_last_5] == ["l2"]
    assert isinstance(ledger.updated_at, datetime)
    assert stats["watchlist_upserts"] == 1
    assert stats["watchlist_removes"] == 1
    assert stats["lesson_upserts"] == 1
    assert stats["lesson_removes"] == 1

    print("[PASS] ledger_updates applied safely.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
