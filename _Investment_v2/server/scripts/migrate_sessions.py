import asyncio
import os
import sys
from datetime import datetime
import uuid

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.connection import Database

async def migrate():
    print("--- Starting Migration ---")
    Database.connect()
    
    # 1. toggle the specific session (or find the one with many cycles)
    # The user mentioned one session has 12 cycles.
    # Let's find sessions with > 2 cycles
    
    cursor = Database.db.sessions.find()
    sessions = await cursor.to_list(length=100)
    
    target_session = None
    
    for s in sessions:
        cycle_count = await Database.db.cycles.count_documents({"session_id": s["id"]})
        print(f"Session {s['id']} has {cycle_count} cycles.")
        if cycle_count >= 10: # Heuristic for the "bundled" session
            target_session = s
            break
            
    if not target_session:
        print("No target session with >= 10 cycles found.")
        return

    print(f"Targeting Session: {target_session['id']}")
    
    # 2. Get all cycles
    cycles_cursor = Database.db.cycles.find({"session_id": target_session["id"]}).sort("cycle_number", 1)
    cycles = await cycles_cursor.to_list(length=100)
    
    print(f"Found {len(cycles)} cycles.")
    
    # 3. Keep last 2, delete others
    cycles_to_keep = cycles[-2:]
    cycles_to_delete = cycles[:-2]
    
    print(f"Deleting {len(cycles_to_delete)} old cycles...")
    for c in cycles_to_delete:
         await Database.db.cycles.delete_one({"id": c["id"]})
         
    # 4. Convert keeps into new sessions
    print("Converting last 2 cycles into new sessions...")
    
    for c in cycles_to_keep:
        new_session_id = str(uuid.uuid4())
        
        # Use cycle's first event timestamp as start time, or now if missing
        start_time = datetime.now()
        if c.get("events") and len(c["events"]) > 0:
            first_ts = c["events"][0].get("timestamp")
            if first_ts:
                try:
                    start_time = datetime.fromisoformat(first_ts.replace('Z', '+00:00'))
                except:
                    pass
        
        # Create new session
        new_session = {
            "id": new_session_id,
            "start_time": start_time,
            "status": "completed", # Since it's a past run
            "config": {"mode": "manual_run"},
            "initial_balance": target_session.get("initial_balance", 10000.0),
            "current_balance": c.get("portfolio", {}).get("total_usdt", 10000.0)
        }
        
        await Database.db.sessions.insert_one(new_session)
        print(f"Created new session {new_session_id}")
        
        # Move cycle to this session
        await Database.db.cycles.update_one(
            {"id": c["id"]},
            {"$set": {
                "session_id": new_session_id,
                "cycle_number": 1 # Reset to 1 as it's a single run
            }}
        )
        print(f"Moved cycle {c['id']} to {new_session_id}")

    # 5. Delete original container session
    print(f"Deleting original session {target_session['id']}")
    await Database.db.sessions.delete_one({"id": target_session["id"]})
    
    print("--- Migration Complete ---")

if __name__ == "__main__":
    asyncio.run(migrate())
