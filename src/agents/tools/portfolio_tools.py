"""Portfolio/admin tools (deterministic).

These tools are intended for Manager / weekly governance operations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.config import load_config
from src.data.audit import AuditContext, AuditManager
from src.data.mongo import jsonify
from src.portfolio.allocation import RebalancePolicy, apply_rebalanced_budgets
from .context import ToolContext


async def rebalance_budgets(
    *,
    performance_table: List[Dict[str, Any]],
    min_budget_mult: float = 0.5,
    max_budget_mult: float = 2.0,
    max_weekly_change_pct: float = 0.25,
    context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """
    Rebalance per-agent budget caps (USDT notional) deterministically.

    `performance_table` is expected to contain items like:
      {"agent_id": "...", "trust_score": 0-100}
    """
    ctx = context or ToolContext()
    if ctx.mongo is None:
        return {"error": "mongo_not_configured"}

    trust_scores: Dict[str, float] = {}
    for row in performance_table or []:
        if not isinstance(row, dict):
            continue
        aid = row.get("agent_id")
        if not aid:
            continue
        ts = row.get("trust_score")
        if ts is None:
            continue
        try:
            trust_scores[str(aid)] = float(ts)
        except Exception:
            continue

    if not trust_scores:
        return {"error": "empty_or_invalid_performance_table"}

    cfg = ctx.config or load_config()
    policy = RebalancePolicy(
        baseline_budget_usdt=float(cfg.risk.agent_budget_notional_usd),
        min_budget_mult=float(min_budget_mult),
        max_budget_mult=float(max_budget_mult),
        max_weekly_change_pct=float(max_weekly_change_pct),
    )

    await ctx.mongo.connect()
    res = await apply_rebalanced_budgets(
        mongo=ctx.mongo,
        run_id=str(ctx.run_id or "unknown_run"),
        trust_scores=trust_scores,
        policy=policy,
        cfg=cfg,
    )

    # Best-effort audit event for governance.
    try:
        audit = AuditManager(ctx.mongo)
        await audit.log(
            "weekly_rebalance_applied",
            {"result": jsonify(res)},
            ctx=AuditContext(run_id=str(ctx.run_id or "unknown_run"), agent_id=str(ctx.agent_id or "manager")),
        )
    except Exception:
        pass

    return res


__all__ = ["rebalance_budgets"]

