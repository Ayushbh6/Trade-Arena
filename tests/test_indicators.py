"""Sanity test for indicators and market state packer.

Run:
  python tests/test_indicators.py

Requires Binance testnet keys (same as test_market_data.py).
This test fetches a fresh snapshot and verifies indicator pack + neutral summary.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


async def main() -> None:
    cfg = load_config()
    ingestor = MarketDataIngestor.from_app_config(cfg, run_id="test_run")
    snapshot = ingestor.build_snapshot()

    builder = MarketStateBuilder()
    brief = builder.build_market_brief(snapshot)

    assert brief["per_symbol"], "empty per_symbol in brief"
    assert "neutral_summary" in brief and isinstance(brief["neutral_summary"], str)

    for sym in brief["symbols"]:
        st = brief["per_symbol"][sym]
        assert "timeframes" in st
        tf_state = st["timeframes"].get("1m")
        assert tf_state and "indicators" in tf_state
        ind = tf_state["indicators"]
        assert "rsi_14" in ind and "atr_14" in ind
        print(f"[OK] indicators present for {sym} 1m")

    print("\nNeutral summary:")
    print(brief["neutral_summary"])

    print("\n[PASS] Indicators and market state sanity checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)

