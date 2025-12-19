from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import sys
import os
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List

# Add project root to sys.path to allow imports from agent/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.manager import run_manager_agent, get_portfolio_state
from database.connection import Database
from database.models import AgentMemory

# Load Env
from dotenv import load_dotenv
load_dotenv()

CYCLE_CADENCE = int(os.getenv("CYCLE_CADENCE", "10")) # Minutes

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
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

ws_manager = ConnectionManager()

# --- LIFECYCLE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Database.connect()
    # Start background loop
    asyncio.create_task(autonomous_loop())
    yield
    # Shutdown
    Database.close()

app = FastAPI(title="Investment Agent V2 API", lifespan=lifespan)

# Configure CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for dev simplicity
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GLOBAL STATE ---
is_agent_active = False

async def run_single_cycle(session_id: str):
    """
    Executes a single cycle of the agent for a given session.
    Returns the cycle object or raises an exception.
    """
    print(f"--- Starting Single Cycle for Session {session_id} ---")
            
    # 2. Get Previous Context
    previous_memory = await Database.get_latest_memory(session_id)
    
    # 3. Create Cycle Record
    cycle_count = await Database.db.cycles.count_documents({"session_id": session_id})
    cycle_number = cycle_count + 1
    
    cycle = await Database.create_cycle(session_id, cycle_number)
    
    # 4. Run Agent
    events = []
    generated_memory = None
    
    # Determine prompt based on context
    prompt = "Analyze the market using your Quant Researcher's Python/Pandas capabilities. Formulate and test high-level strategies (e.g., trend following, mean reversion, or correlations) and execute trades if opportunities exist."
    if previous_memory:
        prompt += f" Follow up on: {previous_memory.next_steps}"
    
    # Run Sync Generator
    iterator = run_manager_agent(prompt, previous_memory=previous_memory, verbose=True)
    
    for event in iterator:
        event_dict = event.model_dump()
        events.append(event_dict)
        
        # Broadcast live
        await ws_manager.broadcast(event_dict)
        
        # Persist event immediately for granular logging
        await Database.add_event_to_cycle(cycle.id, event_dict)
        
        if event.type == "memory":
            try:
                generated_memory = AgentMemory.model_validate_json(event.content)
            except:
                pass
        # Small sleep to yield control
        await asyncio.sleep(0.01)
    
    # 5. Capture Portfolio Snapshot
    portfolio_snapshot = {}
    try:
        port_json = get_portfolio_state()
        port_data = json.loads(port_json)
        # Parse "Positions": ["ETH: 0.1", ...] into {"ETH": 0.1}
        positions_map = {}
        for p in port_data.get("Positions", []):
            parts = p.split(":")
            if len(parts) == 2:
                positions_map[parts[0].strip()] = float(parts[1].strip())
                
        portfolio_snapshot = {
            "total_usdt": float(port_data.get("USDT_Free", 0)),
            "positions": positions_map
        }
    except Exception as e:
        print(f"Error capturing portfolio snapshot: {e}")

    # 6. Save Cycle Data
    await Database.update_cycle(
        cycle_id=cycle.id,
        events=events,
        memory=generated_memory,
        portfolio=portfolio_snapshot
    )
    
    print(f"--- Cycle {cycle_number} Complete. ---")
    
    # Notify frontend that the cycle is done so it can reset the "Processing" state
    await ws_manager.broadcast({
        "type": "system", 
        "source": "system", 
        "content": "Single cycle complete.",
        "metadata": {"status": "done"}
    })
    
    return cycle

async def autonomous_loop():
    global is_agent_active
    print("Autonomous Loop Initialized. Agent is currently IDLE.")
    
    while True:
        try:
            if not is_agent_active:
                await asyncio.sleep(5)
                continue

            # 1. Check for Active Session
            session = await Database.get_active_session()
            if not session:
                print("No active session found. Agent going to IDLE.")
                is_agent_active = False
                continue
            
            await run_single_cycle(session.id)
            
            print(f"Sleeping for {CYCLE_CADENCE} mins ---")
            
            # 7. Sleep
            await asyncio.sleep(CYCLE_CADENCE * 60)
            
        except Exception as e:
            print(f"Error in Autonomous Loop: {e}")
            await asyncio.sleep(60) # Sleep on error

@app.get("/")
async def root():
    return {"status": "ok", "message": "Investment Agent V2 API is running"}

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

@app.post("/agent/start")
async def start_agent(duration_minutes: int = 10):
    global is_agent_active
    
    # Ensure active session
    session = await Database.get_active_session()
    if not session:
        await Database.create_session(config={"mode": "autonomous"})
        
    is_agent_active = True
    
    # Start timer
    asyncio.create_task(stop_after_duration(duration_minutes))
    
    return {"status": "agent_started", "duration": duration_minutes}

async def stop_after_duration(minutes: int):
    await asyncio.sleep(minutes * 60)
    global is_agent_active
    if is_agent_active:
        print(f"Timer expired. Stopping agent after {minutes} minutes.")
        is_agent_active = False
        # Broadcast stop event
        await ws_manager.broadcast({"type": "system", "source": "system", "content": "Timer expired. Agent stopped."})

@app.post("/agent/stop")
async def stop_agent():
    global is_agent_active
    is_agent_active = False
    return {"status": "agent_stopped"}

@app.post("/agent/run-once")
async def run_agent_once():
    """
    Manually triggers a single agent cycle without enabling the autonomous loop.
    """
    global is_agent_active
    
    # Ensure active session
    session = await Database.get_active_session()
    if not session:
        session = await Database.create_session(config={"mode": "manual_run"})
    
    # If the autonomous loop is running, we might want to prevent this or let them run in parallel (but parallel might cause race conditions on memory).
    # Ideally, we check:
    if is_agent_active:
         return {"status": "error", "message": "Agent is currently running in autonomous mode. Stop it first."}

    # Run in background to return quickly, or await? 
    # Since the user wants to see the run, we should probably just fire it off in a task
    # BUT, run_single_cycle is async and connected to WS, so if we await it here, the HTTP request hangs until cycle done (could be long)
    # Better to use BackgroundTasks or just asyncio.create_task
    
    task = asyncio.create_task(run_single_cycle(session.id))
    
    return {"status": "starting_single_run", "session_id": session.id}


@app.get("/session/active")
async def get_active_session_details():
    session = await Database.get_active_session()
    if session:
        return session.model_dump()
    return {"status": "no_active_session"}

@app.get("/history")
async def get_history():
    # Return list of sessions
    cursor = Database.db.sessions.find().sort("start_time", -1).limit(10)
    sessions = await cursor.to_list(length=10)
    # Convert ObjectIds to str if needed (FastAPI handles UUIDs well usually)
    for s in sessions:
        if "_id" in s: del s["_id"]
    return sessions

@app.get("/session/{session_id}")
async def get_session_details(session_id: str):
    cycles_cursor = Database.db.cycles.find({"session_id": session_id}).sort("cycle_number", 1)
    cycles = await cycles_cursor.to_list(length=100)
    for c in cycles:
        if "_id" in c: del c["_id"]
    return {"cycles": cycles}

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