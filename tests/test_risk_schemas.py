"""Sanity test for risk/governance schemas.

Run:
  python tests/test_risk_schemas.py

No network or DB required.
"""

import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.risk.schemas import (  # noqa: E402
    ComplianceReport,
    ResizeSuggestion,
    Violation,
    ViolationSeverity,
    export_json_schema,
)


def main() -> None:
    report = ComplianceReport(
        agent_id="tech_trader",
        hard_violations=[
            Violation(
                rule_id="firm.max_leverage_per_position",
                severity=ViolationSeverity.hard,
                message="Leverage 10.0 exceeds firm max 3.0",
                symbol="BTCUSDT",
                trade_index=0,
                data={"leverage": 10.0, "max": 3.0},
            )
        ],
        soft_violations=[
            Violation(
                rule_id="agent.risk_per_trade_pct",
                severity=ViolationSeverity.soft,
                message="Risk per trade exceeds target; consider resizing down.",
                symbol="BTCUSDT",
                trade_index=0,
                data={"risk_pct": 0.02, "max_pct": 0.01},
            )
        ],
        resize_suggestions=[
            ResizeSuggestion(
                symbol="BTCUSDT",
                trade_index=0,
                suggested_size_mult=0.5,
                reason="Volatility spike regime: reduce size by 50%.",
            )
        ],
        hard_fail=True,
        passed=False,
        notes="Hard fail: manager must veto. Soft suggestions included for future cycles.",
    )

    dumped = report.model_dump(mode="json")
    assert dumped["agent_id"] == "tech_trader"
    assert dumped["hard_fail"] is True and dumped["passed"] is False
    assert dumped["hard_violations"][0]["severity"] == "hard"

    # Ensure JSON-serializable
    json.dumps(dumped)

    schema = export_json_schema(ComplianceReport)
    assert schema.get("title") == "ComplianceReport"
    assert "properties" in schema and "hard_violations" in schema["properties"]

    print("[PASS] Risk schemas validated.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)

