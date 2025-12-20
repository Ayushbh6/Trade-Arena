import os
import redis.asyncio as redis
import json
from dotenv import load_dotenv
from uuid import uuid4
from typing import Optional

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

class RedisClient:
    _pool = None

    @classmethod
    def get_client(cls):
        if cls._pool is None:
            cls._pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
        return redis.Redis(connection_pool=cls._pool)

    @classmethod
    async def close(cls):
        if cls._pool:
            await cls._pool.disconnect()

    # --- STATE MANAGEMENT ---
    
    @classmethod
    async def get_agent_state(cls):
        """
        Returns the current state of the agent from Redis.
        Default: { is_running: False, mode: 'idle', session_id: None }
        """
        client = cls.get_client()
        state = await client.hgetall("agent:state")
        
        if not state:
            return {
                "is_running": False,
                "mode": "idle",
                "session_id": None
            }
        
        # Convert strings back to proper types
        return {
            "is_running": state.get("is_running") == "true",
            "mode": state.get("mode", "idle"),
            "session_id": state.get("session_id")
        }

    @classmethod
    async def set_agent_state(cls, is_running: bool, mode: str, session_id: str = None):
        """
        Updates the agent state.
        """
        client = cls.get_client()
        mapping = {
            "is_running": "true" if is_running else "false",
            "mode": mode,
        }
        await client.hset("agent:state", mapping=mapping)
        if session_id is None:
            await client.hdel("agent:state", "session_id")
        else:
            await client.hset("agent:state", mapping={"session_id": session_id})
        
        # Also publish the status update immediately (Phase 2 Prep)
        await client.publish("agent:status_updates", json.dumps({
            "type": "status_update",
            "content": {
                "is_running": is_running,
                "mode": mode,
                "session_id": session_id
            }
        }))

    # --- DISTRIBUTED LOCKING ---

    @classmethod
    async def acquire_lock(cls, lock_name: str, expire: int = 60) -> Optional[str]:
        """
        Tries to acquire a lock. Returns a lock token if acquired, None otherwise.
        """
        client = cls.get_client()
        token = uuid4().hex
        # SET NX (Not Exists) is the standard way to do locks in Redis
        is_acquired = await client.set(f"lock:{lock_name}", token, ex=expire, nx=True)
        return token if is_acquired else None

    @classmethod
    async def release_lock(cls, lock_name: str, token: str) -> bool:
        client = cls.get_client()
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await client.eval(script, 1, f"lock:{lock_name}", token)
        return bool(result)

    @classmethod
    async def refresh_lock(cls, lock_name: str, token: str, expire: int = 60) -> bool:
        """
        Extends the lock duration (heartbeat).
        """
        client = cls.get_client()
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = await client.eval(script, 1, f"lock:{lock_name}", token, expire)
        return bool(result)

    # --- PUB/SUB (Phase 2 Prep) ---
    
    @classmethod
    async def publish_event(cls, event: dict):
        client = cls.get_client()
        await client.publish("agent:events", json.dumps(event))

    # --- SCHEDULING ---

    @classmethod
    async def get_next_run_time(cls) -> Optional[float]:
        client = cls.get_client()
        value = await client.get("agent:next_run_time")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @classmethod
    async def set_next_run_time(cls, timestamp: float):
        client = cls.get_client()
        await client.set("agent:next_run_time", str(timestamp))

    @classmethod
    async def clear_next_run_time(cls):
        client = cls.get_client()
        await client.delete("agent:next_run_time")

    @classmethod
    async def get_cadence_minutes(cls) -> Optional[int]:
        client = cls.get_client()
        value = await client.get("agent:cadence_minutes")
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @classmethod
    async def set_cadence_minutes(cls, minutes: int):
        client = cls.get_client()
        await client.set("agent:cadence_minutes", str(minutes))

    @classmethod
    async def clear_cadence_minutes(cls):
        client = cls.get_client()
        await client.delete("agent:cadence_minutes")

    @classmethod
    async def get_run_limit(cls) -> Optional[int]:
        client = cls.get_client()
        value = await client.get("agent:run_limit")
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @classmethod
    async def set_run_limit(cls, limit: int):
        client = cls.get_client()
        await client.set("agent:run_limit", str(limit))

    @classmethod
    async def clear_run_limit(cls):
        client = cls.get_client()
        await client.delete("agent:run_limit")

    @classmethod
    async def get_run_count(cls) -> int:
        client = cls.get_client()
        value = await client.get("agent:run_count")
        if value is None:
            return 0
        try:
            return int(value)
        except ValueError:
            return 0

    @classmethod
    async def reset_run_count(cls):
        client = cls.get_client()
        await client.set("agent:run_count", "0")

    @classmethod
    async def incr_run_count(cls) -> int:
        client = cls.get_client()
        return int(await client.incr("agent:run_count"))
