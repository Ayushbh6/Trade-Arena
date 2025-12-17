"""Process manager for orchestrator control (Phase 12).

Manages the orchestrator subprocess lifecycle:
- Start with custom trader selection and cycle limits
- Track running state and progress
- Stop gracefully
- Persist state to MongoDB for cross-session awareness
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from pymongo import ReturnDocument

from src.data.mongo import MongoManager
from src.data.schemas import RUN_SESSIONS


class ProcessManager:
    """Singleton manager for orchestrator subprocess control."""

    _instance: Optional[ProcessManager] = None
    _lock = asyncio.Lock()

    def __init__(self, mongo: MongoManager):
        self.mongo = mongo
        self.process: Optional[subprocess.Popen] = None
        self.run_id: Optional[str] = None
        self.total_cycles: Optional[int] = None
        self.started_at: Optional[datetime] = None
        self._log_fp: Optional[Any] = None

    @classmethod
    async def get_instance(cls, mongo: MongoManager) -> ProcessManager:
        """Get or create singleton instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls(mongo)
            return cls._instance

    async def start_orchestrator(
        self,
        run_id: Optional[str],
        traders: List[str],
        cycles: int,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Start orchestrator subprocess with custom parameters.

        Args:
            run_id: Unique run identifier
            traders: List of enabled trader agent IDs (e.g., ["tech_trader_1", "macro_trader_1"])
            cycles: Maximum number of cycles to run
            dry_run: If True, don't execute trades (proposals/decisions only)

        Returns:
            Status dict with success/error info
        """
        async with self._lock:
            # Check if already running
            if self.process and self.process.poll() is None:
                return {
                    "success": False,
                    "error": "Orchestrator already running",
                    "run_id": self.run_id,
                }

            try:
                # Allocate run_id if missing.
                if not run_id:
                    run_id = await self._next_run_id()
            except Exception as e:
                return {"success": False, "error": f"Failed to allocate run_id: {type(e).__name__}: {e}"}

            # Build command
            python_exe = sys.executable
            repo_root = Path(__file__).resolve().parents[2]
            run_script = repo_root / "run.py"
            
            cmd = [
                python_exe,
                str(run_script),
                "--run-id", run_id,
                "--max-cycles", str(cycles),
                "--enabled-traders", ",".join(traders),
            ]
            
            if dry_run:
                cmd.append("--dry-run")

            # Start subprocess (detached, survives parent)
            try:
                logs_dir = repo_root / "runs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_path = logs_dir / f"orchestrator_{run_id}.log"
                self._log_fp = open(log_path, "ab", buffering=0)

                self.process = subprocess.Popen(
                    cmd,
                    stdout=self._log_fp,
                    stderr=self._log_fp,
                    start_new_session=True,  # Detach from parent
                )
                self.run_id = run_id
                self.total_cycles = cycles
                self.started_at = datetime.now(timezone.utc)

                # Persist to MongoDB
                await self.mongo.collection(RUN_SESSIONS).update_one(
                    {"run_id": run_id},
                    {
                        "$set": {
                            "status": "running",
                            "pid": self.process.pid,
                            "total_cycles": cycles,
                            "enabled_traders": traders,
                            "started_at": self.started_at,
                            "dry_run": dry_run,
                            "updated_at": datetime.now(timezone.utc),
                        }
                        ,
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )

                return {
                    "success": True,
                    "run_id": run_id,
                    "pid": self.process.pid,
                    "cycles": cycles,
                    "traders": traders,
                    "log_path": str(log_path),
                    "started_at": self.started_at.isoformat(),
                }

            except Exception as e:
                try:
                    if self._log_fp:
                        self._log_fp.close()
                finally:
                    self._log_fp = None
                return {
                    "success": False,
                    "error": str(e),
                }

    async def stop_orchestrator(self) -> Dict[str, Any]:
        """
        Stop running orchestrator gracefully.

        Sends SIGTERM to allow current cycle to complete.
        """
        async with self._lock:
            pid: Optional[int] = None
            active_run_id: Optional[str] = self.run_id
            if self.process and self.process.poll() is None:
                pid = int(self.process.pid)
            else:
                # Attempt to stop the latest running session even after API restart.
                last_session = await self.mongo.collection(RUN_SESSIONS).find_one(
                    {"status": "running", "pid": {"$exists": True}},
                    sort=[("started_at", -1)],
                )
                if last_session:
                    pid = int(last_session.get("pid") or 0) or None
                    active_run_id = str(last_session.get("run_id") or "") or None

            if not pid:
                return {"success": False, "error": "No orchestrator running"}

            try:
                # Send SIGTERM for graceful shutdown
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                
                # Wait up to 30 seconds for graceful exit
                if self.process and self.process.poll() is None and int(self.process.pid) == pid:
                    try:
                        self.process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                        self.process.wait()

                # Update MongoDB
                if active_run_id:
                    await self.mongo.collection(RUN_SESSIONS).update_one(
                        {"run_id": active_run_id},
                        {
                            "$set": {
                                "status": "stopped",
                                "stopped_at": datetime.now(timezone.utc),
                            }
                        },
                    )

                stopped_run_id = active_run_id
                self.process = None
                self.run_id = None
                self.total_cycles = None
                self.started_at = None
                try:
                    if self._log_fp:
                        self._log_fp.close()
                finally:
                    self._log_fp = None

                return {
                    "success": True,
                    "run_id": stopped_run_id,
                    "message": "Orchestrator stopped",
                }

            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                }

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current orchestrator status.

        Returns live status including cycle progress from MongoDB.
        """
        async with self._lock:
            # Prefer MongoDB as the source of truth so status works across API restarts.
            last_session = await self.mongo.collection(RUN_SESSIONS).find_one({}, sort=[("started_at", -1)])
            if not last_session:
                return {"running": False, "run_id": None}

            run_id = str(last_session.get("run_id") or "") or None
            status = str(last_session.get("status") or "") or None
            pid = int(last_session.get("pid") or 0) or None
            current_cycle = int(last_session.get("current_cycle") or 0)
            total_cycles = last_session.get("total_cycles")

            def _pid_alive(p: Optional[int]) -> bool:
                if not p:
                    return False
                try:
                    os.kill(p, 0)
                    return True
                except Exception:
                    return False

            if status == "running" and pid and not _pid_alive(pid):
                await self.mongo.collection(RUN_SESSIONS).update_one(
                    {"run_id": run_id},
                    {"$set": {"status": "error", "error": "Process terminated unexpectedly"}},
                )
                status = "error"

            running = bool(status == "running" and pid and _pid_alive(pid))
            return {
                "running": running,
                "run_id": run_id,
                "status": status,
                "current_cycle": current_cycle,
                "total_cycles": total_cycles,
                "started_at": _to_iso(last_session.get("started_at")),
                "pid": pid,
                "error": last_session.get("error"),
            }

    async def _next_run_id(self) -> str:
        seq = await _inc_counter(mongo=self.mongo, name="ui_run_seq")
        return _format_run_id(seq)

def _to_iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return None


async def _inc_counter(*, mongo: MongoManager, name: str) -> int:
    await mongo.connect()
    col = mongo.collection("counters")
    doc = await col.find_one_and_update(
        {"_id": name},
        {"$inc": {"value": 1}, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    v = 0
    if isinstance(doc, dict):
        v = int(doc.get("value") or 0)
    return max(1, v)


def _format_run_id(seq: int) -> str:
    # run_0001, run_0002, ...; expands naturally beyond 4 digits.
    return f"run_{seq:04d}"
