"""Sanity test for Tavily news connector.

Run:
  python tests/test_news_connector.py

Requires:
  TAVILY_API_KEY set in .env
Network access is needed for Tavily API.

Optionally:
  STORE_TO_MONGO=true and MONGODB_URI/MONGODB_URL to persist events.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402


async def main() -> None:
    if not os.getenv("TAVILY_API_KEY"):
        raise RuntimeError("TAVILY_API_KEY is not set; cannot run news connector test.")

    cfg = load_config()
    store = os.getenv("STORE_TO_MONGO", "false").lower() in {"1", "true", "yes", "y", "on"}

    mongo = None
    if store:
        mongo = MongoManager(db_name=os.getenv("MONGODB_TEST_DB", "investment_test"))
        await mongo.connect()
        await mongo.ensure_indexes()

    connector = TavilyNewsConnector.from_app_config(cfg, mongo=mongo, run_id="test_run")
    symbols = cfg.trading.symbols

    print(f"== Fetch news for {symbols} ==")
    docs = await connector.fetch_recent_news(symbols, lookback_hours=24, max_results=5)
    assert isinstance(docs, list)
    print(f"[OK] fetched {len(docs)} docs")

    for d in docs[:3]:
        print(f"- {d.get('timestamp')} {d.get('title')} ({d.get('url')})")

    if mongo is not None:
        await mongo.close()

    print("\n[PASS] News connector sanity checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

