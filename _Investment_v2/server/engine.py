import os
import json
import asyncio
import sys
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.manager import run_manager_agent, get_portfolio_state
from database.connection import Database
from database.models import AgentMemory
from database.redis_client import RedisClient

async def run_single_cycle(session_id: str):
    """
    Executes a single cycle of the agent for a given session.
    Publishes events to Redis for the API to pick up.
    """
    print(f"--- Starting Single Cycle for Session {session_id} ---")
            
    # 1. Get Previous Context
    previous_memory = await Database.get_latest_memory(session_id)
    
    # 2. Create Cycle Record
    cycle_count = await Database.db.cycles.count_documents({"session_id": session_id})
    cycle_number = cycle_count + 1
    
    cycle = await Database.create_cycle(session_id, cycle_number)
    
    # 3. Run Agent
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
        # Inject timestamp if missing
        if not event_dict.get("timestamp"):
            event_dict["timestamp"] = datetime.utcnow().isoformat()
            
        events.append(event_dict)
        
        # --- PHASE 2: BROADCAST TO REDIS ---
        # The API server will hear this and forward to the Frontend
        await RedisClient.publish_event(event_dict)
        
        # Persist event immediately for granular logging
        await Database.add_event_to_cycle(cycle.id, event_dict)
        
        if event.type == "memory":
            try:
                generated_memory = AgentMemory.model_validate_json(event.content)
            except:
                pass
        # Small sleep to yield control
        await asyncio.sleep(0.01)
    
    # 4. Capture Portfolio Snapshot
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

    # 5. Save Cycle Data
    await Database.update_cycle(
        cycle_id=cycle.id,
        events=events,
        memory=generated_memory,
        portfolio=portfolio_snapshot
    )
    
    print(f"--- Cycle {cycle_number} Complete. ---")
    
    # Notify system via Redis
    await RedisClient.publish_event({
        "type": "system", 
        "source": "system", 
        "content": "Single cycle complete.",
        "metadata": {"status": "done"}
    })
    
    return cycle
