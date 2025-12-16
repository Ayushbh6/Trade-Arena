"""FastAPI API for dashboard/UI (Phase 11).

API is read-only for trading (it never triggers execution).
It exposes MongoDB state + a WebSocket live feed for realtime dashboards.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import (
    AGENT_STATES,
    AUDIT_LOG,
    MANAGER_DECISIONS,
    MARKET_SNAPSHOTS,
    ORDERS,
    PNL_REPORTS,
    POSITIONS,
    RUN_SESSIONS,
    TRADE_PROPOSALS,
)
from src.ui.auth import check_login, create_token, verify_token


def _parse_origins(value: str) -> List[str]:
    v = (value or "*").strip()
    if v == "*":
        return ["*"]
    return [p.strip() for p in v.split(",") if p.strip()]


def _serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (doc or {}).items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            out[k] = dt.isoformat()
        else:
            out[k] = jsonify(v)
    if "_id" in out and not isinstance(out["_id"], str):
        out["_id"] = str(out["_id"])
    return out


def _json_safe(value: Any) -> Any:
    """Convert values to JSON-serializable types for API/WS responses."""
    # Keep this separate from src.data.mongo.jsonify (which preserves datetime for BSON).
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _json_safe(value.dict())
        except Exception:
            pass
    return str(value)


async def _resolve_run_id(mongo: MongoManager, run_id: Optional[str]) -> Optional[str]:
    if run_id:
        return run_id
    await mongo.connect()
    doc = await mongo.collection(RUN_SESSIONS).find_one({}, sort=[("created_at", -1)])
    if isinstance(doc, dict) and doc.get("run_id"):
        return str(doc["run_id"])
    doc2 = await mongo.collection(TRADE_PROPOSALS).find_one({}, sort=[("timestamp", -1)])
    if isinstance(doc2, dict) and doc2.get("run_id"):
        return str(doc2["run_id"])
    doc3 = await mongo.collection(AUDIT_LOG).find_one({}, sort=[("timestamp", -1)])
    if isinstance(doc3, dict) and doc3.get("run_id"):
        return str(doc3["run_id"])
    return None


def _auth_enabled() -> bool:
    return os.getenv("UI_AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes", "y", "on")


def _require_token(token: Optional[str]) -> str:
    if not _auth_enabled():
        return "anonymous"
    if not token:
        raise HTTPException(status_code=401, detail="missing_token")
    claims = verify_token(token=token)
    if not claims:
        raise HTTPException(status_code=401, detail="invalid_token")
    return claims.sub


def _bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except Exception:
        return None


def _canonical_agent_ids() -> List[str]:
    v = os.getenv(
        "UI_CANONICAL_AGENTS",
        "tech_trader_1,tech_trader_2,macro_trader_1,structure_trader_1,manager",
    )
    return [p.strip() for p in (v or "").split(",") if p.strip()]


def _canonical_traders() -> List[str]:
    return [a for a in _canonical_agent_ids() if a != "manager"]


def _default_role_for_agent(agent_id: str) -> str:
    if agent_id.startswith("tech_trader_"):
        return "technical"
    if agent_id.startswith("macro_trader_"):
        return "macro"
    if agent_id.startswith("structure_trader_"):
        return "structure"
    return "agent"


def _titleize_token(s: str) -> str:
    s2 = (s or "").strip()
    if not s2:
        return ""
    parts = []
    for p in re.split(r"[\s\-_]+", s2):
        if not p:
            continue
        parts.append(p[:1].upper() + p[1:].lower())
    return " ".join(parts)


def _short_model_name(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    # model is typically "provider/name". We display only the name part.
    name = str(model).split("/", 1)[-1]
    return _titleize_token(name.replace(".", " ").replace(":", " "))


def _tool_display_name(tool: str) -> str:
    # e.g. get_market_brief -> Get Market Brief
    t = (tool or "").strip()
    if not t:
        return ""
    t = t.replace("get_", "get ").replace("tavily_", "tavily ")
    return _titleize_token(t)


def _allowed_tools_by_agent() -> Dict[str, Any]:
    # Keep in sync with src/orchestrator/orchestrator.py wiring.
    return {
        "tech_trader_1": [
            "get_market_brief",
            "get_candles",
            "get_indicator_pack",
            "get_position_summary",
            "get_firm_state",
            "query_memory",
        ],
        "tech_trader_2": [
            "get_market_brief",
            "get_candles",
            "get_indicator_pack",
            "get_position_summary",
            "get_firm_state",
            "query_memory",
        ],
        "macro_trader_1": [
            "get_market_brief",
            "get_recent_news",
            "tavily_search",
            "get_position_summary",
            "get_firm_state",
            "query_memory",
        ],
        "structure_trader_1": [
            "get_market_brief",
            "get_funding_oi_history",
            "get_orderbook_top",
            "get_candles",
            "get_indicator_pack",
            "get_position_summary",
            "get_firm_state",
            "query_memory",
        ],
        # Manager has access to all registered tools (but is limited by max tool calls).
        "manager": {"access": "all", "max_tool_calls": 4},
    }


def _env_trader_model(agent_id: str) -> Optional[str]:
    mapping = {
        "tech_trader_1": "LLM_MODEL_TRADER_1",
        "tech_trader_2": "LLM_MODEL_TRADER_2",
        "macro_trader_1": "LLM_MODEL_TRADER_3",
        "structure_trader_1": "LLM_MODEL_TRADER_4",
    }
    env_key = mapping.get(agent_id)
    if not env_key:
        return None
    v = os.getenv(env_key)
    return str(v) if v else None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int


def create_app() -> FastAPI:
    # Uvicorn does not automatically load `.env` unless you pass `--env-file`.
    # For local/dev ergonomics, best-effort load here without overriding real env.
    try:  # pragma: no cover
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass

    app = FastAPI(title="AI-Native Trader Co. API", version="0.1.0")
    mongo = MongoManager(db_name=os.getenv("MONGODB_DB", "investment"))
    app.state.mongo = mongo

    allowed_origins = _parse_origins(os.getenv("UI_ALLOWED_ORIGINS", "*"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,  # token auth; no cookies needed
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        await mongo.connect()
        await mongo.ensure_indexes()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await mongo.close()

    async def get_mongo() -> MongoManager:
        return app.state.mongo

    async def auth_user(
        authorization: Optional[str] = Header(None),
        token_q: Optional[str] = Query(None, alias="token"),
    ) -> str:
        token = token_q or _bearer_token(authorization)
        return _require_token(token)

    @app.get("/healthz")
    async def healthz(m: MongoManager = Depends(get_mongo)) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, None)
        return {"ok": True, "time": utc_now().isoformat(), "run_id": rid, "auth_enabled": _auth_enabled()}

    @app.post("/auth/login", response_model=LoginResponse)
    async def login(req: LoginRequest) -> LoginResponse:
        if not _auth_enabled():
            raise HTTPException(status_code=404, detail="auth_disabled")
        if not check_login(username=req.username, password=req.password):
            raise HTTPException(status_code=401, detail="invalid_credentials")
        ttl = int(os.getenv("UI_TOKEN_TTL_S", str(12 * 60 * 60)))
        token = create_token(username=req.username, ttl_seconds=ttl)
        return LoginResponse(token=token, expires_in=ttl)

    @app.get("/agents")
    async def list_agents(
        run_id: Optional[str] = None,
        canonical_only: bool = True,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        canonical = set(_canonical_agent_ids())
        active_ids: List[str] = []
        if rid:
            recent = (
                await m.collection(TRADE_PROPOSALS)
                .find({"run_id": rid}, projection={"agent_id": 1})
                .sort("timestamp", -1)
                .limit(200)
                .to_list(length=200)
            )
            seen = set()
            for d in recent or []:
                aid = d.get("agent_id")
                if isinstance(aid, str) and aid and aid not in seen:
                    if canonical_only and canonical and aid not in canonical:
                        continue
                    active_ids.append(aid)
                    seen.add(aid)
        # Default UX: show canonical traders only (hide old test agents in DB).
        if canonical_only:
            want = active_ids or _canonical_traders()
            docs = (
                await m.collection(AGENT_STATES)
                .find({"agent_id": {"$in": want}})
                .sort("agent_id", 1)
                .limit(limit)
                .to_list(length=limit)
            )
            by_id = {str(d.get("agent_id")): d for d in (docs or []) if isinstance(d, dict) and d.get("agent_id")}
            merged: List[Dict[str, Any]] = []
            for aid in want:
                if aid in by_id:
                    merged.append(_serialize_doc(by_id[aid]))
                else:
                    merged.append({"agent_id": aid, "role": _default_role_for_agent(aid)})
            return {"run_id": rid, "agents": merged[:limit]}

        # Debug/ops mode: show all.
        docs = await m.collection(AGENT_STATES).find({}).sort("agent_id", 1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "agents": [_serialize_doc(d) for d in (docs or [])]}

    @app.get("/models")
    async def models(
        run_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)

        selected: Dict[str, Any] = {}
        if rid:
            evt = await m.collection(AUDIT_LOG).find_one(
                {"run_id": rid, "event_type": "models_selected"}, sort=[("timestamp", -1)]
            )
            selected = (evt or {}).get("payload") or {}

        trader_models = dict(selected.get("trader_models") or {})
        for aid in _canonical_traders():
            if not trader_models.get(aid):
                env_m = _env_trader_model(aid)
                if env_m:
                    trader_models[aid] = env_m

        manager_model = selected.get("manager_model") or os.getenv("LLM_MODEL_MANAGER")

        tools_map = _allowed_tools_by_agent()
        out: List[Dict[str, Any]] = []
        for agent_id in _canonical_traders():
            tools = tools_map.get(agent_id) or []
            full = trader_models.get(agent_id)
            out.append(
                {
                    "agent_id": agent_id,
                    "role": _default_role_for_agent(agent_id),
                    "llm_model_full": full,
                    "llm_model_name": _short_model_name(full),
                    "tools": [{"name": t, "label": _tool_display_name(t)} for t in tools],
                }
            )
        out.append(
            {
                "agent_id": "manager",
                "role": "manager",
                "llm_model_full": manager_model,
                "llm_model_name": _short_model_name(manager_model),
                "tools": tools_map.get("manager"),
            }
        )

        return {"run_id": rid, "models": _json_safe(out)}

    @app.get("/agents/{agent_id}/positions")
    async def agent_positions(
        agent_id: str,
        run_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(200, ge=1, le=2000),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "positions": []}
        q = {"run_id": rid, "agent_owner": agent_id}
        docs = await m.collection(POSITIONS).find(q).sort("symbol", 1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "positions": [_serialize_doc(d) for d in (docs or [])]}

    @app.get("/positions")
    async def positions(
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(200, ge=1, le=2000),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "positions": []}
        q: Dict[str, Any] = {"run_id": rid}
        if agent_id:
            q["agent_owner"] = agent_id
        docs = await m.collection(POSITIONS).find(q).sort("symbol", 1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "positions": [_serialize_doc(d) for d in (docs or [])]}

    @app.get("/orders")
    async def orders(
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(200, ge=1, le=2000),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "orders": []}
        q: Dict[str, Any] = {"run_id": rid}
        if agent_id:
            q["agent_id"] = agent_id
        if cycle_id:
            q["cycle_id"] = cycle_id
        docs = await m.collection(ORDERS).find(q).sort("timestamp", -1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "orders": [_serialize_doc(d) for d in reversed(docs or [])]}

    @app.get("/proposals")
    async def proposals(
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        canonical_only: bool = True,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "proposals": []}
        q: Dict[str, Any] = {"run_id": rid}
        if agent_id:
            q["agent_id"] = agent_id
        elif canonical_only:
            canonical = [a for a in _canonical_agent_ids() if a != "manager"]
            q["agent_id"] = {"$in": canonical}
        if cycle_id:
            q["cycle_id"] = cycle_id
        docs = await m.collection(TRADE_PROPOSALS).find(q).sort("timestamp", -1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "proposals": [_serialize_doc(d) for d in (docs or [])]}

    @app.get("/decisions")
    async def decisions(
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "decisions": []}
        q: Dict[str, Any] = {"run_id": rid}
        if cycle_id:
            q["cycle_id"] = cycle_id
        docs = await m.collection(MANAGER_DECISIONS).find(q).sort("timestamp", -1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "decisions": [_serialize_doc(d) for d in (docs or [])]}

    @app.get("/pnl")
    async def pnl(
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "latest": None}
        q: Dict[str, Any] = {"run_id": rid}
        if cycle_id:
            q["cycle_id"] = cycle_id
        doc = await m.collection(PNL_REPORTS).find_one(q, sort=[("timestamp", -1)])
        return {"run_id": rid, "latest": _serialize_doc(doc) if doc else None}

    @app.get("/pnl/history")
    async def pnl_history(
        run_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(500, ge=10, le=2000),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "reports": []}
        q: Dict[str, Any] = {"run_id": rid}
        if since_ts:
            try:
                ts = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
                q["timestamp"] = {"$gte": ts}
            except Exception:
                raise HTTPException(status_code=400, detail="invalid_since_ts") from None
        docs = await m.collection(PNL_REPORTS).find(q).sort("timestamp", -1).limit(limit).to_list(length=limit)
        # Return in chronological order for charting.
        return {"run_id": rid, "reports": [_serialize_doc(d) for d in reversed(docs or [])]}

    @app.get("/cycles")
    async def cycles(
        run_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "cycles": []}
        docs = (
            await m.collection(AUDIT_LOG)
            .find({"run_id": rid, "event_type": "cycle_start"})
            .sort("timestamp", -1)
            .limit(limit)
            .to_list(length=limit)
        )
        out = []
        for d in docs or []:
            out.append({"cycle_id": d.get("payload", {}).get("cycle_id") or d.get("cycle_id"), "timestamp": d.get("timestamp")})
        return {"run_id": rid, "cycles": [_serialize_doc(c) for c in reversed(out)]}

    @app.get("/audit")
    async def audit(
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
        limit: int = Query(200, ge=1, le=2000),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "events": []}
        q: Dict[str, Any] = {"run_id": rid}
        if event_type:
            q["event_type"] = event_type
        if agent_id:
            q["agent_id"] = agent_id
        if since_ts:
            try:
                ts = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
                q["timestamp"] = {"$gt": ts}
            except Exception:
                raise HTTPException(status_code=400, detail="invalid_since_ts") from None
        docs = await m.collection(AUDIT_LOG).find(q).sort("timestamp", -1).limit(limit).to_list(length=limit)
        return {"run_id": rid, "events": [_serialize_doc(d) for d in reversed(docs or [])]}

    @app.get("/market/summary")
    async def market_summary(
        symbols: Optional[str] = None,
        timeframe: str = "5m",
        run_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "snapshot": None}
        doc = await m.collection(MARKET_SNAPSHOTS).find_one({"run_id": rid}, sort=[("timestamp", -1)])
        if not doc:
            return {"run_id": rid, "snapshot": None}
        per = doc.get("per_symbol") or {}
        want = [s.strip() for s in (symbols.split(",") if symbols else []) if s.strip()]
        if not want:
            want = list(doc.get("symbols") or [])
        out: Dict[str, Any] = {}
        for sym in want:
            p = per.get(sym) or {}
            candles = ((p.get("candles") or {}).get(timeframe) or [])
            last = candles[-1] if isinstance(candles, list) and candles else None
            tob = p.get("top_of_book") or {}
            out[sym] = {
                "mark_price": p.get("mark_price"),
                "funding_rate": p.get("funding_rate"),
                "open_interest": p.get("open_interest"),
                "top_of_book": {"bid": tob.get("bid"), "ask": tob.get("ask"), "spread": tob.get("spread")},
                "last_candle": last,
            }
        snap = {"_id": str(doc.get("_id")) if doc.get("_id") is not None else None, "timestamp": doc.get("timestamp")}
        return {"run_id": rid, "snapshot": _serialize_doc(snap), "symbols": out}

    @app.get("/market/candles")
    async def market_candles(
        symbol: str,
        timeframe: str = "5m",
        limit: int = Query(300, ge=10, le=1000),
        run_id: Optional[str] = None,
        _user: str = Depends(auth_user),
        m: MongoManager = Depends(get_mongo),
    ) -> Dict[str, Any]:
        await m.connect()
        rid = await _resolve_run_id(m, run_id)
        if not rid:
            return {"run_id": None, "symbol": symbol, "timeframe": timeframe, "candles": []}
        doc = await m.collection(MARKET_SNAPSHOTS).find_one({"run_id": rid}, sort=[("timestamp", -1)])
        if not doc:
            return {"run_id": rid, "symbol": symbol, "timeframe": timeframe, "candles": []}
        per = doc.get("per_symbol") or {}
        p = per.get(symbol) or {}
        candles = ((p.get("candles") or {}).get(timeframe) or [])
        if not isinstance(candles, list):
            candles = []
        candles = candles[-int(limit) :]
        # Lightweight-charts expects seconds, not ms.
        out = []
        for c in candles:
            if not isinstance(c, dict):
                continue
            t_ms = c.get("open_time_ms")
            if t_ms is None:
                continue
            o = _to_float(c.get("open"))
            h = _to_float(c.get("high"))
            l = _to_float(c.get("low"))
            cl = _to_float(c.get("close"))
            if o is None or h is None or l is None or cl is None:
                continue
            out.append(
                {
                    "time": int(t_ms) // 1000,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "volume": _to_float(c.get("volume")),
                }
            )
        snap = {"_id": str(doc.get("_id")) if doc.get("_id") is not None else None, "timestamp": doc.get("timestamp")}
        return {"run_id": rid, "snapshot": _serialize_doc(snap), "symbol": symbol, "timeframe": timeframe, "candles": jsonify(out)}

    @app.websocket("/live")
    async def live(
        websocket: WebSocket,
        run_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        if _auth_enabled():
            claims = verify_token(token=token or "")
            if not claims:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        m: MongoManager = app.state.mongo
        rid = await _resolve_run_id(m, run_id)
        poll_s = float(os.getenv("UI_WS_POLL_INTERVAL_S", "1.0"))

        last_ts = utc_now()
        last_id: Optional[ObjectId] = None

        async def send_json(obj: Dict[str, Any]) -> None:
            await websocket.send_json(_json_safe(obj))

        # Initial snapshot (lightweight)
        agents_doc = await m.collection(AGENT_STATES).find({}).sort("agent_id", 1).to_list(length=200)
        proposals_doc = (
            await m.collection(TRADE_PROPOSALS).find({"run_id": rid}).sort("timestamp", -1).limit(30).to_list(length=30)
            if rid
            else []
        )
        decisions_doc = (
            await m.collection(MANAGER_DECISIONS).find({"run_id": rid}).sort("timestamp", -1).limit(10).to_list(length=10)
            if rid
            else []
        )
        pnl_doc = (
            await m.collection(PNL_REPORTS).find_one({"run_id": rid}, sort=[("timestamp", -1)]) if rid else None
        )
        await send_json(
            {
                "type": "hello",
                "run_id": rid,
                "server_time": utc_now().isoformat(),
                "auth_enabled": _auth_enabled(),
            }
        )
        await send_json(
            {
                "type": "snapshot",
                "data": {
                    "agents": [_serialize_doc(d) for d in (agents_doc or [])],
                    "proposals": [_serialize_doc(d) for d in (proposals_doc or [])],
                    "decisions": [_serialize_doc(d) for d in (decisions_doc or [])],
                    "pnl_latest": _serialize_doc(pnl_doc) if pnl_doc else None,
                },
            }
        )

        try:
            while True:
                # Tail audit_log for this run_id
                if not rid:
                    await asyncio.sleep(poll_s)
                    continue
                q: Dict[str, Any] = {"run_id": rid}
                if last_id is None:
                    q["timestamp"] = {"$gt": last_ts}
                else:
                    q["$or"] = [
                        {"timestamp": {"$gt": last_ts}},
                        {"timestamp": {"$eq": last_ts}, "_id": {"$gt": last_id}},
                    ]
                docs = (
                    await m.collection(AUDIT_LOG)
                    .find(q)
                    .sort([("timestamp", 1), ("_id", 1)])
                    .limit(500)
                    .to_list(length=500)
                )
                if docs:
                    last_doc = docs[-1]
                    last_ts_v = last_doc.get("timestamp")
                    if isinstance(last_ts_v, datetime):
                        last_ts = last_ts_v if last_ts_v.tzinfo else last_ts_v.replace(tzinfo=timezone.utc)
                    if isinstance(last_doc.get("_id"), ObjectId):
                        last_id = last_doc["_id"]
                    await send_json({"type": "audit", "data": [_serialize_doc(d) for d in docs]})

                    event_types = {str(d.get("event_type") or "") for d in docs if isinstance(d, dict)}
                    if "trader_proposals_ready" in event_types:
                        proposals_doc = (
                            await m.collection(TRADE_PROPOSALS)
                            .find({"run_id": rid})
                            .sort("timestamp", -1)
                            .limit(30)
                            .to_list(length=30)
                        )
                        await send_json({"type": "proposals", "data": [_serialize_doc(d) for d in (proposals_doc or [])]})
                    if "manager_decision_ready" in event_types or "manager_decisions_ready" in event_types:
                        decisions_doc = (
                            await m.collection(MANAGER_DECISIONS)
                            .find({"run_id": rid})
                            .sort("timestamp", -1)
                            .limit(10)
                            .to_list(length=10)
                        )
                        await send_json({"type": "decisions", "data": [_serialize_doc(d) for d in (decisions_doc or [])]})
                    if "pnl_report_generated" in event_types:
                        pnl_doc = await m.collection(PNL_REPORTS).find_one({"run_id": rid}, sort=[("timestamp", -1)])
                        await send_json({"type": "pnl", "data": _serialize_doc(pnl_doc) if pnl_doc else None})
                await asyncio.sleep(poll_s)
        except WebSocketDisconnect:
            return

    return app


app = create_app()
