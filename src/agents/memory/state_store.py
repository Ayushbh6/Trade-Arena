"""Mongo-backed persistence for Phase 7 context state.

Persist context state per (run_id, agent_id) for replayability and to avoid
cross-run contamination.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.agents.memory.context_manager import ContextState, new_context_state, PromptBudget
from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import AGENT_CONTEXT_STATES


class ContextStateStore:
    def __init__(self, *, mongo: MongoManager):
        self.mongo = mongo

    async def load_or_create(
        self,
        *,
        run_id: str,
        agent_id: str,
        budget: Optional[PromptBudget] = None,
    ) -> ContextState:
        await self.mongo.connect()
        col = self.mongo.collection(AGENT_CONTEXT_STATES)
        doc = await col.find_one({"run_id": run_id, "agent_id": agent_id})
        if not doc:
            return new_context_state(run_id=run_id, agent_id=agent_id, budget=budget)

        try:
            return ContextState.model_validate(doc)
        except Exception:
            # If a doc exists but is incompatible (schema evolution), start fresh but keep run_id isolation.
            return new_context_state(run_id=run_id, agent_id=agent_id, budget=budget)

    async def save(self, state: ContextState) -> None:
        await self.mongo.connect()
        col = self.mongo.collection(AGENT_CONTEXT_STATES)

        now = utc_now()
        state.updated_at = now  # type: ignore[misc]
        if not isinstance(state.created_at, datetime):
            state.created_at = now  # type: ignore[misc]

        doc = jsonify(state.model_dump(mode="json"))
        await col.replace_one(
            {"run_id": state.run_id, "agent_id": state.agent_id},
            doc,
            upsert=True,
        )


__all__ = ["ContextStateStore"]

