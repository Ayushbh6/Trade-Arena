"""Quick script to check existing agent data in MongoDB."""
import asyncio
from src.data.mongo import MongoManager
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

async def main():
    mongo = MongoManager()
    await mongo.connect()
    
    print("\n" + "="*60)
    print("📊 AI TRADING FUND - DATA SUMMARY")
    print("="*60 + "\n")
    
    # Count documents
    collections = {
        "trade_proposals": "💡 Trade Proposals",
        "manager_decisions": "⚖️  Manager Decisions", 
        "llm_calls": "🤖 LLM Calls",
        "pnl_reports": "💰 P&L Reports",
        "positions": "📈 Positions",
        "orders": "📋 Orders",
        "audit_log": "📝 Audit Events"
    }
    
    for col_name, label in collections.items():
        count = await mongo.db[col_name].count_documents({})
        print(f"{label:30s}: {count:6d} records")
    
    print("\n" + "-"*60)
    print("🔍 RECENT AGENT ACTIVITY")
    print("-"*60 + "\n")
    
    # Get recent proposals
    proposals = await mongo.db.trade_proposals.find({}).sort("timestamp", -1).limit(3).to_list(length=3)
    
    if proposals:
        print("Last 3 Trade Proposals:")
        for i, p in enumerate(proposals, 1):
            print(f"\n  {i}. Agent: {p.get('agent_id', 'unknown')}")
            print(f"     Symbol: {p.get('symbol', 'N/A')}")
            print(f"     Action: {p.get('action', 'N/A')}")
            print(f"     Time: {p.get('timestamp', 'N/A')}")
            notes = p.get('notes', '')
            if notes:
                print(f"     Reasoning: {notes[:100]}...")
    else:
        print("  No proposals yet. Run a cycle to see agent work!")
    
    # Get recent decisions
    print("\n" + "-"*60)
    decisions = await mongo.db.manager_decisions.find({}).sort("timestamp", -1).limit(2).to_list(length=2)
    
    if decisions:
        print("Last 2 Manager Decisions:")
        for i, d in enumerate(decisions, 1):
            print(f"\n  {i}. Cycle: {d.get('cycle_id', 'unknown')}")
            print(f"     Time: {d.get('timestamp', 'N/A')}")
            items = d.get('items', [])
            print(f"     Reviewed {len(items)} proposal(s)")
            notes = d.get('notes', '')
            if notes:
                print(f"     Manager Notes: {notes[:100]}...")
    else:
        print("  No manager decisions yet.")
    
    print("\n" + "="*60)
    print("💡 TIP: Open http://localhost:5173 to see this in the dashboard!")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
