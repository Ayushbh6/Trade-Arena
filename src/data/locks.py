"""Simple distributed locks using MongoDB.

Used to ensure weekly jobs (trust update/rebalance) run only once even when the
app is deployed with multiple workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import ReturnDocument

from src.data.mongo import MongoManager, utc_now


LOCKS = "locks"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class LockResult:
    acquired: bool
    lock_name: str
    owner: str
    expires_at: datetime


async def acquire_lock(
    *,
    mongo: MongoManager,
    lock_name: str,
    owner: str,
    ttl_seconds: int = 600,
) -> LockResult:
    """Acquire lock if free/expired; otherwise return acquired=False."""
    await mongo.connect()
    now = _utc(utc_now())
    expires = now + timedelta(seconds=int(ttl_seconds))

    col = mongo.collection(LOCKS)
    doc = await col.find_one_and_update(
        {"_id": lock_name, "$or": [{"expires_at": {"$lte": now}}, {"expires_at": {"$exists": False}}]},
        {"$set": {"owner": owner, "expires_at": expires, "updated_at": now}, "$setOnInsert": {"created_at": now}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    if isinstance(doc, dict) and doc.get("owner") == owner:
        return LockResult(acquired=True, lock_name=lock_name, owner=owner, expires_at=expires)
    # Someone else holds it.
    held_expires = expires
    try:
        if isinstance(doc, dict) and doc.get("expires_at"):
            held_expires = _utc(doc["expires_at"])
    except Exception:
        pass
    return LockResult(acquired=False, lock_name=lock_name, owner=owner, expires_at=held_expires)


async def release_lock(
    *,
    mongo: MongoManager,
    lock_name: str,
    owner: str,
) -> None:
    """Release lock best-effort (only if owned)."""
    try:
        await mongo.connect()
        await mongo.collection(LOCKS).delete_one({"_id": lock_name, "owner": owner})
    except Exception:
        return


__all__ = ["LOCKS", "LockResult", "acquire_lock", "release_lock"]

