"""Authentic integration test for audit logging (Phase 4.4).

Run:
  python tests/test_audit_log.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.audit import AuditContext, AuditManager  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG  # noqa: E402


async def main() -> None:
    print("== Audit log integration test (Phase 4.4) ==")
    mongo = MongoManager()
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    audit = AuditManager(mongo)
    ctx = AuditContext(run_id="run_audit_test", agent_id="test_suite")

    await audit.log("audit_test_start", {"ts": datetime.now(timezone.utc).isoformat()}, ctx=ctx)
    await audit.log("audit_test_event", {"hello": "world"}, ctx=ctx)
    await audit.log("audit_test_complete", {"ok": True}, ctx=ctx)
    print("[OK] Wrote 3 audit events.")

    col = mongo.collection(AUDIT_LOG)
    cursor = col.find({"run_id": "run_audit_test"}).sort("timestamp", -1).limit(5)
    docs = await cursor.to_list(length=5)
    assert len(docs) >= 3, f"expected >=3 audit docs, got {len(docs)}"
    print("[OK] Queried audit_log. latest event_type:", docs[0].get("event_type"))

    await mongo.close()
    print("[PASS] Audit log checks passed.")


if __name__ == "__main__":
    asyncio.run(main())

