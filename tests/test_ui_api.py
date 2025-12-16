import os
import asyncio
import sys
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")

from fastapi.testclient import TestClient

from src.data.mongo import MongoManager, utc_now
from src.data.schemas import (
    AGENT_STATES,
    AUDIT_LOG,
    MANAGER_DECISIONS,
    MARKET_SNAPSHOTS,
    PNL_REPORTS,
    POSITIONS,
    RUN_SESSIONS,
    TRADE_PROPOSALS,
)
from src.ui.auth import create_token


async def _seed(m: MongoManager, run_id: str) -> None:
    await m.connect()
    now = utc_now()
    await m.collection(RUN_SESSIONS).insert_one(
        {"run_id": run_id, "status": "running", "created_at": now, "updated_at": now, "config": {}}
    )
    agents = [
        ("tech_trader_1", "technical", 10000.0, 55.0),
        ("tech_trader_2", "technical", 10000.0, 50.0),
        ("macro_trader_1", "macro", 10000.0, 52.0),
        ("structure_trader_1", "structure", 10000.0, 48.0),
    ]
    for agent_id, role, budget, trust in agents:
        await m.collection(AGENT_STATES).update_one(
            {"agent_id": agent_id},
            {"$set": {"agent_id": agent_id, "role": role, "budget_usdt": budget, "trust_score": trust}},
            upsert=True,
        )
        await m.collection(TRADE_PROPOSALS).insert_one(
            {"run_id": run_id, "timestamp": now, "agent_id": agent_id, "trades": [], "notes": "ok"}
        )

    await m.collection(POSITIONS).insert_one(
        {"run_id": run_id, "symbol": "BTCUSDT", "qty": 0.1, "agent_owner": "tech_trader_1"}
    )
    await m.collection(MANAGER_DECISIONS).insert_one(
        {"run_id": run_id, "timestamp": now, "manager_id": "manager", "decisions": [], "notes": "ok"}
    )
    await m.collection(PNL_REPORTS).insert_one({"run_id": run_id, "timestamp": now, "firm_metrics": {"equity": 10000.0}})
    await m.collection(AUDIT_LOG).insert_one({"run_id": run_id, "timestamp": now, "event_type": "cycle_start", "payload": {}})
    await m.collection(AUDIT_LOG).insert_one(
        {
            "run_id": run_id,
            "timestamp": now,
            "event_type": "models_selected",
            "payload": {
                "trader_models": {
                    "tech_trader_1": "openai/gpt-4.1-mini",
                    "tech_trader_2": "openai/gpt-4.1-mini",
                    "macro_trader_1": "google/gemini-2.5-flash",
                    "structure_trader_1": "qwen/qwen3-coder",
                },
                "manager_model": "deepseek/deepseek-chat",
            },
        }
    )
    # Minimal market snapshot so UI charts render in local/dev.
    t0 = int(now.timestamp() * 1000)
    candles_5m = []
    px = 100000.0
    for i in range(60):  # 5h of 5m bars
        ts = t0 - (60 - i) * 5 * 60 * 1000
        o = px + i * 10.0
        candles_5m.append(
            {
                "open_time_ms": ts,
                "open": o,
                "high": o + 15.0,
                "low": o - 12.0,
                "close": o + 4.0,
                "volume": 123.0 + i,
            }
        )
    await m.collection(MARKET_SNAPSHOTS).insert_one(
        {
            "run_id": run_id,
            "timestamp": now,
            "symbols": ["BTCUSDT"],
            "per_symbol": {
                "BTCUSDT": {
                    "mark_price": candles_5m[-1]["close"],
                    "candles": {"5m": candles_5m},
                    "top_of_book": {"bid": candles_5m[-1]["close"] - 1.0, "ask": candles_5m[-1]["close"] + 1.0},
                }
            },
        }
    )


def main() -> None:
    print("== Phase 11 API smoke test ==")
    os.environ["UI_AUTH_ENABLED"] = "true"
    run_id = "test_ui_api_run"

    async def setup() -> None:
        m = MongoManager(db_name=os.getenv("MONGODB_DB", "investment"))
        await _seed(m, run_id)
        await m.close()

    asyncio.run(setup())

    from src.ui.api import create_app

    app = create_app()
    token = create_token(username="user001", ttl_seconds=3600)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        r = client.get(f"/agents?run_id={run_id}", headers=headers)
        body = r.json()
        assert r.status_code == 200 and "agents" in body
        assert len(body.get("agents") or []) == 4

        r = client.get(f"/agents/tech_trader_1/positions?run_id={run_id}", headers=headers)
        assert r.status_code == 200 and r.json()["run_id"] == run_id

        r = client.get(f"/proposals?run_id={run_id}", headers=headers)
        assert r.status_code == 200 and len(r.json()["proposals"]) == 4

        r = client.get(f"/decisions?run_id={run_id}", headers=headers)
        assert r.status_code == 200 and len(r.json()["decisions"]) >= 1

        r = client.get(f"/pnl?run_id={run_id}", headers=headers)
        assert r.status_code == 200 and r.json()["latest"] is not None

        r = client.get(f"/audit?run_id={run_id}", headers=headers)
        assert r.status_code == 200 and len(r.json()["events"]) >= 1

        r = client.get(f"/market/summary?run_id={run_id}&timeframe=5m", headers=headers)
        assert r.status_code == 200 and r.json().get("snapshot") is not None

        r = client.get(f"/market/candles?run_id={run_id}&symbol=BTCUSDT&timeframe=5m&limit=10", headers=headers)
        assert r.status_code == 200 and len(r.json().get("candles") or []) >= 2

        r = client.get(f"/models?run_id={run_id}", headers=headers)
        body = r.json()
        assert r.status_code == 200 and len(body.get("models") or []) == 5
        assert body["models"][0].get("llm_model_name"), "expected llm_model_name in /models output"

    print("[PASS] API endpoints respond with seeded Mongo data.")


if __name__ == "__main__":
    main()
