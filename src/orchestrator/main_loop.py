"""Cadence loop using APScheduler (Phase 5.2).

Supports:
- run once
- run continuously on a fixed cadence
"""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.data.audit import AuditContext, AuditManager
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.run_manager import RunManager
from src.portfolio.portfolio import PortfolioManager
from src.portfolio.reporting import ReportingEngine
from src.orchestrator.weekly_review import run_weekly_review


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cycle_id() -> str:
    return _utc_now().strftime("cycle_%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class MainLoopConfig:
    cadence_minutes: int = 6
    auto_weekly_review: bool = False
    weekly_review_day: str = "mon"  # UTC
    weekly_review_hour: int = 0
    weekly_review_minute: int = 5
    max_cycles: Optional[int] = None  # Phase 12: stop after N cycles


async def run_once(*, orchestrator: Orchestrator, run_id: str, cycle_id: Optional[str] = None) -> None:
    # Ensure portfolio/reporting are present if orchestrator was created without them externally
    if not orchestrator.portfolio_manager:
        orchestrator.portfolio_manager = PortfolioManager()
    if not orchestrator.reporting_engine:
        orchestrator.reporting_engine = ReportingEngine(orchestrator.portfolio_manager)

    await orchestrator.run_cycle(run_id=run_id, cycle_id=cycle_id or _cycle_id())


async def run_forever(
    *,
    orchestrator: Orchestrator,
    run_id: str,
    cfg: MainLoopConfig,
) -> None:
    stop_event = asyncio.Event()
    cycle_counter = 0  # Phase 12: track cycles

    # Instantiate Portfolio Manager and Reporting Engine
    # Note: These persist across cycles in the main loop
    portfolio_manager = PortfolioManager()
    reporting_engine = ReportingEngine(portfolio_manager)
    
    # Inject into orchestrator
    orchestrator.portfolio_manager = portfolio_manager
    orchestrator.reporting_engine = reporting_engine

    def _request_stop(*_args: object) -> None:
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                signal.signal(sig, lambda *_a: _request_stop())
    except Exception:
        pass

    scheduler = AsyncIOScheduler(timezone="UTC")
    run_mgr = RunManager(mongo=orchestrator.mongo)
    await run_mgr.create_if_missing(run_id=run_id, cfg=orchestrator.config, status="running")

    async def _job() -> None:
        nonlocal cycle_counter
        
        # Phase 12: Check max_cycles limit
        if cfg.max_cycles and cycle_counter >= cfg.max_cycles:
            await AuditManager(orchestrator.mongo).log(
                "max_cycles_reached",
                {"run_id": run_id, "cycles": cycle_counter, "max_cycles": cfg.max_cycles},
                ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
            )
            await run_mgr.set_status(run_id=run_id, status="completed")
            stop_event.set()
            return
        
        status = await run_mgr.get_status(run_id=run_id)
        if status == "paused":
            await AuditManager(orchestrator.mongo).log(
                "cycle_skipped_paused",
                {"run_id": run_id},
                ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
            )
            return
        if status == "stopped":
            stop_event.set()
            return
        
        await orchestrator.run_cycle(run_id=run_id, cycle_id=_cycle_id())
        cycle_counter += 1
        
        # Update cycle progress in MongoDB
        await orchestrator.mongo.db.run_sessions.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "current_cycle": cycle_counter,
                    "total_cycles": cfg.max_cycles,
                    "status": "running",
                }
            },
        )
        await AuditManager(orchestrator.mongo).log(
            "cycle_progress",
            {"run_id": run_id, "current_cycle": cycle_counter, "total_cycles": cfg.max_cycles},
            ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
        )

        # If we just completed the last cycle, stop immediately (don't wait for the next scheduler tick).
        if cfg.max_cycles and cycle_counter >= cfg.max_cycles:
            await AuditManager(orchestrator.mongo).log(
                "run_complete",
                {"run_id": run_id, "cycles": cycle_counter},
                ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
            )
            await run_mgr.set_status(run_id=run_id, status="completed")
            stop_event.set()
            return

    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=max(1, int(cfg.cadence_minutes)),
        id="trading_cycle",
        max_instances=1,
        coalesce=True,
    )

    async def _weekly_review_job() -> None:
        await run_weekly_review(mongo=orchestrator.mongo, run_id=run_id, cfg=orchestrator.config)

    if bool(cfg.auto_weekly_review):
        # Cron trigger: defaults to Monday 00:05 UTC.
        scheduler.add_job(
            _weekly_review_job,
            trigger="cron",
            day_of_week=str(cfg.weekly_review_day),
            hour=int(cfg.weekly_review_hour),
            minute=int(cfg.weekly_review_minute),
            id="weekly_review",
            max_instances=1,
            coalesce=True,
        )

    audit = AuditManager(orchestrator.mongo)
    await audit.log(
        "main_loop_start",
        {
            "run_id": run_id,
            "cadence_minutes": cfg.cadence_minutes,
            "auto_weekly_review": bool(cfg.auto_weekly_review),
        },
        ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
    )

    # Run the first cycle immediately so the UI sees activity without waiting a full cadence interval.
    await _job()
    if stop_event.is_set():
        await audit.log("main_loop_stop", {"run_id": run_id}, ctx=AuditContext(run_id=run_id, agent_id="orchestrator"))
        return

    scheduler.start()
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        await audit.log(
            "main_loop_stop",
            {"run_id": run_id},
            ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
        )


__all__ = ["MainLoopConfig", "run_once", "run_forever"]
