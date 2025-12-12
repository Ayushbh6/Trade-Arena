"""Risk proposal validator.

This module bridges live/recorded state (firm, budgets, market brief) with the
pure deterministic rule engine in `src/risk/rules.py`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..agents.schemas import TradeProposal
from ..agents.tools.context import ToolContext
from ..agents.tools.market_tools import get_firm_state, get_market_brief
from ..config import load_config
from ..data.mongo import jsonify
from .rules import evaluate_trade_proposal
from .schemas import ComplianceReport


async def validate_proposal(
    proposal: TradeProposal,
    *,
    tools_context: ToolContext,
    firm_state: Optional[Dict[str, Any]] = None,
    market_brief: Optional[Dict[str, Any]] = None,
    allow_live_fetch: bool = True,
) -> ComplianceReport:
    """Validate a trader proposal using current firm state and market context.

    - Hard failures MUST be vetoed by the Manager.
    - Soft violations may be resized by the Manager using suggestions.
    """
    cfg = tools_context.config or load_config()

    if firm_state is None:
        firm_state = await get_firm_state(context=tools_context)

    # Risk limits come from config; firm_state may also carry a copy for the LLM.
    risk_limits: Dict[str, Any] = dict(cfg.risk.__dict__)
    if isinstance(firm_state.get("risk_limits"), dict):
        # Keep config defaults as source of truth; only overlay known keys.
        for k, v in firm_state["risk_limits"].items():
            if k in risk_limits:
                risk_limits[k] = v

    agent_budget = float(
        (firm_state.get("agent_budgets") or {}).get(
            proposal.agent_id, cfg.risk.agent_budget_notional_usd
        )
    )

    symbols = sorted({t.symbol for t in proposal.trades if t.symbol})
    if not symbols:
        # No trades: still emit a report (pass) for completeness.
        return ComplianceReport(
            agent_id=proposal.agent_id,
            run_id=proposal.run_id,
            cycle_id=proposal.cycle_id,
            hard_violations=[],
            soft_violations=[],
            resize_suggestions=[],
            hard_fail=False,
            passed=True,
            notes="No trades in proposal; no risk checks triggered.",
        )

    if market_brief is None:
        market_brief = await get_market_brief(
            symbols=symbols,
            lookback_minutes=240,
            allow_live_fetch=allow_live_fetch,
            context=tools_context,
        )

    report = evaluate_trade_proposal(
        proposal,
        firm_state=firm_state,
        agent_budget_usdt=agent_budget,
        risk_limits=risk_limits,
        market_brief=market_brief,
    )

    # Best-effort ensure report is JSON/BSON-friendly when persisted.
    _ = jsonify(report.model_dump(mode="json"))

    return report


__all__ = ["validate_proposal"]

