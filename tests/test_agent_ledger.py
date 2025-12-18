from __future__ import annotations

import asyncio

from src.portfolio.agent_ledger import AgentLedgerManager, estimate_required_margin_usdt
from src.execution.schemas import OrderIntent, OrderLeg, OrderPlan, OrderSide, ExecutionOrderType


def test_margin_estimate_basic() -> None:
    assert estimate_required_margin_usdt(notional_usdt=6000.0, leverage=3.0) == 2000.0
    assert estimate_required_margin_usdt(notional_usdt=1000.0, leverage=None) == 1000.0


def test_compute_from_plan_sums_entry_margin() -> None:
    plan = OrderPlan(
        run_id="run_x",
        cycle_id="cycle_x",
        intents=[
            OrderIntent(
                intent_id="i1",
                client_order_id="c1",
                run_id="run_x",
                cycle_id="cycle_x",
                agent_id="tech_trader_1",
                trade_index=0,
                symbol="BTCUSDT",
                leg=OrderLeg.entry,
                side=OrderSide.buy,
                order_type=ExecutionOrderType.market,
                notional_usdt=1000.0,
                leverage=5.0,
            ),
            OrderIntent(
                intent_id="i2",
                client_order_id="c2",
                run_id="run_x",
                cycle_id="cycle_x",
                agent_id="tech_trader_1",
                trade_index=1,
                symbol="ETHUSDT",
                leg=OrderLeg.entry,
                side=OrderSide.buy,
                order_type=ExecutionOrderType.market,
                notional_usdt=500.0,
                leverage=2.0,
            ),
            # SL leg should not affect reserved margin in the ledger.
            OrderIntent(
                intent_id="i3",
                client_order_id="c3",
                run_id="run_x",
                cycle_id="cycle_x",
                agent_id="tech_trader_1",
                trade_index=1,
                symbol="ETHUSDT",
                leg=OrderLeg.stop_loss,
                side=OrderSide.sell,
                order_type=ExecutionOrderType.stop_market,
                notional_usdt=500.0,
                leverage=None,
                trigger_price=100.0,
                reduce_only=True,
            ),
        ],
    )

    mgr = AgentLedgerManager(mongo=None)
    ledgers = asyncio.run(
        mgr.compute_from_plan(
            run_id="run_x",
            plan=plan,
            firm_capital_usdt=10000.0,
            per_agent_budget_usdt={"tech_trader_1": 2500.0},
        )
    )

    l = ledgers["tech_trader_1"]
    assert round(l.reserved_margin_usdt, 6) == 450.0
    assert round(l.available_budget_usdt, 6) == 2050.0
    assert len(l.open_items) == 2
