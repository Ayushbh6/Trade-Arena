"""Deterministic rule engine sanity tests.

Run:
  python tests/test_risk_rules.py

No network or DB required (pure rules).
"""

import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.schemas import (  # noqa: E402
    OrderType,
    Side,
    TradeAction,
    TradeIdea,
    TradeProposal,
)
from src.risk.rules import evaluate_trade_proposal  # noqa: E402


def main() -> None:
    risk_limits = {
        "firm_max_total_notional_mult": 2.0,
        "firm_max_leverage_per_position": 3.0,
        "firm_daily_stop_pct": 0.05,
        "agent_budget_notional_usd": 1000.0,
        "agent_max_risk_pct_per_trade": 0.01,
        "agent_cooldown_cycles_after_stop": 2,
        "vol_spike_size_reduction_mult": 0.5,
    }

    # 1) Hard: missing stop loss for open/add
    p1 = TradeProposal(
        agent_id="tech_trader",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=500.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                take_profit=110.0,
                confidence=0.6,
                rationale="test",
            )
        ],
    )
    r1 = evaluate_trade_proposal(
        p1,
        firm_state={"capital_usdt": 3000.0, "total_notional_usdt": 0.0, "drawdown_pct": 0.0},
        agent_budget_usdt=1000.0,
        risk_limits=risk_limits,
        market_brief=None,
    )
    assert r1.hard_fail is True
    assert any(v["rule_id"] == "trade.stop_loss_required" for v in r1.model_dump(mode="json")["hard_violations"])

    # 2) Hard: leverage > max
    p2 = TradeProposal(
        agent_id="tech_trader",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=200.0,
                leverage=10.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=99.0,
                take_profit=102.0,
                confidence=0.6,
                rationale="test",
            )
        ],
    )
    r2 = evaluate_trade_proposal(
        p2,
        firm_state={"capital_usdt": 3000.0, "total_notional_usdt": 0.0, "drawdown_pct": 0.0},
        agent_budget_usdt=1000.0,
        risk_limits=risk_limits,
        market_brief=None,
    )
    assert r2.hard_fail is True
    assert any(v.rule_id == "firm.max_leverage_per_position" for v in r2.hard_violations)

    # 3) Soft: size exceeds budget -> resize suggestion
    p3 = TradeProposal(
        agent_id="tech_trader",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=5000.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=99.0,
                take_profit=102.0,
                confidence=0.6,
                rationale="test",
            )
        ],
    )
    r3 = evaluate_trade_proposal(
        p3,
        firm_state={"capital_usdt": 3000.0, "total_notional_usdt": 0.0, "drawdown_pct": 0.0},
        agent_budget_usdt=1000.0,
        risk_limits=risk_limits,
        market_brief=None,
    )
    assert r3.hard_fail is False and r3.passed is True
    assert any(v.rule_id == "agent.budget_cap" for v in r3.soft_violations)
    assert any(s.suggested_size_usdt == 1000.0 for s in r3.resize_suggestions)

    # 4) Soft: per-trade risk exceeds max -> resize suggestion
    # risk_pct = (100-90)/100 = 10%; risk_usdt = 500*0.10=50 => 5% of 1000 budget
    p4 = TradeProposal(
        agent_id="tech_trader",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=500.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=90.0,
                take_profit=120.0,
                confidence=0.6,
                rationale="test",
            )
        ],
    )
    r4 = evaluate_trade_proposal(
        p4,
        firm_state={"capital_usdt": 3000.0, "total_notional_usdt": 0.0, "drawdown_pct": 0.0},
        agent_budget_usdt=1000.0,
        risk_limits=risk_limits,
        market_brief=None,
    )
    assert r4.hard_fail is False
    assert any(v.rule_id == "agent.risk_per_trade_pct" for v in r4.soft_violations)
    assert any(s.suggested_size_usdt is not None and s.suggested_size_usdt < 500.0 for s in r4.resize_suggestions)

    # 5) Hard: firm daily stop blocks new risk, but reduce/close allowed.
    p5 = TradeProposal(
        agent_id="tech_trader",
        trades=[
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.open,
                size_usdt=200.0,
                leverage=2.0,
                order_type=OrderType.limit,
                limit_price=100.0,
                stop_loss=99.0,
                take_profit=102.0,
                confidence=0.6,
                rationale="test",
            ),
            TradeIdea(
                symbol="BTCUSDT",
                side=Side.long,
                action=TradeAction.reduce,
                size_usdt=50.0,
                leverage=2.0,
                order_type=OrderType.market,
                stop_loss=None,
                take_profit=None,
                confidence=0.6,
                rationale="reduce exposure",
            ),
        ],
    )
    r5 = evaluate_trade_proposal(
        p5,
        firm_state={"capital_usdt": 3000.0, "total_notional_usdt": 0.0, "drawdown_pct": 0.08},
        agent_budget_usdt=1000.0,
        risk_limits=risk_limits,
        market_brief={"per_symbol": {"BTCUSDT": {"mark_price": 100.0}}},
    )
    assert r5.hard_fail is True
    # The open trade should be flagged; the reduce trade should not create stop-loss-required violation.
    assert any(v.rule_id == "firm.daily_stop" and v.trade_index == 0 for v in r5.hard_violations)
    assert not any(v.rule_id == "trade.stop_loss_required" and v.trade_index == 1 for v in r5.hard_violations)

    # Ensure JSON-serializable output
    json.dumps(r5.model_dump(mode="json"))

    print("[PASS] Risk rules sanity checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)

