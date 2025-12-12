"""Audit logging helpers.

MongoDB `audit_log` is the system of record for *every* decision and side effect.
This module provides a small convenience wrapper over `MongoManager.log_audit_event`
so other modules don't need to know collection details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.data.mongo import MongoManager


@dataclass(frozen=True)
class AuditContext:
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    trace_id: Optional[str] = None


class AuditManager:
    """Thin wrapper around MongoManager for audit events."""

    def __init__(self, mongo: MongoManager):
        self.mongo = mongo

    async def log(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        ctx: Optional[AuditContext] = None,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        c = ctx or AuditContext()
        return await self.mongo.log_audit_event(
            event_type,
            payload,
            run_id=run_id or c.run_id,
            agent_id=agent_id or c.agent_id,
            trace_id=trace_id or c.trace_id,
            metadata=metadata,
        )


__all__ = ["AuditContext", "AuditManager"]

