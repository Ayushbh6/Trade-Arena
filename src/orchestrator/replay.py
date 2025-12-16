"""Replay runner (Phase 10.2).

Replays a historical run window by:
- reading the original cycle timeline from audit_log (cycle_start -> snapshot_ref)
- loading the exact stored market snapshot per cycle
- re-running traders + manager (no live tools, no execution)
- computing a schema-level diff (original vs replay) for proposals/decision
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from src.data.mongo import MongoManager, jsonify
from src.data.schemas import AUDIT_LOG, MANAGER_DECISIONS, MARKET_SNAPSHOTS, TRADE_PROPOSALS
from src.orchestrator.orchestrator import CycleResult, Orchestrator


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_ts_utc(s: str) -> datetime:
    # Accept either "2025-12-15T12:34:56Z" or "+00:00".
    t = s.strip().replace("Z", "+00:00")
    return _utc(datetime.fromisoformat(t))


def _deep_diff(a: Any, b: Any, *, path: str = "$") -> List[Dict[str, Any]]:
    if type(a) != type(b):
        return [{"path": path, "a": a, "b": b}]
    if isinstance(a, dict):
        out: List[Dict[str, Any]] = []
        keys = sorted(set(a.keys()) | set(b.keys()))
        for k in keys:
            out.extend(_deep_diff(a.get(k), b.get(k), path=f"{path}.{k}"))
        return out
    if isinstance(a, list):
        out = []
        if len(a) != len(b):
            out.append({"path": f"{path}.length", "a": len(a), "b": len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_deep_diff(x, y, path=f"{path}[{i}]"))
        return out
    if a != b:
        return [{"path": path, "a": a, "b": b}]
    return []


@dataclass(frozen=True)
class ReplayCycleBundle:
    cycle_id: str
    cycle_start_ts: datetime
    snapshot: Dict[str, Any]
    models_selected: Dict[str, Any]
    original_proposals: List[Dict[str, Any]]
    original_manager_decision: Optional[Dict[str, Any]]


async def _find_one_audit(
    mongo: MongoManager,
    *,
    run_id: str,
    event_type: str,
    cycle_id: str,
) -> Optional[Dict[str, Any]]:
    await mongo.connect()
    return await mongo.collection(AUDIT_LOG).find_one(
        {"run_id": run_id, "event_type": event_type, "payload.cycle_id": cycle_id}
    )


async def fetch_replay_bundles(
    *,
    mongo: MongoManager,
    source_run_id: str,
    start: datetime,
    end: datetime,
) -> List[ReplayCycleBundle]:
    await mongo.connect()
    q = {
        "run_id": source_run_id,
        "event_type": "cycle_start",
        "timestamp": {"$gte": _utc(start), "$lt": _utc(end)},
    }
    cycle_starts = (
        await mongo.collection(AUDIT_LOG).find(q).sort("timestamp", 1).to_list(length=2000)
    )
    out: List[ReplayCycleBundle] = []
    for cs in cycle_starts or []:
        ts = cs.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        payload = cs.get("payload") or {}
        cycle_id = payload.get("cycle_id")
        if not isinstance(cycle_id, str) or not cycle_id:
            continue

        ms_ready = await _find_one_audit(
            mongo, run_id=source_run_id, event_type="market_snapshot_ready", cycle_id=cycle_id
        )
        if not ms_ready:
            continue
        snap_ref = (ms_ready.get("payload") or {}).get("snapshot_ref")
        if not snap_ref:
            continue
        try:
            snapshot_id = ObjectId(str(snap_ref))
        except Exception:
            continue
        snapshot = await mongo.collection(MARKET_SNAPSHOTS).find_one({"_id": snapshot_id})
        if not snapshot:
            continue

        models_evt = await _find_one_audit(
            mongo, run_id=source_run_id, event_type="models_selected", cycle_id=cycle_id
        )
        models_selected = (models_evt or {}).get("payload") or {}

        original_proposals = await mongo.collection(TRADE_PROPOSALS).find(
            {"run_id": source_run_id, "cycle_id": cycle_id}
        ).to_list(length=20)
        original_manager_decision = await mongo.collection(MANAGER_DECISIONS).find_one(
            {"run_id": source_run_id, "cycle_id": cycle_id}
        )

        out.append(
            ReplayCycleBundle(
                cycle_id=cycle_id,
                cycle_start_ts=_utc(ts),
                snapshot=snapshot,
                models_selected=models_selected,
                original_proposals=original_proposals or [],
                original_manager_decision=original_manager_decision,
            )
        )
    return out


async def run_replay(
    *,
    orchestrator: Orchestrator,
    source_run_id: str,
    replay_run_id: str,
    start: datetime,
    end: datetime,
    model_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    mongo = orchestrator.mongo
    bundles = await fetch_replay_bundles(mongo=mongo, source_run_id=source_run_id, start=start, end=end)

    report: Dict[str, Any] = {
        "ok": True,
        "source_run_id": source_run_id,
        "replay_run_id": replay_run_id,
        "start": _utc(start),
        "end": _utc(end),
        "cycles": [],
    }

    for b in bundles:
        selected = b.models_selected.get("trader_models") or {}
        models: Dict[str, str] = {
            "tech_trader_1": selected.get("tech_trader_1"),
            "tech_trader_2": selected.get("tech_trader_2"),
            "macro_trader_1": selected.get("macro_trader_1"),
            "structure_trader_1": selected.get("structure_trader_1"),
            "manager": b.models_selected.get("manager_model"),
        }
        models = {k: v for k, v in models.items() if isinstance(v, str) and v}
        if model_overrides:
            models.update({k: v for k, v in model_overrides.items() if isinstance(v, str) and v})

        res: CycleResult = await orchestrator.run_cycle_from_snapshot(
            source_run_id=source_run_id,
            snapshot=b.snapshot,
            run_id=replay_run_id,
            cycle_id=b.cycle_id,
            models=models or None,
        )

        # Compare (schema-level) against original stored docs.
        orig_by_agent = {p.get("agent_id"): jsonify(p) for p in (b.original_proposals or []) if p.get("agent_id")}
        replay_by_agent = {p.agent_id: jsonify(p.model_dump(mode="json")) for p in (res.proposals or [])}

        prop_diffs: Dict[str, Any] = {}
        for agent_id in sorted(set(orig_by_agent.keys()) | set(replay_by_agent.keys())):
            a = orig_by_agent.get(agent_id)
            r = replay_by_agent.get(agent_id)
            if a is None or r is None:
                prop_diffs[agent_id] = {"missing": True, "original": bool(a), "replay": bool(r)}
                continue
            prop_diffs[agent_id] = {
                "diffs": _deep_diff(a, r),
                "changed": a != r,
            }

        orig_md = jsonify(b.original_manager_decision) if b.original_manager_decision else None
        replay_md = (
            jsonify(res.manager_decision.model_dump(mode="json")) if res.manager_decision is not None else None
        )
        md_diff = None
        if orig_md is None and replay_md is None:
            md_diff = {"missing": True, "original": False, "replay": False}
        elif orig_md is None or replay_md is None:
            md_diff = {"missing": True, "original": bool(orig_md), "replay": bool(replay_md)}
        else:
            md_diff = {"diffs": _deep_diff(orig_md, replay_md), "changed": orig_md != replay_md}

        report["cycles"].append(
            {
                "cycle_id": b.cycle_id,
                "cycle_start_ts": b.cycle_start_ts,
                "source_snapshot_id": str(b.snapshot.get("_id")),
                "proposals_diff": prop_diffs,
                "manager_decision_diff": md_diff,
            }
        )

    return jsonify(report)


__all__ = ["parse_ts_utc", "fetch_replay_bundles", "run_replay"]
