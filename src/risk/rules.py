"""Deterministic hard/soft risk rules for trade proposals.

Design:
- Rules are pure and deterministic (no DB/network).
- "Hard" violations MUST be vetoed by the Manager.
- "Soft" violations may be resized by the Manager using suggestions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..agents.schemas import OrderType, Side, TradeAction, TradeIdea, TradeProposal
from .schemas import ComplianceReport, ResizeSuggestion, Violation, ViolationSeverity


def _is_risk_increasing(action: TradeAction) -> bool:
    return action in {TradeAction.open, TradeAction.add}


def _is_risk_reducing(action: TradeAction) -> bool:
    return action in {TradeAction.reduce, TradeAction.close}


def _entry_price(trade: TradeIdea, market_brief: Optional[Dict[str, Any]]) -> Optional[float]:
    if trade.order_type == OrderType.limit:
        return trade.limit_price

    # market order: best-effort use mark_price from market_brief
    if not market_brief:
        return None
    per_symbol = (market_brief or {}).get("per_symbol") or {}
    sym = per_symbol.get(trade.symbol) or {}
    mp = sym.get("mark_price")
    try:
        return float(mp) if mp is not None else None
    except Exception:
        return None


def _risk_per_unit(entry: float, stop_loss: float, side: Side) -> Optional[float]:
    if entry <= 0 or stop_loss <= 0:
        return None
    if side == Side.long:
        # stop must be below entry for long
        if stop_loss >= entry:
            return None
        return entry - stop_loss
    # short: stop must be above entry
    if stop_loss <= entry:
        return None
    return stop_loss - entry


def _risk_pct_of_notional(entry: float, stop_loss: float, side: Side) -> Optional[float]:
    r = _risk_per_unit(entry, stop_loss, side)
    if r is None or entry == 0:
        return None
    return r / entry


def _projected_rr(entry: float, stop_loss: float, take_profit: float, side: Side) -> Optional[float]:
    r = _risk_per_unit(entry, stop_loss, side)
    if r is None or r == 0:
        return None
    if side == Side.long:
        if take_profit <= entry:
            return None
        reward = take_profit - entry
    else:
        if take_profit >= entry:
            return None
        reward = entry - take_profit
    if reward <= 0:
        return None
    return reward / r


def evaluate_trade_proposal(
    proposal: TradeProposal,
    *,
    firm_state: Dict[str, Any],
    agent_budget_usdt: float,
    risk_limits: Dict[str, Any],
    market_brief: Optional[Dict[str, Any]] = None,
) -> ComplianceReport:
    """Evaluate a proposal against deterministic hard/soft rules."""
    report = ComplianceReport(
        agent_id=proposal.agent_id,
        run_id=proposal.run_id,
        cycle_id=proposal.cycle_id,
        hard_violations=[],
        soft_violations=[],
        resize_suggestions=[],
        hard_fail=False,
        passed=True,
        notes=None,
    )

    firm_drawdown = float(firm_state.get("drawdown_pct") or 0.0)
    firm_daily_stop = float(risk_limits.get("firm_daily_stop_pct") or 0.0)

    firm_capital = float(firm_state.get("capital_usdt") or 0.0)
    firm_total_notional = float(firm_state.get("total_notional_usdt") or 0.0)
    firm_max_total_mult = float(risk_limits.get("firm_max_total_notional_mult") or 0.0)
    firm_max_total = firm_capital * firm_max_total_mult if firm_capital and firm_max_total_mult else 0.0

    firm_max_leverage = float(risk_limits.get("firm_max_leverage_per_position") or 0.0)
    agent_max_risk_pct = float(risk_limits.get("agent_max_risk_pct_per_trade") or 0.0)
    vol_size_mult = float(risk_limits.get("vol_spike_size_reduction_mult") or 0.0)

    proposed_increase_notional = 0.0
    for trade in proposal.trades:
        if _is_risk_increasing(trade.action):
            proposed_increase_notional += float(trade.size_usdt)

    # Daily stop: no new risk if breached (but allow risk-reducing trades).
    if firm_daily_stop and firm_drawdown >= firm_daily_stop:
        for i, trade in enumerate(proposal.trades):
            if _is_risk_increasing(trade.action):
                report.hard_violations.append(
                    Violation(
                        rule_id="firm.daily_stop",
                        severity=ViolationSeverity.hard,
                        message=(
                            f"Firm drawdown {firm_drawdown:.4f} exceeds daily stop {firm_daily_stop:.4f}; "
                            "no new risk allowed."
                        ),
                        agent_id=proposal.agent_id,
                        symbol=trade.symbol,
                        trade_index=i,
                        data={"drawdown_pct": firm_drawdown, "limit_pct": firm_daily_stop},
                    )
                )

    # Firm max total notional (resizable soft rule).
    if firm_max_total and proposed_increase_notional > 0:
        projected = firm_total_notional + proposed_increase_notional
        if projected > firm_max_total:
            overflow = projected - firm_max_total
            report.soft_violations.append(
                Violation(
                    rule_id="firm.max_total_notional",
                    severity=ViolationSeverity.soft,
                    message=(
                        f"Projected firm notional {projected:.2f} exceeds max {firm_max_total:.2f} "
                        f"by {overflow:.2f}; resize required."
                    ),
                    agent_id=proposal.agent_id,
                    data={
                        "firm_total_notional_usdt": firm_total_notional,
                        "proposed_increase_notional_usdt": proposed_increase_notional,
                        "firm_max_total_notional_usdt": firm_max_total,
                    },
                )
            )
            # Suggest a global size multiplier for this proposal's risk-increasing trades.
            allowed = max(0.0, firm_max_total - firm_total_notional)
            if proposed_increase_notional > 0 and allowed >= 0:
                mult = min(1.0, allowed / proposed_increase_notional) if allowed else 0.0
                report.resize_suggestions.append(
                    ResizeSuggestion(
                        symbol="*",
                        suggested_size_mult=mult,
                        reason="Resize to satisfy firm max total notional constraint.",
                    )
                )

    # Per-trade checks.
    for i, trade in enumerate(proposal.trades):
        increasing = _is_risk_increasing(trade.action)
        reducing = _is_risk_reducing(trade.action)

        # Leverage limit is hard (manager must veto if exceeded).
        if trade.leverage is not None and firm_max_leverage and trade.leverage > firm_max_leverage:
            report.hard_violations.append(
                Violation(
                    rule_id="firm.max_leverage_per_position",
                    severity=ViolationSeverity.hard,
                    message=(
                        f"Leverage {trade.leverage:.2f} exceeds firm max {firm_max_leverage:.2f}."
                    ),
                    agent_id=proposal.agent_id,
                    symbol=trade.symbol,
                    trade_index=i,
                    data={"leverage": trade.leverage, "max": firm_max_leverage},
                )
            )

        # Require stop loss for new risk (hard).
        if increasing and trade.stop_loss is None:
            report.hard_violations.append(
                Violation(
                    rule_id="trade.stop_loss_required",
                    severity=ViolationSeverity.hard,
                    message="Stop loss is required for risk-increasing trades.",
                    agent_id=proposal.agent_id,
                    symbol=trade.symbol,
                    trade_index=i,
                )
            )

        # Stop loss must be on correct side (hard) for risk-increasing trades.
        if increasing and trade.stop_loss is not None:
            entry = _entry_price(trade, market_brief)
            if entry is not None:
                if _risk_per_unit(entry, float(trade.stop_loss), trade.side) is None:
                    report.hard_violations.append(
                        Violation(
                            rule_id="trade.stop_loss_side",
                            severity=ViolationSeverity.hard,
                            message="Stop loss is on the wrong side of entry for the given side.",
                            agent_id=proposal.agent_id,
                            symbol=trade.symbol,
                            trade_index=i,
                            data={"entry": entry, "stop_loss": trade.stop_loss, "side": trade.side},
                        )
                    )

        # Budget is soft (resizable) for risk-increasing trades.
        if increasing and agent_budget_usdt and trade.size_usdt > agent_budget_usdt:
            report.soft_violations.append(
                Violation(
                    rule_id="agent.budget_cap",
                    severity=ViolationSeverity.soft,
                    message=(
                        f"Trade size {trade.size_usdt:.2f} exceeds agent budget cap "
                        f"{agent_budget_usdt:.2f}; resize required."
                    ),
                    agent_id=proposal.agent_id,
                    symbol=trade.symbol,
                    trade_index=i,
                    data={"size_usdt": trade.size_usdt, "agent_budget_usdt": agent_budget_usdt},
                )
            )
            report.resize_suggestions.append(
                ResizeSuggestion(
                    symbol=trade.symbol,
                    trade_index=i,
                    suggested_size_usdt=agent_budget_usdt,
                    reason="Resize down to agent budget cap.",
                )
            )

        # Per-trade risk % of agent budget (soft; resizable) if entry + stop known.
        if increasing and agent_budget_usdt and agent_max_risk_pct and trade.stop_loss is not None:
            entry = _entry_price(trade, market_brief)
            if entry is None:
                report.soft_violations.append(
                    Violation(
                        rule_id="agent.risk_per_trade_uncomputable",
                        severity=ViolationSeverity.soft,
                        message="Cannot compute per-trade risk without an entry price; add limit_price or provide mark_price context.",
                        agent_id=proposal.agent_id,
                        symbol=trade.symbol,
                        trade_index=i,
                    )
                )
            else:
                rp = _risk_pct_of_notional(entry, float(trade.stop_loss), trade.side)
                if rp is None:
                    # Wrong-side stop already caught above; keep this as soft backstop.
                    report.soft_violations.append(
                        Violation(
                            rule_id="agent.risk_per_trade_invalid_stop",
                            severity=ViolationSeverity.soft,
                            message="Cannot compute risk: stop appears invalid relative to entry.",
                            agent_id=proposal.agent_id,
                            symbol=trade.symbol,
                            trade_index=i,
                            data={"entry": entry, "stop_loss": trade.stop_loss, "side": trade.side},
                        )
                    )
                else:
                    risk_usdt = float(trade.size_usdt) * rp
                    risk_pct_budget = risk_usdt / agent_budget_usdt if agent_budget_usdt else 0.0
                    if risk_pct_budget > agent_max_risk_pct:
                        report.soft_violations.append(
                            Violation(
                                rule_id="agent.risk_per_trade_pct",
                                severity=ViolationSeverity.soft,
                                message=(
                                    f"Estimated risk {risk_pct_budget:.4f} of agent budget exceeds "
                                    f"max {agent_max_risk_pct:.4f}; resize required."
                                ),
                                agent_id=proposal.agent_id,
                                symbol=trade.symbol,
                                trade_index=i,
                                data={
                                    "risk_pct_budget": risk_pct_budget,
                                    "max_risk_pct_budget": agent_max_risk_pct,
                                    "entry": entry,
                                    "stop_loss": trade.stop_loss,
                                },
                            )
                        )
                        target_size = float(trade.size_usdt) * (agent_max_risk_pct / risk_pct_budget)
                        report.resize_suggestions.append(
                            ResizeSuggestion(
                                symbol=trade.symbol,
                                trade_index=i,
                                suggested_size_usdt=max(0.0, target_size),
                                reason="Resize down to satisfy max per-trade risk % of budget.",
                            )
                        )

        # Volatility spike circuit breaker (soft; resizable). Apply only for new risk.
        if increasing and vol_size_mult and market_brief:
            try:
                per_symbol = (market_brief.get("per_symbol") or {}).get(trade.symbol) or {}
                tf = (market_brief.get("market_metrics") or {}).get("breadth", {}).get("timeframe")
                # Fall back to 1h if unknown; the brief uses 1h summary by default.
                tf = tf or "1h"
                vol_regime = (
                    ((per_symbol.get("timeframes") or {}).get(tf) or {})
                    .get("indicators", {})
                    .get("vol_regime")
                )
            except Exception:
                vol_regime = None

            if vol_regime == "high_vol":
                report.soft_violations.append(
                    Violation(
                        rule_id="circuit.vol_spike_size_reduction",
                        severity=ViolationSeverity.soft,
                        message=(
                            f"High volatility regime detected ({vol_regime}); "
                            f"reduce size by {vol_size_mult:.2f}x."
                        ),
                        agent_id=proposal.agent_id,
                        symbol=trade.symbol,
                        trade_index=i,
                        data={"vol_regime": vol_regime, "size_mult": vol_size_mult},
                    )
                )
                report.resize_suggestions.append(
                    ResizeSuggestion(
                        symbol=trade.symbol,
                        trade_index=i,
                        suggested_size_mult=vol_size_mult,
                        reason="Circuit breaker: reduce size during volatility spike regime.",
                    )
                )

        # Missing take profit is soft (manager can accept but should be aware).
        if increasing and trade.take_profit is None:
            report.soft_violations.append(
                Violation(
                    rule_id="trade.take_profit_missing",
                    severity=ViolationSeverity.soft,
                    message="Take profit is missing; consider defining an exit target or conditions.",
                    agent_id=proposal.agent_id,
                    symbol=trade.symbol,
                    trade_index=i,
                )
            )

        # RR sanity (soft) if all prices known.
        if increasing and trade.stop_loss is not None and trade.take_profit is not None:
            entry = _entry_price(trade, market_brief)
            if entry is not None:
                rr = _projected_rr(
                    entry, float(trade.stop_loss), float(trade.take_profit), trade.side
                )
                if rr is not None and rr < 1.0:
                    report.soft_violations.append(
                        Violation(
                            rule_id="trade.rr_below_1",
                            severity=ViolationSeverity.soft,
                            message=f"Projected risk:reward {rr:.2f} is below 1.0.",
                            agent_id=proposal.agent_id,
                            symbol=trade.symbol,
                            trade_index=i,
                            data={"rr": rr},
                        )
                    )

        # Risk reducing trades should generally be allowed even under constraints.
        # (We intentionally do not block reduce/close actions here.)
        _ = reducing

    report.hard_fail = len(report.hard_violations) > 0
    report.passed = not report.hard_fail

    if report.hard_fail:
        report.notes = "Hard violations present: manager must veto affected new-risk trades."
    elif report.soft_violations:
        report.notes = "Soft violations present: manager may resize per suggestions."
    else:
        report.notes = "No violations detected."

    return report


__all__ = ["evaluate_trade_proposal"]

