"""Integration-style sanity test for agent tools and registry.

Run:
  python tests/test_agent_tools.py

Requires:
  - Local MongoDB reachable via MONGODB_URI / MONGODB_URL
  - Binance Futures testnet keys in env/.env
  - TAVILY_API_KEY in env/.env

This test uses authentic flows only (no fakes/mocks).
"""

import asyncio
import os
import sys
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.tools import (  # noqa: E402
    ToolContext,
    build_openrouter_tools,
    build_tool_dispatch,
)
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


async def main() -> None:
    cfg = load_config()
    run_id = "test_tools_run"

    print("== Agent tools integration test ==")

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo connected and indexes ensured.")

    # Ensure we have a fresh market snapshot stored.
    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id=run_id)
    snapshot = await ingestor.fetch_and_store_snapshot()
    assert snapshot["per_symbol"], "empty market snapshot"
    print(f"[OK] Market snapshot stored for symbols: {snapshot.get('symbols')}")

    news_connector = TavilyNewsConnector.from_app_config(cfg, mongo=mongo, run_id=run_id)
    # Store a small recent news batch.
    news_docs = await news_connector.fetch_recent_news(
        cfg.trading.symbols, lookback_hours=24, max_results=3
    )
    print(f"[OK] Recent news fetched/stored: {len(news_docs)} items")

    ctx = ToolContext(
        mongo=mongo,
        config=cfg,
        market_state_builder=MarketStateBuilder(),
        news_connector=news_connector,
        run_id=run_id,
    )

    tool_defs = build_openrouter_tools(ctx)
    assert any(t["function"]["name"] == "get_market_brief" for t in tool_defs)
    dispatch = build_tool_dispatch(ctx)

    symbols = list(cfg.trading.symbols)
    brief = await dispatch["get_market_brief"](symbols=symbols, lookback_minutes=60, allow_live_fetch=False)
    assert "per_symbol" in brief and brief["per_symbol"], "expected per_symbol market brief"
    assert "neutral_summary" in brief
    print("[OK] get_market_brief passed. neutral_summary snippet:")
    print("  ", (brief.get("neutral_summary") or "")[:200])

    candles = await dispatch["get_candles"](
        symbols=symbols[:1], timeframes=["1m"], lookback_bars=20
    )
    sym0 = symbols[0]
    assert len(candles["symbols"][sym0]["1m"]) > 0
    print(f"[OK] get_candles passed for {sym0}. last bar snippet:")
    print("  ", candles["symbols"][sym0]["1m"][-1])

    indicators = await dispatch["get_indicator_pack"](symbols=symbols[:1], timeframes=["1m"])
    assert "rsi_14" in indicators["symbols"][sym0]["1m"]
    print(f"[OK] get_indicator_pack passed for {sym0} 1m. keys snippet:")
    ind_keys = list(indicators["symbols"][sym0]["1m"].keys())
    print("  ", ind_keys[:10])

    news = await dispatch["get_recent_news"](symbols=symbols[:1], lookback_hours=24)
    assert isinstance(news["symbols"][sym0], list)
    print(f"[OK] get_recent_news passed for {sym0}. first item snippet:")
    if news["symbols"][sym0]:
        print("  ", news["symbols"][sym0][0])
    else:
        print("  (no recent news found in lookback window)")

    tav = await dispatch["tavily_search"](query=f"{sym0} crypto", max_results=2, recency_hours=24)
    assert "results" in tav and isinstance(tav["results"], list)
    print("[OK] tavily_search passed. first result snippet:")
    if tav["results"]:
        print("  ", {k: tav["results"][0].get(k) for k in ("title", "url")})

    # These may be empty until later phases populate positions/states.
    pos = await dispatch["get_position_summary"](agent_id="tech_trader")
    assert "positions" in pos and "last_orders" in pos
    print("[OK] get_position_summary passed. positions count:", len(pos.get("positions") or []))

    firm = await dispatch["get_firm_state"]()
    assert "risk_limits" in firm and "agent_budgets" in firm
    print("[OK] get_firm_state passed. risk_limits snippet:")
    print("  ", firm.get("risk_limits"))

    print("[PASS] Agent tools integration sanity checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)
