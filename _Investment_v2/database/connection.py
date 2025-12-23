import os
from motor.motor_asyncio import AsyncIOMotorClient
from database.models import TradingSession, CycleLog, AgentMemory
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = "investment_agent_v2"

class Database:
    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    def connect(cls):
        if cls.client is None:
            cls.client = AsyncIOMotorClient(MONGO_URI)
            cls.db = cls.client[DB_NAME]
            print(f"Connected to MongoDB at {MONGO_URI}")

    @classmethod
    def close(cls):
        if cls.client:
            cls.client.close()
            cls.client = None

    # --- Session Management ---
    @classmethod
    async def create_session(cls, config: dict, initial_balance: float) -> TradingSession:
        session = TradingSession(config=config, initial_balance=initial_balance, current_balance=initial_balance)
        await cls.db.sessions.insert_one(session.model_dump())
        return session

    @classmethod
    async def get_active_session(cls) -> TradingSession:
        data = await cls.db.sessions.find_one({"status": "active"}, sort=[("start_time", -1)])
        if data:
            return TradingSession(**data)
        return None

    @classmethod
    async def stop_session(cls, session_id: str):
        await cls.db.sessions.update_one({"id": session_id}, {"$set": {"status": "stopped"}})

    # --- Cycle Management ---
    @classmethod
    async def create_cycle(cls, session_id: str, cycle_number: int) -> CycleLog:
        cycle = CycleLog(session_id=session_id, cycle_number=cycle_number)
        await cls.db.cycles.insert_one(cycle.model_dump())
        return cycle

    @classmethod
    async def add_event_to_cycle(cls, cycle_id: str, event: dict):
        """Appends a single event to the cycle's event log immediately."""
        await cls.db.cycles.update_one(
            {"id": cycle_id},
            {"$push": {"events": event}}
        )

    @classmethod
    async def update_cycle(cls, cycle_id: str, events: list, memory: AgentMemory, portfolio: dict):
        await cls.db.cycles.update_one(
            {"id": cycle_id},
            {
                "$set": {
                    "end_time": datetime.utcnow(),
                    "events": events, # We still set this to ensure consistency at the end
                    "memory_generated": memory.model_dump() if memory else None,
                    "portfolio_after": portfolio
                }
            }
        )

    # --- State/Event Audit Trail ---
    @classmethod
    async def add_state_event(cls, event: dict):
        """Persists a single audit event for full state traceability."""
        await cls.db.state_events.insert_one(event)

    @classmethod
    async def get_latest_memory(cls, session_id: str) -> AgentMemory:
        # Find the last completed cycle for this session
        data = await cls.db.cycles.find_one(
            {"session_id": session_id, "memory_generated": {"$ne": None}},
            sort=[("cycle_number", -1)]
        )
        if data and data.get("memory_generated"):
            return AgentMemory(**data["memory_generated"])
        return None
