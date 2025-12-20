import asyncio
import os
import sys
import time
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.connection import Database
from database.redis_client import RedisClient
from server.engine import run_single_cycle

load_dotenv()

CYCLE_CADENCE = int(os.getenv("CYCLE_CADENCE", "10")) # Minutes

async def autonomous_loop():
    print("Worker Process Initialized. Polling Redis for State...")
    
    # Connect to DBs
    Database.connect()
    
    try:
        while True:
            try:
                # 1. Check Global State from Redis
                state = await RedisClient.get_agent_state()
                
                if not state["is_running"]:
                    await asyncio.sleep(2)
                    continue

                # 3. Handle 'Manual' Run-Once Mode
                if state["mode"] == "manual":
                    session = await Database.get_active_session()
                    if not session:
                        print("State is running but no active session in DB. Waiting...")
                        await asyncio.sleep(5)
                        continue

                    # Acquire lock to ensure we don't run it twice if multiple workers exist
                    lock_token = await RedisClient.acquire_lock("manual_run_exec", expire=600) # 10 min lock for manual run
                    if lock_token:
                        try:
                            print("EXECUTING MANUAL RUN...")
                            await run_single_cycle(session.id)
                            print("Manual run finished. Reverting state to Idle.")
                            
                            # Mark session as stopped in DB
                            await Database.stop_session(session.id)
                            
                            # Reset Redis State
                            await RedisClient.set_agent_state(False, "idle", None)
                            await RedisClient.clear_next_run_time()
                            await RedisClient.clear_run_limit()
                            await RedisClient.reset_run_count()
                            await RedisClient.clear_cadence_minutes()
                        finally:
                            await RedisClient.release_lock("manual_run_exec", lock_token)
                    else:
                        # Lock held by another worker
                        await asyncio.sleep(1)
                    continue

                # 4. Handle 'Autonomous' Mode
                if state["mode"] == "autonomous":
                    session = await Database.get_active_session()
                    if not session:
                        print("State is running but no active session in DB. Waiting...")
                        await asyncio.sleep(5)
                        continue

                    now = time.time()
                    cadence_minutes = await RedisClient.get_cadence_minutes()
                    cadence = cadence_minutes if cadence_minutes and cadence_minutes > 0 else CYCLE_CADENCE

                    next_run_time = await RedisClient.get_next_run_time()
                    if next_run_time and now < next_run_time:
                        await asyncio.sleep(min(5, next_run_time - now))
                        continue

                    lock_token = await RedisClient.acquire_lock("autonomous_cycle_exec", expire=90)
                    if lock_token:
                        try:
                            print("EXECUTING AUTONOMOUS CYCLE...")
                            await run_single_cycle(session.id)
                            await RedisClient.set_next_run_time(time.time() + (cadence * 60))
                            run_limit = await RedisClient.get_run_limit()
                            if run_limit:
                                run_count = await RedisClient.incr_run_count()
                                if run_count >= run_limit:
                                    stop_token = await RedisClient.acquire_lock("agent_stop_exec", expire=30)
                                    if stop_token:
                                        try:
                                            await Database.stop_session(session.id)
                                            await RedisClient.set_agent_state(False, "idle", None)
                                            await RedisClient.clear_next_run_time()
                                            await RedisClient.clear_run_limit()
                                            await RedisClient.reset_run_count()
                                            await RedisClient.clear_cadence_minutes()
                                            await RedisClient.publish_event({
                                                "type": "system",
                                                "source": "system",
                                                "content": f"Run limit reached ({run_limit}). Agent stopped."
                                            })
                                        finally:
                                            await RedisClient.release_lock("agent_stop_exec", stop_token)
                        finally:
                            await RedisClient.release_lock("autonomous_cycle_exec", lock_token)
                        await asyncio.sleep(1)
                    else:
                        # Lock held? Wait a bit.
                        await asyncio.sleep(5)
            
            except Exception as e:
                print(f"Error in Worker Loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(60) # Sleep on error

    finally:
        # Cleanup
        Database.close()
        await RedisClient.close()

if __name__ == "__main__":
    try:
        asyncio.run(autonomous_loop())
    except KeyboardInterrupt:
        print("Worker stopped by user.")
