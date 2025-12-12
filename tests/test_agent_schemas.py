"""Sanity test for agent schemas.

Run:
  python tests/test_agent_schemas.py

No network or DB required.
"""

import os
import sys

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
    export_json_schema,
)


def main() -> None:
    idea = TradeIdea(
        symbol="BTCUSDT",
        side=Side.long,
        action=TradeAction.open,
        size_usdt=250.0,
        leverage=2.0,
        order_type=OrderType.market,
        confidence=0.72,
        rationale="15m trend up, vol normal, breakout retest.",
        tags=["breakout", "trend"],
    )

    proposal = TradeProposal(agent_id="tech_trader", trades=[idea])
    dumped = proposal.model_dump(mode="json")
    assert dumped["agent_id"] == "tech_trader"
    assert dumped["trades"][0]["symbol"] == "BTCUSDT"

    # limit order requires limit_price
    try:
        TradeIdea(
            symbol="ETHUSDT",
            side=Side.short,
            action=TradeAction.open,
            size_usdt=100.0,
            order_type=OrderType.limit,
            confidence=0.5,
            rationale="test",
        )
        raise AssertionError("expected validation error for missing limit_price")
    except Exception:
        pass

    decision = ManagerDecision(
        manager_id="manager",
        decisions=[
            DecisionItem(
                symbol="BTCUSDT",
                decision=DecisionType.approve,
                approved_size_usdt=250.0,
                approved_leverage=2.0,
            )
        ],
    )
    dd = decision.model_dump(mode="json")
    assert dd["decisions"][0]["decision"] == "approve"

    schema = export_json_schema(TradeProposal)
    assert schema.get("title") == "TradeProposal"
    assert "properties" in schema and "trades" in schema["properties"]

    print("[PASS] Agent schemas validated.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)

