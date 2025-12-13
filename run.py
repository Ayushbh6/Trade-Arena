"""Run the AI-native trader (Phase 5.2 entrypoint).

Defaults:
- Binance Futures TESTNET execution enabled (never mainnet).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.config import load_config
from src.data.mongo import MongoManager
from src.orchestrator.main_loop import MainLoopConfig, run_forever, run_once
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI-Native Trader Co. (testnet)")
    p.add_argument("--run-id", default=f"run_{_utc_now_str()}", help="Run/session identifier")
    p.add_argument("--once", action="store_true", help="Run exactly one cycle and exit")
    p.add_argument("--cycle-id", default=None, help="Optional cycle id for --once")
    p.add_argument("--db-name", default=os.getenv("MONGODB_DB", "investment"), help="MongoDB database name")
    p.add_argument("--cadence-minutes", type=int, default=None, help="Cadence in minutes (default from env/config)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not place orders (still logs proposals/decisions/plan).",
    )
    return p


async def _amain() -> int:
    load_dotenv()
    args = _build_arg_parser().parse_args()

    cfg = load_config()
    mongo = MongoManager(db_name=args.db_name)
    await mongo.connect()
    await mongo.ensure_indexes()

    print("[INFO] Starting AI-Native Trader Co.")
    print(f"[INFO] run_id={args.run_id}")
    print(f"[INFO] BINANCE_TESTNET={cfg.binance.testnet} BINANCE_BASE_URL={cfg.binance.base_url}")
    print(f"[INFO] BINANCE_ALLOW_MAINNET={cfg.binance.allow_mainnet}")
    print(f"[INFO] cadence_minutes={args.cadence_minutes or cfg.trading.cadence_minutes}")

    orch = Orchestrator(
        mongo=mongo,
        config=cfg,
        orchestrator_config=OrchestratorConfig(
            execute_testnet=not args.dry_run,
        ),
    )

    if args.once:
        await run_once(orchestrator=orch, run_id=args.run_id, cycle_id=args.cycle_id)
        return 0

    ml_cfg = MainLoopConfig(cadence_minutes=args.cadence_minutes or cfg.trading.cadence_minutes)
    await run_forever(orchestrator=orch, run_id=args.run_id, cfg=ml_cfg)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()

