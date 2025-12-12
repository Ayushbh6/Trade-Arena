import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.schemas import (
    DecisionItem,
    DecisionType,
    ManagerDecision,
    OrderType,
    Side,
    TradeAction,
    TradeIdea,
    TradeProposal,
)
from src.execution.planner import build_order_plan
from src.execution.schemas import ExecutionOrderType, OrderLeg, OrderSide


def main() -> None:
    print("== Execution schemas + planner test (Phase 4.1) ==")

    proposal = TradeProposal(
        agent_id="tech_trader",
        run_id="run_001",
        cycle_id="cycle_001",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=500.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=92000.0,
                stop_loss=91500.0,
                take_profit=93000.0,
                confidence=0.62,
                rationale="Test trade 1",
                invalidation="Test invalidation 1",
                tags=["test"],
            ),
            TradeIdea(
                symbol="ETHUSDT",
                side=Side.short,
                action=TradeAction.open,
                size_usdt=300.0,
                leverage=2.0,
                order_type=OrderType.market,
                stop_loss=3500.0,
                take_profit=None,
                confidence=0.55,
                rationale="Test trade 2",
                invalidation="Test invalidation 2",
                tags=["test"],
            ),
        ],
        notes="test proposal",
    )

    manager = ManagerDecision(
        manager_id="manager",
        run_id="run_001",
        cycle_id="cycle_001",
        decisions=[
            DecisionItem(
                agent_id="tech_trader",
                trade_index=0,
                symbol="BTCUSDT",
                decision=DecisionType.approve,
                notes="approve btc",
            ),
            DecisionItem(
                agent_id="tech_trader",
                trade_index=1,
                symbol="ETHUSDT",
                decision=DecisionType.resize,
                approved_size_usdt=200.0,
                approved_leverage=1.0,
                notes="resize eth",
            ),
            DecisionItem(
                agent_id="tech_trader",
                trade_index=0,
                symbol="BTCUSDT",
                decision=DecisionType.veto,
                notes="veto ignored for planning",
            ),
        ],
        notes="test manager decision",
    )

    plan1 = build_order_plan(proposals=[proposal], manager_decision=manager)
    plan2 = build_order_plan(proposals=[proposal], manager_decision=manager)

    assert plan1.model_dump() == plan2.model_dump(), "order planning must be deterministic"
    assert plan1.run_id == "run_001"
    assert plan1.cycle_id == "cycle_001"

    # BTC: entry + SL + TP = 3 intents
    # ETH: entry + SL = 2 intents (TP omitted)
    assert len(plan1.intents) == 5, f"expected 5 intents, got {len(plan1.intents)}"

    btc_entry = next(i for i in plan1.intents if i.symbol == "BTCUSDT" and i.leg == OrderLeg.entry)
    assert btc_entry.side == OrderSide.buy
    assert btc_entry.order_type == ExecutionOrderType.limit
    assert btc_entry.time_in_force == "GTC"

    btc_sl = next(i for i in plan1.intents if i.symbol == "BTCUSDT" and i.leg == OrderLeg.stop_loss)
    assert btc_sl.side == OrderSide.sell
    assert btc_sl.order_type == ExecutionOrderType.stop_market
    assert btc_sl.trigger_price == 91500.0
    assert btc_sl.reduce_only is True

    btc_tp = next(i for i in plan1.intents if i.symbol == "BTCUSDT" and i.leg == OrderLeg.take_profit)
    assert btc_tp.side == OrderSide.sell
    assert btc_tp.order_type == ExecutionOrderType.take_profit_market
    assert btc_tp.trigger_price == 93000.0
    assert btc_tp.reduce_only is True

    eth_entry = next(i for i in plan1.intents if i.symbol == "ETHUSDT" and i.leg == OrderLeg.entry)
    assert eth_entry.side == OrderSide.sell
    assert eth_entry.order_type == ExecutionOrderType.market
    assert eth_entry.notional_usdt == 200.0, "resize must override trade size"
    assert eth_entry.leverage == 1.0, "resize must override leverage"

    eth_sl = next(i for i in plan1.intents if i.symbol == "ETHUSDT" and i.leg == OrderLeg.stop_loss)
    assert eth_sl.side == OrderSide.buy
    assert eth_sl.order_type == ExecutionOrderType.stop_market
    assert eth_sl.reduce_only is True

    print("[OK] Deterministic order planning.")
    print("[OK] Entry/SL/TP mapping validated.")
    print("[OK] Resize override validated.")

    print("\n== OrderPlan snippet ==")
    print(
        json.dumps(
            {
                "run_id": plan1.run_id,
                "cycle_id": plan1.cycle_id,
                "intents_count": len(plan1.intents),
                "first_intent": plan1.intents[0].model_dump(),
            },
            indent=2,
            default=str,
        )
    )
    print("[PASS] Phase 4.1 execution schemas + planner checks passed.")


if __name__ == "__main__":
    main()
