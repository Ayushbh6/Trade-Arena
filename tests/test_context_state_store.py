"""Integration sanity test for Phase 7 ContextState persistence.

Run:
  python tests/test_context_state_store.py

Requires:
  - Local MongoDB reachable via MONGODB_URI / MONGODB_URL

No OpenRouter key required.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.memory.context_manager import ConversationTurn, TurnRole  # noqa: E402
from src.agents.memory.state_store import ContextStateStore  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AGENT_CONTEXT_STATES  # noqa: E402


async def main() -> None:
    print("== Phase 7 ContextStateStore integration test ==")
    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    run_id = "test_run_context_state"
    agent_id = "tech_trader_context_state_test"

    store = ContextStateStore(mongo=mongo)

    # Start fresh for test repeatability.
    await mongo.collection(AGENT_CONTEXT_STATES).delete_many({"run_id": run_id, "agent_id": agent_id})

    state = await store.load_or_create(run_id=run_id, agent_id=agent_id)
    assert state.run_id == run_id
    assert state.agent_id == agent_id
    assert state.ledger.run_id == run_id
    assert state.ledger.agent_id == agent_id
    print("[OK] Created new ContextState.")

    await store.save(state)
    print("[OK] Saved ContextState.")

    loaded = await store.load_or_create(run_id=run_id, agent_id=agent_id)
    assert loaded.run_id == run_id
    assert loaded.agent_id == agent_id
    assert loaded.ledger.agent_id == agent_id
    print("[OK] Loaded ContextState.")

    loaded.instant_turns.append(
        ConversationTurn(role=TurnRole.user, content="What is our exposure?", cycle_id="cycle_test_1")
    )
    loaded.instant_turns.append(
        ConversationTurn(role=TurnRole.assistant, content="Exposure is within limits.", cycle_id="cycle_test_1")
    )
    loaded.narrative_summary = "Earlier cycles: no trades due to unclear edge."
    await store.save(loaded)
    print("[OK] Updated and re-saved ContextState.")

    loaded2 = await store.load_or_create(run_id=run_id, agent_id=agent_id)
    assert len(loaded2.instant_turns) == 2
    assert "Earlier cycles" in (loaded2.narrative_summary or "")
    print("[PASS] ContextState persistence roundtrip succeeded.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)

