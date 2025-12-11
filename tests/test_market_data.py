"""Sanity test for market data ingestion.

Run:
  python tests/test_market_data.py

Requires:
  BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY (or BINANCE_API_KEY/SECRET_KEY)
  BINANCE_TESTNET=true
Optionally:
  MONGODB_URI/MONGODB_URL to store snapshots when STORE_TO_MONGO=true
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402


async def main() -> None:
    cfg = load_config()
    store = os.getenv("STORE_TO_MONGO", "false").lower() in {"1", "true", "yes", "y", "on"}

    mongo = None
    if store:
        mongo = MongoManager(db_name=os.getenv("MONGODB_TEST_DB", "investment_test"))
        await mongo.connect()
        await mongo.ensure_indexes()

    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id="test_run")

    print("== Build snapshot ==")
    snapshot = await ingestor.fetch_and_store_snapshot() if store else ingestor.build_snapshot()
    assert snapshot["symbols"], "no symbols in snapshot"
    print(f"[OK] snapshot symbols: {snapshot['symbols']}")

    for sym in snapshot["symbols"]:
        data = snapshot["per_symbol"].get(sym, {})
        assert "candles" in data and data["candles"], f"no candles for {sym}"
        for tf in cfg.trading.candle_timeframes:
            assert tf in data["candles"], f"missing timeframe {tf} for {sym}"
            assert len(data["candles"][tf]) > 0, f"empty candles {tf} for {sym}"
        print(f"[OK] candles fetched for {sym}")

        if data.get("funding_rate") is not None:
            print(f"[OK] funding_rate for {sym}: {data['funding_rate']}")
        if data.get("open_interest") is not None:
            print(f"[OK] open_interest for {sym}: {data['open_interest']}")
        if data.get("top_of_book"):
            tob = data["top_of_book"]
            print(f"[OK] top_of_book for {sym}: bid={tob.get('bid')} ask={tob.get('ask')}")

    if mongo is not None:
        await mongo.close()

    print("\n[PASS] Market data ingestion sanity checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)

