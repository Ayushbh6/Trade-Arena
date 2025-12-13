"""MongoDB connection manager and audit/LLM logging helpers.

This layer is strategy-neutral. It provides async connectivity, index setup,
and rich audit/LLM-call persistence for later replay and analysis.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING, TypeAlias

from pymongo.errors import PyMongoError

from .schemas import AUDIT_LOG, COLLECTION_SPECS, LLM_CALLS, PNL_REPORTS, MARKET_SNAPSHOTS

# Motor types for static checking without upsetting Pylance at runtime.
if TYPE_CHECKING:
    from motor.motor_asyncio import (
        AsyncIOMotorClient as _AsyncIOMotorClient,
        AsyncIOMotorCollection as _AsyncIOMotorCollection,
        AsyncIOMotorDatabase as _AsyncIOMotorDatabase,
    )

    MotorClient: TypeAlias = _AsyncIOMotorClient
    MotorCollection: TypeAlias = _AsyncIOMotorCollection
    MotorDatabase: TypeAlias = _AsyncIOMotorDatabase
else:  # pragma: no cover
    MotorClient: TypeAlias = Any
    MotorCollection: TypeAlias = Any
    MotorDatabase: TypeAlias = Any
try:  # pragma: no cover
    from motor.motor_asyncio import AsyncIOMotorClient as _AsyncIOMotorClientRuntime
except ImportError:  # pragma: no cover
    _AsyncIOMotorClientRuntime = Any


def utc_now() -> datetime:
    """UTC timestamp helper."""
    return datetime.now(timezone.utc)


def jsonify(value: Any) -> Any:
    """Best-effort conversion to JSON/BSON-safe types."""
    # pylint: disable=too-many-return-statements,too-many-branches,broad-exception-caught
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.hex()
    if isinstance(value, (list, tuple)):
        return [jsonify(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    # Pydantic v2
    if hasattr(value, "model_dump"):
        try:
            return jsonify(value.model_dump())
        except Exception:
            pass
    # Pydantic v1 or SDK objects with dict()
    if hasattr(value, "dict"):
        try:
            return jsonify(value.dict())
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return jsonify(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "json"):
        try:
            return json.loads(value.json())
        except Exception:
            pass
    try:
        return jsonify(vars(value))
    except Exception:
        return str(value)


class MongoManager:
    """Async MongoDB manager using Motor."""

    def __init__(self, db_name: str = "investment", uri: Optional[str] = None):
        self.uri = uri or os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
        if not self.uri:
            raise RuntimeError("MONGODB_URI (or MONGODB_URL) is not set in env.")
        self.db_name = db_name
        self.client: Optional[MotorClient] = None
        self.db: Optional[MotorDatabase] = None

    async def connect(self) -> MotorDatabase:
        """Connect (lazily) and return the database handle."""
        if self.client is None:
            self.client = _AsyncIOMotorClientRuntime(self.uri)
            self.db = self.client[self.db_name]
        if self.db is None:
            raise RuntimeError("MongoManager failed to connect.")
        return self.db

    async def close(self) -> None:
        """Close client and clear handles."""
        if self.client is not None:
            self.client.close()
        self.client = None
        self.db = None

    def collection(self, name: str) -> MotorCollection:
        """Get a collection handle (requires connect)."""
        if self.db is None:
            raise RuntimeError("MongoManager not connected. Call await connect().")
        return self.db[name]

    async def ensure_indexes(self) -> None:
        """Create indexes declared in schemas.py."""
        await self.connect()
        for spec in COLLECTION_SPECS.values():
            col = self.collection(spec.name)
            for idx in spec.indexes:
                try:
                    await col.create_index(list(idx))
                except PyMongoError:
                    # Index may already exist or be incompatible; ignore for MVP.
                    continue

    async def insert_one(self, collection: str, doc: Dict[str, Any]) -> str:
        """Insert a document and return inserted id as str."""
        await self.connect()
        col = self.collection(collection)
        res = await col.insert_one(jsonify(doc))
        return str(res.inserted_id)

    async def find_one(self, collection: str, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find one document matching query."""
        await self.connect()
        return await self.collection(collection).find_one(query)

    async def log_audit_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Insert an audit event into audit_log."""
        # pylint: disable=too-many-arguments
        doc: Dict[str, Any] = {
            "timestamp": utc_now(),
            "event_type": event_type,
            "payload": payload,
        }
        if run_id:
            doc["run_id"] = run_id
        if agent_id:
            doc["agent_id"] = agent_id
        if trace_id:
            doc["trace_id"] = trace_id
        if metadata:
            doc["metadata"] = metadata
        return await self.insert_one(AUDIT_LOG, doc)

    async def log_llm_call(
        self,
        *,
        provider: str,
        model: str,
        messages: Any,
        response: Any,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        timing: Optional[Dict[str, Any]] = None,
        request_params: Optional[Dict[str, Any]] = None,
        tool_calls: Optional[Any] = None,
        error: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store a full LLM call including raw response and all metadata.

        `messages` and `response` may be SDK objects; they are jsonified.
        """
        # pylint: disable=too-many-arguments,too-many-locals
        doc: Dict[str, Any] = {
            "timestamp": utc_now(),
            "provider": provider,
            "model": model,
            "messages": jsonify(messages),
            "response": jsonify(response),
        }
        if run_id:
            doc["run_id"] = run_id
        if agent_id:
            doc["agent_id"] = agent_id
        if trace_id:
            doc["trace_id"] = trace_id
        if usage:
            doc["usage"] = jsonify(usage)
        if timing:
            doc["timing"] = jsonify(timing)
        if request_params:
            doc["request_params"] = jsonify(request_params)
        if tool_calls is not None:
            doc["tool_calls"] = jsonify(tool_calls)
        if error:
            doc["error"] = jsonify(error)
        if extra:
            doc["extra"] = jsonify(extra)

        inserted_id = await self.insert_one(LLM_CALLS, doc)
        # Mirror into audit_log for unified timeline.
        await self.log_audit_event(
            "llm_call",
            payload={"llm_call_ref": inserted_id},
            run_id=run_id,
            agent_id=agent_id,
            trace_id=trace_id,
            metadata={
                "provider": provider,
                "model": model,
                "usage": jsonify(usage) if usage else None,
                "timing": jsonify(timing) if timing else None,
            },
        )
        return inserted_id


class LlmTimer:
    """Small helper to measure latency for an LLM call."""
    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.start_s = time.perf_counter()

    def finish(self) -> Dict[str, Any]:
        """Return latency summary."""
        end_s = time.perf_counter()
        return {"latency_s": end_s - self.start_s}


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def _smoke() -> None:
        mgr = MongoManager(db_name="investment_test")
        await mgr.connect()
        await mgr.ensure_indexes()
        await mgr.log_audit_event("smoke", {"ok": True})
        await mgr.close()

    asyncio.run(_smoke())
