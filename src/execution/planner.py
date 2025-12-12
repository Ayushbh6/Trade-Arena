"""Order plan builder (deterministic, exchange-agnostic)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.agents.schemas import (
    DecisionType,
    ManagerDecision,
    OrderType,
    Side,
    TradeAction,
    TradeProposal,
)
from src.execution.schemas import (
    ExecutionOrderType,
    OrderIntent,
    OrderLeg,
    OrderPlan,
    OrderSide,
    make_client_order_id,
)


class OrderPlanError(RuntimeError):
    pass


def _entry_side(trade_side: Side) -> OrderSide:
    return OrderSide.buy if trade_side == Side.long else OrderSide.sell


def _exit_side(trade_side: Side) -> OrderSide:
    return OrderSide.sell if trade_side == Side.long else OrderSide.buy


def _entry_order_type(trade_order_type: OrderType) -> Tuple[ExecutionOrderType, Optional[str]]:
    if trade_order_type == OrderType.market:
        return ExecutionOrderType.market, None
    return ExecutionOrderType.limit, "GTC"


def _trade_key(agent_id: Optional[str], trade_index: Optional[int]) -> Tuple[str, int]:
    if not agent_id:
        raise OrderPlanError("ManagerDecision DecisionItem.agent_id is required for execution planning")
    if trade_index is None:
        raise OrderPlanError(
            "ManagerDecision DecisionItem.trade_index is required for execution planning"
        )
    return agent_id, trade_index


def build_order_plan(
    *,
    proposals: List[TradeProposal],
    manager_decision: ManagerDecision,
) -> OrderPlan:
    """Convert manager approvals/resizes into an executable OrderPlan.

    Notes:
    - This function is deterministic and does not call the exchange.
    - Quantities are not computed here (executor will use mark price + filters).
    """

    by_agent: Dict[str, TradeProposal] = {p.agent_id: p for p in proposals}
    intents: List[OrderIntent] = []

    for d in manager_decision.decisions:
        if d.decision in {DecisionType.veto, DecisionType.defer}:
            continue
        if d.decision not in {DecisionType.approve, DecisionType.resize}:
            continue

        agent_id, trade_index = _trade_key(d.agent_id, d.trade_index)
        proposal = by_agent.get(agent_id)
        if proposal is None:
            raise OrderPlanError(f"Missing TradeProposal for agent_id={agent_id}")
        if trade_index >= len(proposal.trades):
            raise OrderPlanError(
                f"trade_index out of range for agent_id={agent_id}: {trade_index}"
            )

        trade = proposal.trades[trade_index]
        if trade.symbol != d.symbol:
            raise OrderPlanError(
                f"Decision symbol mismatch for agent_id={agent_id} trade_index={trade_index}: "
                f"{d.symbol} != {trade.symbol}"
            )

        final_size = (
            d.approved_size_usdt if (d.decision == DecisionType.resize and d.approved_size_usdt) else trade.size_usdt
        )
        final_leverage = (
            d.approved_leverage
            if (d.decision == DecisionType.resize and d.approved_leverage)
            else trade.leverage
        )

        if trade.action not in {TradeAction.open, TradeAction.add}:
            raise OrderPlanError(
                f"Only open/add supported in Phase 4.1 planner. Got action={trade.action}"
            )

        entry_order_type, tif = _entry_order_type(trade.order_type)
        entry_side = _entry_side(trade.side)

        entry_intent = OrderIntent(
            intent_id=make_client_order_id(
                run_id=manager_decision.run_id,
                cycle_id=manager_decision.cycle_id,
                agent_id=agent_id,
                trade_index=trade_index,
                leg=OrderLeg.entry.value,
                symbol=trade.symbol,
            ),
            client_order_id=make_client_order_id(
                run_id=manager_decision.run_id,
                cycle_id=manager_decision.cycle_id,
                agent_id=agent_id,
                trade_index=trade_index,
                leg=OrderLeg.entry.value,
                symbol=trade.symbol,
            ),
            run_id=manager_decision.run_id,
            cycle_id=manager_decision.cycle_id,
            agent_id=agent_id,
            trade_index=trade_index,
            symbol=trade.symbol,
            leg=OrderLeg.entry,
            side=entry_side,
            order_type=entry_order_type,
            notional_usdt=float(final_size),
            leverage=float(final_leverage) if final_leverage is not None else None,
            limit_price=float(trade.limit_price) if trade.limit_price is not None else None,
            trigger_price=None,
            reduce_only=False,
            time_in_force=tif,
            meta={
                "source": "manager_decision",
                "decision": d.decision,
            },
        )
        intents.append(entry_intent)

        exit_side = _exit_side(trade.side)

        if trade.stop_loss is not None:
            sl_intent = OrderIntent(
                intent_id=make_client_order_id(
                    run_id=manager_decision.run_id,
                    cycle_id=manager_decision.cycle_id,
                    agent_id=agent_id,
                    trade_index=trade_index,
                    leg=OrderLeg.stop_loss.value,
                    symbol=trade.symbol,
                ),
                client_order_id=make_client_order_id(
                    run_id=manager_decision.run_id,
                    cycle_id=manager_decision.cycle_id,
                    agent_id=agent_id,
                    trade_index=trade_index,
                    leg=OrderLeg.stop_loss.value,
                    symbol=trade.symbol,
                ),
                run_id=manager_decision.run_id,
                cycle_id=manager_decision.cycle_id,
                agent_id=agent_id,
                trade_index=trade_index,
                symbol=trade.symbol,
                leg=OrderLeg.stop_loss,
                side=exit_side,
                order_type=ExecutionOrderType.stop_market,
                notional_usdt=float(final_size),
                leverage=None,
                limit_price=None,
                trigger_price=float(trade.stop_loss),
                reduce_only=True,
                time_in_force=None,
                meta={"source": "manager_decision"},
            )
            intents.append(sl_intent)

        if trade.take_profit is not None:
            tp_intent = OrderIntent(
                intent_id=make_client_order_id(
                    run_id=manager_decision.run_id,
                    cycle_id=manager_decision.cycle_id,
                    agent_id=agent_id,
                    trade_index=trade_index,
                    leg=OrderLeg.take_profit.value,
                    symbol=trade.symbol,
                ),
                client_order_id=make_client_order_id(
                    run_id=manager_decision.run_id,
                    cycle_id=manager_decision.cycle_id,
                    agent_id=agent_id,
                    trade_index=trade_index,
                    leg=OrderLeg.take_profit.value,
                    symbol=trade.symbol,
                ),
                run_id=manager_decision.run_id,
                cycle_id=manager_decision.cycle_id,
                agent_id=agent_id,
                trade_index=trade_index,
                symbol=trade.symbol,
                leg=OrderLeg.take_profit,
                side=exit_side,
                order_type=ExecutionOrderType.take_profit_market,
                notional_usdt=float(final_size),
                leverage=None,
                limit_price=None,
                trigger_price=float(trade.take_profit),
                reduce_only=True,
                time_in_force=None,
                meta={"source": "manager_decision"},
            )
            intents.append(tp_intent)

    return OrderPlan(
        run_id=manager_decision.run_id,
        cycle_id=manager_decision.cycle_id,
        manager_id=manager_decision.manager_id,
        created_at=manager_decision.timestamp,
        intents=intents,
        notes=manager_decision.notes,
    )
