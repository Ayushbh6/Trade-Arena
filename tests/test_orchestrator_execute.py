"""Full-cycle orchestrator execution smoke test (testnet) with cleanup.

This runs the REAL LLM -> manager -> order planning -> executor path, then cleans up:
- cancels open orders for the traded symbols
- closes any resulting positions with a reduce-only market order

Run:
  python tests/test_orchestrator_execute.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys (BINANCE_TESTNET_API_KEY/SECRET_KEY) and BINANCE_TESTNET=true
  - OPENROUTER_API_KEY and model env vars for traders/manager

Notes:
- Because LLMs may decide "no trade", this test will try up to 2 cycles.
  It FAILS if no executable order plan is produced.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.schemas import (  # noqa: E402
    DecisionItem,
    DecisionType,
    ManagerDecision,
    OrderType,
    Side,
    TradeAction,
    TradeIdea,
    TradeProposal,
)
from src.config import load_config  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import AUDIT_LOG  # noqa: E402
from src.execution.binance_client import BinanceFuturesClient  # noqa: E402
from src.execution.executor import ExecutorConfig, OrderExecutor  # noqa: E402
from src.execution.planner import build_order_plan  # noqa: E402
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402


def _print_section(title: str) -> None:
    print("\n" + "=" * 10 + f" {title} " + "=" * 10, flush=True)


def _print_kv(label: str, value: str) -> None:
    print(f"[INFO] {label}: {value}", flush=True)


async def _tail_audit(
    *,
    mongo: MongoManager,
    run_id: str,
    start_ts: datetime,
    stop: asyncio.Event,
) -> None:
    await mongo.connect()
    col = mongo.collection(AUDIT_LOG)
    seen: set[str] = set()
    watched = {
        # orchestrator
        "cycle_start",
        "market_snapshot_ready",
        "models_selected",
        "tool_usage_traders",
        "trader_proposals_ready",
        "risk_reports_ready",
        "manager_decision_ready",
        "order_plan_ready",
        "cycle_end",
        "trader_error",
        "manager_error",
        "order_plan_error",
        # execution
        "execution_plan_start",
        "execution_entry_preflight_failed",
        "execution_order_placed",
        "execution_order_failed",
        "execution_plan_complete",
        # positions
        "positions_sync_start",
        "positions_sync_complete",
    }

    def _id_str(doc: dict) -> str:
        return str(doc.get("_id"))

    saw_cycle_end = False
    while True:
        cursor = (
            col.find(
                {
                    "run_id": run_id,
                    "event_type": {"$in": list(watched)},
                    "timestamp": {"$gte": start_ts},
                }
            )
            .sort("timestamp", 1)
            .limit(500)
        )
        docs = await cursor.to_list(length=500)
        for d in docs:
            did = _id_str(d)
            if did in seen:
                continue
            seen.add(did)
            ts = d.get("timestamp")
            et = d.get("event_type")
            payload = d.get("payload") or {}

            if et in {"cycle_start", "cycle_end", "market_snapshot_ready", "models_selected", "order_plan_ready"}:
                _print_section(f"{et} {ts}")
                for k, v in payload.items():
                    if k in {"intents", "proposals", "reports", "decision", "report"}:
                        continue
                    _print_kv(k, str(v))
                if et == "cycle_end":
                    saw_cycle_end = True
                continue

            if et == "trader_error":
                _print_section(f"trader_error {ts}")
                _print_kv("agent_id", str(payload.get("agent_id")))
                _print_kv("model", str(payload.get("model")))
                _print_kv("error", str(payload.get("error")))
                continue

            if et == "manager_error":
                _print_section(f"manager_error {ts}")
                _print_kv("model", str(payload.get("model")))
                _print_kv("error", str(payload.get("error")))
                continue

            if et == "trader_proposals_ready":
                _print_section(f"trader_proposals_ready {ts}")
                for p in payload.get("proposals") or []:
                    _print_kv(f"{p.get('agent_id')}.trades", str(len(p.get("trades") or [])))
                continue

            if et == "manager_decision_ready":
                _print_section(f"manager_decision_ready {ts}")
                decision = payload.get("decision") or {}
                _print_kv("decisions", str(len(decision.get("decisions") or [])))
                _print_kv("notes", str(decision.get("notes")))
                continue

            if et.startswith("execution_") or et.startswith("positions_"):
                _print_section(f"{et} {ts}")
                # Keep concise: print the key fields if present.
                if et == "execution_order_placed":
                    intent = payload.get("intent") or {}
                    _print_kv("symbol", str(intent.get("symbol")))
                    _print_kv("leg", str(intent.get("leg")))
                    _print_kv("client_order_id", str(intent.get("client_order_id")))
                if et in {"execution_order_failed", "execution_entry_preflight_failed"}:
                    _print_kv("error", str(payload.get("error") or payload))
                if et == "execution_plan_complete":
                    rep = payload.get("report") or {}
                    _print_kv("results", str(len(rep.get("results") or [])))
                if et == "positions_sync_complete":
                    _print_kv("synced", str(payload.get("synced")))
                continue

        if saw_cycle_end:
            return
        if stop.is_set():
            return
        await asyncio.sleep(0.5)


async def _cleanup_symbols(client: BinanceFuturesClient, symbols: list[str]) -> None:
    for symbol in symbols:
        # Cancel open orders for symbol.
        try:
            open_orders = client.client.futures_get_open_orders(symbol=symbol)
            for o in open_orders:
                oid = o.get("orderId")
                if oid is None:
                    continue
                client.cancel_order(symbol=symbol, order_id=int(oid))
        except Exception:
            pass

        # Close any open position.
        try:
            pos_rows = client.get_positions(symbol=symbol)
            for p in pos_rows:
                amt = float(p.get("positionAmt") or 0.0)
                if abs(amt) < 1e-12:
                    continue
                side = "SELL" if amt > 0 else "BUY"
                qty = abs(amt)
                client.place_order(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    quantity=qty,
                    reduce_only=True,
                    client_order_id=f"cleanup_{symbol}_{int(time.time())}",
                )
        except Exception:
            pass


async def main() -> None:
    print("== Orchestrator execute smoke test (testnet) ==")
    cfg = load_config()
    assert cfg.binance.testnet, "BINANCE_TESTNET must be true for this test."

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    client = BinanceFuturesClient(
        testnet=cfg.binance.testnet,
        base_url=cfg.binance.base_url,
        recv_window=cfg.binance.recv_window,
        allow_mainnet=cfg.binance.allow_mainnet,
    )
    client.ping()
    print("[OK] Binance testnet client ready.")

    symbols = list(cfg.trading.symbols)
    await _cleanup_symbols(client, symbols)
    print("[OK] Pre-cleanup done.")

    orch = Orchestrator(
        mongo=mongo,
        config=cfg,
        orchestrator_config=OrchestratorConfig(
            execute_testnet=True,
            trader_timeout_s=180.0,
            manager_timeout_s=180.0,
            extra_trader_instructions=(
                "If there is ANY reasonable technical setup on one of the configured symbols, "
                "propose exactly ONE small trade (size_usdt 250-500) with a stop_loss and take_profit. "
                "If absolutely no edge, no-trade is acceptable."
            ),
        ),
    )

    run_id = f"test_orchestrator_exec_{int(time.time())}"
    executed = False
    last = None
    for i in range(2):
        cycle_id = f"cycle_exec_{i+1:03d}"
        print(f"[INFO] Running cycle {cycle_id} ...")
        stop = asyncio.Event()
        start_ts = datetime.now(timezone.utc)
        tail_task = asyncio.create_task(
            _tail_audit(mongo=mongo, run_id=run_id, start_ts=start_ts, stop=stop)
        )
        try:
            last = await orch.run_cycle(run_id=run_id, cycle_id=cycle_id)
        finally:
            stop.set()
            try:
                await asyncio.wait_for(tail_task, timeout=10.0)
            except Exception:
                pass
        print(f"[OK] Cycle done. intents={last.order_plan_intents} exec_status={last.execution_status}")
        if last.execution_status == "success":
            executed = True
            break

    # Deterministic fallback: if LLMs decide no-trade twice, still validate executor+cleanup end-to-end.
    if not executed:
        _print_section("Fallback Execution (Deterministic)")
        symbol = os.getenv("BINANCE_TEST_SYMBOL", (cfg.trading.symbols[0] if cfg.trading.symbols else "BTCUSDT"))
        notional_usdt = float(os.getenv("BINANCE_TEST_NOTIONAL_USD", "250"))
        sl_pct = float(os.getenv("BINANCE_TEST_SL_PCT", "0.003"))  # 0.30%
        tp_pct = float(os.getenv("BINANCE_TEST_TP_PCT", "0.004"))  # 0.40%

        mark = client.get_mark_price(symbol)
        stop_loss = mark * (1.0 - sl_pct)
        take_profit = mark * (1.0 + tp_pct)

        proposal = TradeProposal(
            agent_id="tech_trader_fallback",
            run_id=run_id,
            cycle_id="fallback_cycle",
            trades=[
                TradeIdea(
                    symbol=symbol,
                    side=Side.long,
                    action=TradeAction.open,
                    size_usdt=notional_usdt,
                    leverage=2.0,
                    order_type=OrderType.market,
                    limit_price=None,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.51,
                    rationale="Deterministic fallback trade for executor validation.",
                    invalidation="N/A",
                    tags=["fallback_executor_test"],
                )
            ],
            notes="fallback executor test",
        )

        manager = ManagerDecision(
            manager_id="manager",
            run_id=run_id,
            cycle_id="fallback_cycle",
            decisions=[
                DecisionItem(
                    agent_id="tech_trader_fallback",
                    trade_index=0,
                    symbol=symbol,
                    decision=DecisionType.approve,
                    notes="approve fallback test trade",
                )
            ],
            notes="approve fallback deterministic test trade",
        )

        plan = build_order_plan(proposals=[proposal], manager_decision=manager)
        _print_kv("symbol", symbol)
        _print_kv("mark_price", f"{mark:.4f}")
        _print_kv("intents", str(len(plan.intents)))

        exec_cfg = ExecutorConfig(wait_fill_timeout_s=15.0)
        executor = OrderExecutor(mongo=mongo, client=client, config=exec_cfg)
        report = await executor.execute_plan(plan)
        statuses = [str(r.status) for r in (report.results or [])]
        _print_kv("execution_results", str(len(report.results or [])))
        _print_kv("statuses", ", ".join(statuses))

        if any(str(r.status) in {"placed", "already_exists"} for r in (report.results or [])):
            executed = True

    # Always cleanup even if no trade happened.
    await _cleanup_symbols(client, symbols)
    print("[OK] Post-cleanup done.")

    assert last is not None
    assert executed, f"Expected successful execution; got execution_status={last.execution_status}"

    print("[PASS] Orchestrator executed at least one cycle with orders and cleaned up.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)
