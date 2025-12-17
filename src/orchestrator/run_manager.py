"""Run/session lifecycle management (Phase 10.1).

MongoDB is the system of record. A run session document lets us:
- track status (running/paused/stopped)
- store a lightweight config snapshot for replayability
- safely support pause/resume in multi-worker deployments
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from src.config import AppConfig
from src.data.mongo import MongoManager, jsonify
from src.data.schemas import RUN_SESSIONS


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_run_id(*, prefix: str = "run") -> str:
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:8]
    return f"{prefix}_{ts}_{suffix}"


@dataclass(frozen=True)
class RunSession:
    run_id: str
    status: str  # running | paused | stopped
    created_at: datetime
    updated_at: datetime
    config: Dict[str, Any]


class RunManager:
    def __init__(self, *, mongo: MongoManager):
        self.mongo = mongo

    async def create_if_missing(
        self,
        *,
        run_id: str,
        cfg: AppConfig,
        status: str = "running",
    ) -> None:
        await self.mongo.connect()
        now = _utc_now()
        try:
            cfg_doc: Dict[str, Any] = asdict(cfg)
        except Exception:
            # Best-effort fallback; still keep the session record.
            cfg_doc = {"raw": str(cfg)}
        doc: Dict[str, Any] = {
            "run_id": run_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "config": jsonify(cfg_doc),
        }
        # Upsert to avoid clobbering if multiple workers start the same run_id.
        await self.mongo.collection(RUN_SESSIONS).update_one(
            {"run_id": run_id},
            {"$setOnInsert": doc},
            upsert=True,
        )

    async def get_status(self, *, run_id: str) -> Optional[str]:
        await self.mongo.connect()
        doc = await self.mongo.collection(RUN_SESSIONS).find_one({"run_id": run_id})
        if not doc:
            return None
        status = doc.get("status")
        return str(status) if status is not None else None

    async def set_status(self, *, run_id: str, status: str) -> None:
        await self.mongo.connect()
        await self.mongo.collection(RUN_SESSIONS).update_one(
            {"run_id": run_id},
            {"$set": {"status": status, "updated_at": _utc_now()}},
            upsert=True,
        )

    async def touch(self, *, run_id: str) -> None:
        await self.set_status(run_id=run_id, status="running")
