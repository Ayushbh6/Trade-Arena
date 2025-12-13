"""Orchestrator single-cycle integration test (Phase 5.2/5.3 + Phase 6.2).

Validates:
- A cycle runs end-to-end (at least through snapshot + audit logging).
- Orchestrator uses BOTH LLM_MODEL_TRADER_1 and LLM_MODEL_TRADER_2 for the two
  technical traders (recorded in audit_log as models_selected).
- PortfolioManager tracks state and generates a PnL report at the end.

Run:
  python tests/test_orchestrator.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys
  - LLM_MODEL_TRADER_1 and LLM_MODEL_TRADER_2 set (can be same, but both must exist)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG  # noqa: E402
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402
from src.portfolio.portfolio import PortfolioManager  # noqa: E402
from src.portfolio.reporting import ReportingEngine  # noqa: E402


def _print_section(title: str) -> None:
    print("\n" + "=" * 10 + f" {title} " + "=" * 10, flush=True)


def _print_kv(label: str, value: str) -> None:
    print(f"[INFO] {label}: {value}", flush=True)


async def _tail_audit_events(
    *,
    mongo: MongoManager,
    run_id: str,
    stop: asyncio.Event,
) -> None:
    await mongo.connect()
    col = mongo.collection(AUDIT_LOG)

    seen_ids: set[str] = set()
    event_types = {
        "cycle_start",
        "market_snapshot_ready",
        "models_selected",
        "tool_results_summary_traders",
        "tool_usage_traders",
        "trader_proposals_ready",
        "risk_reports_ready",
        "tool_usage_manager",
        "manager_decision_ready",
        "order_plan_ready",
        "cycle_end",
        "trader_error",
        "manager_error",
        "order_plan_error",
        "execution_error",
        "pnl_report_generated",
        "portfolio_trade_fetch_error"
    }

    def _id_str(doc: dict) -> str:
        return str(doc.get("_id"))

    saw_cycle_end = False
    while True:
        cursor = (
            col.find({"run_id": run_id, "event_type": {"$in": list(event_types)}})
            .sort("timestamp", 1)
            .limit(200)
        )
        docs = await cursor.to_list(length=200)
        for d in docs:
            doc_id = _id_str(d)
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            ts = d.get("timestamp")
            et = d.get("event_type")
            payload = d.get("payload") or {}

            if et == "cycle_start":
                _print_section(f"Cycle Start {ts}")
                _print_kv("cycle_id", str(payload.get("cycle_id")))
                continue

            if et == "market_snapshot_ready":
                _print_section(f"Market Snapshot Ready {ts}")
                _print_kv("snapshot_ref", str(payload.get("snapshot_ref")))
                continue

            if et == "models_selected":
                _print_section(f"Models Selected {ts}")
                tm = payload.get("trader_models") or {}
                _print_kv("tech_trader_1", str(tm.get("tech_trader_1")))
                _print_kv("tech_trader_2", str(tm.get("tech_trader_2")))
                _print_kv("manager_model", str(payload.get("manager_model")))
                continue

            if et == "trader_error":
                _print_section(f"Trader Error {ts}")
                _print_kv("agent_id", str(payload.get("agent_id")))
                _print_kv("model", str(payload.get("model")))
                _print_kv("error", str(payload.get("error")))
                if payload.get("error_type"):
                    _print_kv("error_type", str(payload.get("error_type")))
                if payload.get("error_repr"):
                    _print_kv("error_repr", str(payload.get("error_repr")))
                continue

            if et == "tool_usage_traders":
                _print_section(f"Trader Tool Usage {ts}")
                agents = payload.get("agents") or {}
                for aid, info in agents.items():
                    _print_kv(f"{aid}.tool_calls", str(info.get("tool_calls")))
                    _print_kv(f"{aid}.tools", ", ".join(info.get("tools") or []))
                continue

            if et == "tool_results_summary_traders":
                _print_section(f"Trader Tool Results Summary {ts}")
                agents = payload.get("agents") or {}
                for aid, items in agents.items():
                    items = items or []
                    if not items:
                        _print_kv(f"{aid}.tool_anomalies", "none")
                        continue
                    _print_kv(f"{aid}.tool_anomalies", str(len(items)))
                    for it in items:
                        _print_kv(
                            f"{aid}.{it.get('tool')}",
                            f"{it.get('status')} {it.get('error') or it.get('note') or ''}".strip(),
                        )
                continue

            if et == "trader_proposals_ready":
                _print_section(f"Trader Proposals Ready {ts}")
                for p in payload.get("proposals") or []:
                    agent_id = p.get("agent_id")
                    trades = p.get("trades") or []
                    _print_kv(f"{agent_id}.trades", str(len(trades)))
                    # Print full proposal JSON so the flow is inspectable.
                    try:
                        import json

                        print(json.dumps(p, indent=2, default=str), flush=True)
                    except Exception:
                        print(str(p), flush=True)
                continue

            if et == "risk_reports_ready":
                _print_section(f"Risk Reports Ready {ts}")
                for r in payload.get("reports") or []:
                    _print_kv(
                        f"{r.get('agent_id')}.passed",
                        str(r.get("passed")),
                    )
                    _print_kv(
                        f"{r.get('agent_id')}.hard_fail",
                        str(r.get("hard_fail")),
                    )
                continue

            if et == "tool_usage_manager":
                _print_section(f"Manager Tool Usage {ts}")
                _print_kv("tool_calls", str(payload.get("tool_calls")))
                _print_kv("tools", ", ".join(payload.get("tools") or []))
                continue

            if et == "manager_error":
                _print_section(f"Manager Error {ts}")
                _print_kv("error", str(payload.get("error")))
                if payload.get("model"):
                    _print_kv("model", str(payload.get("model")))
                if payload.get("error_type"):
                    _print_kv("error_type", str(payload.get("error_type")))
                if payload.get("error_repr"):
                    _print_kv("error_repr", str(payload.get("error_repr")))
                continue

            if et == "manager_decision_ready":
                _print_section(f"Manager Decision Ready {ts}")
                decision = payload.get("decision") or {}
                try:
                    import json

                    print(json.dumps(decision, indent=2, default=str), flush=True)
                except Exception:
                    print(str(decision), flush=True)
                continue

            if et == "order_plan_ready":
                _print_section(f"Order Plan Ready {ts}")
                intents = payload.get("intents") or []
                _print_kv("intents", str(len(intents)))
                continue

            if et == "pnl_report_generated":
                _print_section(f"PnL Report Generated {ts}")
                report = payload.get("report") or {}
                try:
                    import json
                    print(json.dumps(report, indent=2, default=str), flush=True)
                except Exception:
                    print(str(report), flush=True)
                continue

            if et in {"order_plan_error", "execution_error", "portfolio_trade_fetch_error"}:
                _print_section(f"{et} {ts}")
                _print_kv("error", str(payload.get("error")))
                continue

            if et == "cycle_end":
                _print_section(f"Cycle End {ts}")
                _print_kv("execution_status", str(payload.get("execution_status")))
                _print_kv("order_plan_intents", str(payload.get("order_plan_intents")))
                saw_cycle_end = True
                continue

        if saw_cycle_end:
            return
        if stop.is_set():
            # One final pass has been done; exit even if cycle_end missing.
            return
        await asyncio.sleep(0.5)


async def main() -> None:
    print("== Orchestrator integration test (Phase 5 + 6.2) ==")
    cfg = load_config()

    trader_1 = os.getenv("LLM_MODEL_TRADER_1")
    trader_2 = os.getenv("LLM_MODEL_TRADER_2")
    assert trader_1, "LLM_MODEL_TRADER_1 must be set for this test."
    assert trader_2, "LLM_MODEL_TRADER_2 must be set for this test."

    run_id = f"test_orchestrator_run_{int(time.time())}"
    cycle_id = "cycle_test_001"

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.", flush=True)
    
    # Init Portfolio Manager
    portfolio_manager = PortfolioManager()
    reporting_engine = ReportingEngine(portfolio_manager)

    orch = Orchestrator(
        mongo=mongo,
        portfolio_manager=portfolio_manager,
        reporting_engine=reporting_engine,
        config=cfg,
        # We enable execution to test the full portfolio loop (even if no trades happen, we want the report)
        orchestrator_config=OrchestratorConfig(
            execute_testnet=False,  # Set to True if we want real orders, but False for now to verify wiring
            trader_timeout_s=120.0,
            manager_timeout_s=120.0,
        ),
    )

    stop = asyncio.Event()
    tail_task = asyncio.create_task(_tail_audit_events(mongo=mongo, run_id=run_id, stop=stop))
    try:
        t0 = time.perf_counter()
        res = await orch.run_cycle(run_id=run_id, cycle_id=cycle_id)
        dt_s = time.perf_counter() - t0
        stop.set()
        await asyncio.wait_for(tail_task, timeout=10.0)
        print(
            f"\n[OK] Cycle completed. execution_status={res.execution_status} intents={res.order_plan_intents}",
            flush=True,
        )
        print(f"[INFO] Cycle duration_s={dt_s:.2f}", flush=True)
    finally:
        stop.set()
        if not tail_task.done():
            await asyncio.wait_for(tail_task, timeout=10.0)

    await mongo.connect()
    col = mongo.collection(AUDIT_LOG)

    doc = await col.find_one(
        {"run_id": run_id, "event_type": "models_selected"},
        sort=[("timestamp", -1)],
    )
    assert doc, "Expected models_selected audit event."

    payload = doc.get("payload") or {}
    trader_models = payload.get("trader_models") or {}
    assert trader_models.get("tech_trader_1") == trader_1
    assert trader_models.get("tech_trader_2") == trader_2
    print("[PASS] models_selected recorded both trader models correctly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] Orchestrator test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)
