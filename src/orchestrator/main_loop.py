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
from src.portfolio.portfolio import PortfolioManager
from src.portfolio.reporting import ReportingEngine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cycle_id() -> str:
    return _utc_now().strftime("cycle_%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class MainLoopConfig:
    cadence_minutes: int = 6


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

    async def _job() -> None:
        await orchestrator.run_cycle(run_id=run_id, cycle_id=_cycle_id())

    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=max(1, int(cfg.cadence_minutes)),
        id="trading_cycle",
        max_instances=1,
        coalesce=True,
    )

    audit = AuditManager(orchestrator.mongo)
    await audit.log(
        "main_loop_start",
        {"run_id": run_id, "cadence_minutes": cfg.cadence_minutes},
        ctx=AuditContext(run_id=run_id, agent_id="orchestrator"),
    )

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

