"""Deterministic re-grounding of ledger facts from MongoDB.

Phase 7 invariant: Mongo facts are the source of truth. Any conflicting LLM
memory must be overwritten by re-grounding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agents.memory.ledger import LedgerFacts, LedgerPosition
from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import AGENT_STATES, MANAGER_DECISIONS, PNL_REPORTS, POSITIONS, TRADE_PROPOSALS


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def rebuild_ledger_facts_from_mongo(
    *,
    mongo: MongoManager,
    run_id: str,
    agent_id: str,
    max_outcomes: int = 10,
) -> LedgerFacts:
    """Rebuild deterministic ledger facts for a given (run_id, agent_id)."""
    await mongo.connect()

    # --- Budgets/trust (best-effort; may not exist) ---
    agent_budget: Optional[float] = None
    trust_score: Optional[float] = None
    try:
        state = await mongo.collection(AGENT_STATES).find_one({"agent_id": agent_id})
        if isinstance(state, dict):
            b = state.get("budget_usdt")
            if b is not None:
                agent_budget = float(b)
            ts = state.get("trust_score")
            if ts is not None:
                trust_score = float(ts)
    except Exception:
        agent_budget = None
        trust_score = None

    # --- Positions (best-effort attribution via agent_owner) ---
    positions_docs = (
        await mongo.collection(POSITIONS)
        .find({"run_id": run_id, "agent_owner": agent_id})
        .sort("timestamp", -1)
        .to_list(length=100)
    )
    positions: List[LedgerPosition] = []
    for d in positions_docs or []:
        try:
            positions.append(
                LedgerPosition(
                    symbol=str(d.get("symbol") or ""),
                    qty=float(d.get("qty") or 0.0),
                    avg_entry_price=float(d.get("avg_entry_price") or 0.0),
                    mark_price=float(d.get("mark_price") or 0.0),
                    unrealized_pnl=float(d.get("unrealized_pnl") or 0.0),
                    leverage=float(d.get("leverage") or 0.0),
                    as_of=d.get("timestamp") or _utc_now(),
                    source_ref={"collection": POSITIONS, "id": d.get("_id"), "run_id": run_id},
                )
            )
        except Exception:
            continue

    # --- Recent outcomes (proposal/decision/pnl refs) ---
    proposal_docs = (
        await mongo.collection(TRADE_PROPOSALS)
        .find({"run_id": run_id, "agent_id": agent_id})
        .sort("timestamp", -1)
        .limit(max_outcomes)
        .to_list(length=max_outcomes)
    )
    recent_outcomes: List[Dict[str, Any]] = []
    for p in proposal_docs or []:
        cycle_id = p.get("cycle_id")
        proposal_ref = f"{TRADE_PROPOSALS}:{p.get('_id')}"

        decision_ref = None
        decision_items: List[Dict[str, Any]] = []
        if cycle_id:
            d = await mongo.collection(MANAGER_DECISIONS).find_one({"run_id": run_id, "cycle_id": cycle_id})
            if isinstance(d, dict):
                decision_ref = f"{MANAGER_DECISIONS}:{d.get('_id')}"
                items = d.get("decisions") or []
                if isinstance(items, list):
                    decision_items = [
                        it for it in items
                        if isinstance(it, dict) and (it.get("agent_id") == agent_id)
                    ]

        pnl_ref = None
        if cycle_id:
            r = await mongo.collection(PNL_REPORTS).find_one({"run_id": run_id, "cycle_id": cycle_id})
            if isinstance(r, dict):
                pnl_ref = f"{PNL_REPORTS}:{r.get('_id')}"

        symbols: List[str] = []
        trades = p.get("trades") or []
        if isinstance(trades, list):
            for t in trades:
                if isinstance(t, dict) and t.get("symbol"):
                    symbols.append(str(t["symbol"]))

        recent_outcomes.append(
            jsonify(
                {
                    "cycle_id": cycle_id,
                    "timestamp": p.get("timestamp"),
                    "symbols": sorted(set(symbols)),
                    "proposal_ref": proposal_ref,
                    "decision_ref": decision_ref,
                    "decision_items": decision_items,
                    "pnl_ref": pnl_ref,
                }
            )
        )

    return LedgerFacts(
        capital_usdt=None,
        agent_budget_usdt=agent_budget,
        trust_score=trust_score,
        positions=positions,
        recent_outcomes=recent_outcomes,
        as_of=utc_now(),
    )


__all__ = ["rebuild_ledger_facts_from_mongo"]

