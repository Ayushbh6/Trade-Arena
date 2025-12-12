"""Authentic integration test for Phase 4.3 position tracker (Binance Futures testnet).

Run:
  python tests/test_position_tracker.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys (BINANCE_TESTNET_API_KEY/SECRET_KEY)
  - BINANCE_TESTNET=true
"""

import asyncio
import json
import os
import sys
import time

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
from src.data.mongo import MongoManager  # noqa: E402
from src.data.schemas import POSITIONS  # noqa: E402
from src.execution.binance_client import BinanceFuturesClient  # noqa: E402
from src.execution.executor import ExecutorConfig, OrderExecutor  # noqa: E402
from src.execution.planner import build_order_plan  # noqa: E402
from src.execution.position_tracker import PositionTracker  # noqa: E402


async def _cleanup_symbol(client: BinanceFuturesClient, symbol: str) -> None:
    try:
        open_orders = client.client.futures_get_open_orders(symbol=symbol)
        for o in open_orders:
            oid = o.get("orderId")
            if oid is None:
                continue
            client.cancel_order(symbol=symbol, order_id=int(oid))
    except Exception:
        pass

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
                client_order_id=f"cleanup_{int(time.time())}",
            )
    except Exception:
        pass


async def main() -> None:
    print("== Position tracker integration test (Phase 4.3) ==")
    symbol = os.getenv("BINANCE_TEST_SYMBOL", "BTCUSDT")

    mongo = MongoManager()
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    client = BinanceFuturesClient()
    client.ping()
    print("[OK] Binance client ready.")

    await _cleanup_symbol(client, symbol)
    print(f"[OK] Pre-cleanup done for {symbol}.")

    mark = client.get_mark_price(symbol)
    notional_usdt = float(os.getenv("BINANCE_TEST_NOTIONAL_USD", "250"))
    sl_mult = float(os.getenv("BINANCE_TEST_SL_PCT", "0.003"))
    tp_mult = float(os.getenv("BINANCE_TEST_TP_PCT", "0.004"))

    stop_loss = mark * (1.0 - sl_mult)
    take_profit = mark * (1.0 + tp_mult)

    proposal = TradeProposal(
        agent_id="tech_trader",
        run_id="run_pos_test",
        cycle_id="cycle_pos_test",
        trades=[
            TradeIdea(
                symbol=symbol,
                side=Side.long,
                action=TradeAction.open,
                size_usdt=notional_usdt,
                leverage=2.0,
                order_type=OrderType.market,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=0.55,
                rationale="Position tracker test trade.",
                invalidation="N/A",
                tags=["position_tracker_test"],
            )
        ],
    )

    manager = ManagerDecision(
        manager_id="manager",
        run_id="run_pos_test",
        cycle_id="cycle_pos_test",
        decisions=[
            DecisionItem(
                agent_id="tech_trader",
                trade_index=0,
                symbol=symbol,
                decision=DecisionType.approve,
                notes="approve for position tracker test",
            )
        ],
        notes="approve position tracker test trade",
    )

    plan = build_order_plan(proposals=[proposal], manager_decision=manager)
    executor = OrderExecutor(mongo=mongo, client=client, config=ExecutorConfig(wait_fill_timeout_s=15.0))
    report = await executor.execute_plan(plan)
    print("[OK] Executed entry + exits.")
    entry_ok = any(r.leg == "entry" and r.status in {"placed", "already_exists"} for r in report.results)
    assert entry_ok, "entry not placed; see report"

    tracker = PositionTracker(mongo=mongo, client=client)
    synced = await tracker.sync_positions(run_id="run_pos_test", cycle_id="cycle_pos_test", symbols=[symbol])
    print(f"[OK] Synced positions: {len(synced)}")
    print("== Synced snippet ==")
    print(json.dumps(synced[0] if synced else {}, indent=2, default=str))

    # Verify Mongo doc exists for the symbol with non-zero qty.
    await mongo.connect()
    pos_doc = await mongo.collection(POSITIONS).find_one({"run_id": "run_pos_test", "symbol": symbol})
    assert pos_doc is not None, "expected position doc in Mongo"
    assert abs(float(pos_doc.get("qty") or 0.0)) > 0.0, "expected non-zero position qty"
    print(f"[OK] Mongo position qty: {pos_doc.get('qty')} agent_owner={pos_doc.get('agent_owner')}")

    await _cleanup_symbol(client, symbol)
    print(f"[OK] Post-cleanup done for {symbol}.")

    await mongo.close()
    print("[PASS] Position tracker integration test passed.")


if __name__ == "__main__":
    asyncio.run(main())

