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
from src.orchestrator.run_manager import RunManager, generate_run_id


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI-Native Trader Co. (testnet)")
    p.add_argument("--run-id", default=generate_run_id(prefix="run"), help="Run/session identifier")
    p.add_argument("--once", action="store_true", help="Run exactly one cycle and exit")
    p.add_argument("--cycle-id", default=None, help="Optional cycle id for --once")
    p.add_argument(
        "--set-run-status",
        default=None,
        choices=["running", "paused", "stopped"],
        help="Update run session status in Mongo and exit (Phase 10 pause/resume).",
    )
    p.add_argument(
        "--replay",
        action="store_true",
        help="Run Phase 10 replay: re-run traders+manager against stored snapshots (no execution).",
    )
    p.add_argument("--replay-source-run-id", default=None, help="Source run_id to replay (required for --replay).")
    p.add_argument("--from-ts", default=None, help="Replay window start (UTC ISO, e.g. 2025-12-15T00:00:00Z).")
    p.add_argument("--to-ts", default=None, help="Replay window end (UTC ISO, e.g. 2025-12-16T00:00:00Z).")
    p.add_argument("--replay-run-id", default=None, help="run_id to write replay outputs under (optional).")
    p.add_argument("--replay-model-tech-trader-1", default=None, help="Override model for tech_trader_1 in replay.")
    p.add_argument("--replay-model-tech-trader-2", default=None, help="Override model for tech_trader_2 in replay.")
    p.add_argument("--replay-model-macro-trader-1", default=None, help="Override model for macro_trader_1 in replay.")
    p.add_argument(
        "--replay-model-structure-trader-1", default=None, help="Override model for structure_trader_1 in replay."
    )
    p.add_argument("--replay-model-manager", default=None, help="Override model for manager in replay.")
    p.add_argument(
        "--weekly-review",
        action="store_true",
        help="Run Phase 9 weekly review (previous week trust update + budget rebalance) and exit.",
    )
    p.add_argument("--db-name", default=os.getenv("MONGODB_DB", "investment"), help="MongoDB database name")
    p.add_argument("--cadence-minutes", type=int, default=None, help="Cadence in minutes (default from env/config)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not place orders (still logs proposals/decisions/plan).",
    )
    p.add_argument(
        "--memory-compression",
        action="store_true",
        help="Enable Phase 7 memory: raw QnA + narrative summary + grounded ledger (and summarizer compression).",
    )
    p.add_argument(
        "--auto-weekly-review",
        action="store_true",
        help="Enable APScheduler weekly review job (defaults to Monday 00:05 UTC).",
    )
    return p


async def _amain() -> int:
    load_dotenv()
    args = _build_arg_parser().parse_args()

    cfg = load_config()
    mongo = MongoManager(db_name=args.db_name)
    await mongo.connect()
    await mongo.ensure_indexes()
    run_mgr = RunManager(mongo=mongo)

    if args.set_run_status:
        await run_mgr.set_status(run_id=args.run_id, status=str(args.set_run_status))
        print(f"[INFO] run_status_updated run_id={args.run_id} status={args.set_run_status}")
        return 0

    if args.replay:
        from src.orchestrator.replay import parse_ts_utc, run_replay

        if not args.replay_source_run_id or not args.from_ts or not args.to_ts:
            raise SystemExit("--replay requires --replay-source-run-id, --from-ts, and --to-ts")
        replay_run_id = args.replay_run_id or generate_run_id(prefix=f"replay_{args.replay_source_run_id}")
        await run_mgr.create_if_missing(run_id=replay_run_id, cfg=cfg, status="running")

        orch = Orchestrator(
            mongo=mongo,
            config=cfg,
            orchestrator_config=OrchestratorConfig(
                execute_testnet=False,
                memory_compression=bool(args.memory_compression),
            ),
        )

        overrides = {
            "tech_trader_1": args.replay_model_tech_trader_1,
            "tech_trader_2": args.replay_model_tech_trader_2,
            "macro_trader_1": args.replay_model_macro_trader_1,
            "structure_trader_1": args.replay_model_structure_trader_1,
            "manager": args.replay_model_manager,
        }
        overrides = {k: v for k, v in overrides.items() if v}

        res = await run_replay(
            orchestrator=orch,
            source_run_id=str(args.replay_source_run_id),
            replay_run_id=replay_run_id,
            start=parse_ts_utc(str(args.from_ts)),
            end=parse_ts_utc(str(args.to_ts)),
            model_overrides=overrides or None,
        )
        print("[INFO] replay_report:", res)
        return 0

    await run_mgr.create_if_missing(run_id=args.run_id, cfg=cfg, status="running")

    print("[INFO] Starting AI-Native Trader Co.")
    print(f"[INFO] run_id={args.run_id}")
    print(f"[INFO] BINANCE_TESTNET={cfg.binance.testnet} BINANCE_BASE_URL={cfg.binance.base_url}")
    print(f"[INFO] BINANCE_ALLOW_MAINNET={cfg.binance.allow_mainnet}")
    print(f"[INFO] cadence_minutes={args.cadence_minutes or cfg.trading.cadence_minutes}")
    print("[INFO] traders=[tech_trader_1, tech_trader_2, macro_trader_1, structure_trader_1]")
    print("[INFO] models env=[LLM_MODEL_TRADER_1, LLM_MODEL_TRADER_2, LLM_MODEL_TRADER_3, LLM_MODEL_TRADER_4]")
    env_mem = os.getenv("MEMORY_COMPRESSION", "").strip().lower() in {"1", "true", "yes", "y", "on"}
    mem_enabled = bool(args.memory_compression or env_mem)
    print(f"[INFO] memory_compression={mem_enabled}")

    orch = Orchestrator(
        mongo=mongo,
        config=cfg,
        orchestrator_config=OrchestratorConfig(
            execute_testnet=not args.dry_run,
            memory_compression=mem_enabled,
        ),
    )

    if args.weekly_review:
        from src.orchestrator.weekly_review import run_weekly_review

        res = await run_weekly_review(mongo=mongo, run_id=args.run_id, cfg=cfg)
        print("[INFO] weekly_review:", res)
        return 0

    if args.once:
        await run_once(orchestrator=orch, run_id=args.run_id, cycle_id=args.cycle_id)
        return 0

    ml_cfg = MainLoopConfig(
        cadence_minutes=args.cadence_minutes or cfg.trading.cadence_minutes,
        auto_weekly_review=bool(args.auto_weekly_review),
    )
    await run_forever(orchestrator=orch, run_id=args.run_id, cfg=ml_cfg)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
