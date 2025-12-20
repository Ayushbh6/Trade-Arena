from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import sys
import os
import json
import asyncio
from contextlib import asynccontextmanager
from typing import List

# Add project root to sys.path to allow imports from agent/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.connection import Database
from database.redis_client import RedisClient

# Load Env
from dotenv import load_dotenv
load_dotenv()

# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()

# --- LIFECYCLE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Database.connect()
    
    # Start Redis Listeners (The "Nervous System")
    # 1. Status Listener: Updates buttons (Start/Stop) across tabs
    status_task = asyncio.create_task(listen_for_status_updates())
    # 2. Event Listener: Broadcasts agent thoughts/trades/logs to UI
    event_task = asyncio.create_task(listen_for_agent_events())
    
    yield
    # Shutdown
    status_task.cancel()
    event_task.cancel()
    Database.close()
    await RedisClient.close()

app = FastAPI(title="Investment Agent V2 API", lifespan=lifespan)

# Configure CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for dev simplicity
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- REDIS LISTENERS ---

async def listen_for_status_updates():
    """
    Subscribes to Redis status updates and forwards them to local WebSockets.
    """
    try:
        pubsub = RedisClient.get_client().pubsub()
        await pubsub.subscribe("agent:status_updates")
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                await ws_manager.broadcast(data)
    except Exception as e:
        print(f"Status Listener Error: {e}")
    finally:
        try:
            await pubsub.unsubscribe("agent:status_updates")
        except Exception:
            pass

async def listen_for_agent_events():
    """
    Subscribes to Redis agent events (thoughts, trades) and forwards to WebSockets.
    This allows the Worker (separate process) to talk to the UI.
    """
    try:
        pubsub = RedisClient.get_client().pubsub()
        await pubsub.subscribe("agent:events")
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                await ws_manager.broadcast(data)
    except Exception as e:
        print(f"Event Listener Error: {e}")
    finally:
        try:
            await pubsub.unsubscribe("agent:events")
        except Exception:
            pass

@app.get("/")
async def root():
    return {"status": "ok", "message": "Investment Agent V2 API is running (Decoupled Mode)"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# --- API ENDPOINTS ---

@app.post("/start")
async def start_session(initial_balance: float = 10000.0):
    # Stop any existing active session
    active = await Database.get_active_session()
    if active:
        await Database.stop_session(active.id)
        
    session = await Database.create_session(config={"mode": "autonomous"}, initial_balance=initial_balance)
    return {"status": "started", "session_id": session.id}

@app.post("/stop")
async def stop_session():
    active = await Database.get_active_session()
    if active:
        await Database.stop_session(active.id)
        return {"status": "stopped", "session_id": active.id}
    return {"status": "no_active_session"}

@app.get("/agent/status")
async def get_agent_status():
    state = await RedisClient.get_agent_state()
    return {
        "is_running": state["is_running"],
        "is_autonomous": state["mode"] == "autonomous",
        "is_manual": state["mode"] == "manual",
        "mode": state["mode"]
    }

@app.post("/agent/start")
async def start_agent(cadence_minutes: int = 10, run_limit: int | None = None):
    # Stop any existing active session first to ensure clean state
    active = await Database.get_active_session()
    if active:
        await Database.stop_session(active.id)

    if run_limit is not None and run_limit < 1:
        return {"status": "error", "message": "run_limit must be >= 1"}

    # Create NEW session for this loop
    session = await Database.create_session(config={"mode": "autonomous"})
        
    # Update Redis State -> Worker will pick this up!
    await RedisClient.set_agent_state(True, "autonomous", session.id)
    await RedisClient.set_cadence_minutes(cadence_minutes)
    await RedisClient.reset_run_count()
    if run_limit is None:
        await RedisClient.clear_run_limit()
    else:
        await RedisClient.set_run_limit(run_limit)
    await RedisClient.clear_next_run_time()
    
    return {"status": "agent_started", "cadence_minutes": cadence_minutes, "run_limit": run_limit, "session_id": session.id}

@app.post("/agent/stop")
async def stop_agent():
    state = await RedisClient.get_agent_state()
    if state.get("session_id"):
        await Database.stop_session(state["session_id"])
    await RedisClient.set_agent_state(False, "idle", None)
    await RedisClient.clear_next_run_time()
    await RedisClient.clear_run_limit()
    await RedisClient.reset_run_count()
    await RedisClient.clear_cadence_minutes()
    return {"status": "agent_stopped"}

@app.post("/agent/run-once")
async def run_agent_once():
    """
    Manually triggers a single agent cycle via Redis.
    The Worker will pick this up, run once, and reset state.
    """
    # Check Redis State
    state = await RedisClient.get_agent_state()
    if state["is_running"]:
         return {"status": "error", "message": "Agent is currently running. Stop it first."}
    
    # Create NEW session strictly for this single run
    # We do NOT use get_active_session here because we want isolated runs
    session = await Database.create_session(config={"mode": "manual_run"})
    
    # Set State to Manual -> Worker picks this up!
    await RedisClient.clear_run_limit()
    await RedisClient.reset_run_count()
    await RedisClient.clear_cadence_minutes()
    await RedisClient.clear_next_run_time()
    await RedisClient.set_agent_state(True, "manual", session.id)
    
    return {"status": "starting_single_run", "session_id": session.id}


@app.get("/session/active")
async def get_active_session_details():
    session = await Database.get_active_session()
    if session:
        return session.model_dump()
    return {"status": "no_active_session"}

@app.get("/history")
async def get_history():
    # Return list of sessions with cycle counts
    cursor = Database.db.sessions.find().sort("start_time", -1).limit(20)
    sessions = await cursor.to_list(length=20)
    
    results = []
    for s in sessions:
        if "_id" in s: del s["_id"]
        # Count cycles for this session to enable UI grouping
        count = await Database.db.cycles.count_documents({"session_id": s["id"]})
        s["cycle_count"] = count
        
        # Get the last decision for this session
        last_decision = "No decisions yet"
        # Find the latest cycle that has events
        latest_cycle_with_events = await Database.db.cycles.find_one(
            {"session_id": s["id"], "events": {"$exists": True, "$not": {"$size": 0}}},
            sort=[("cycle_number", -1)]
        )
        
        if latest_cycle_with_events:
            # Find the last event of type 'decision'
            events = latest_cycle_with_events.get("events", [])
            decisions = [e for e in events if e.get("type") == "decision"]
            if decisions:
                last_decision = decisions[-1].get("content", "No decisions yet")
        
        s["last_decision"] = last_decision
        results.append(s)
        
    return results

@app.get("/session/{session_id}")
async def get_session_details(session_id: str):
    # Fetch session details
    session = await Database.db.sessions.find_one({"id": session_id})
    if session and "_id" in session:
        del session["_id"]

    cycles_cursor = Database.db.cycles.find({"session_id": session_id}).sort("cycle_number", 1)
    cycles = await cycles_cursor.to_list(length=100)
    for c in cycles:
        if "_id" in c: del c["_id"]
    
    return {"session": session, "cycles": cycles}

@app.websocket("/ws/test")
async def websocket_test(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("ping")
    await websocket.close()

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection open, maybe handle incoming commands later
            # For now just listen for disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        pass

if __name__ == "__main__":
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
