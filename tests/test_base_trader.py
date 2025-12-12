"""Authentic integration test for BaseTrader REACT loop.

Run:
  python tests/test_base_trader.py

Requires:
  - Local MongoDB (MONGODB_URI / MONGODB_URL)
  - Binance Futures testnet keys
  - TAVILY_API_KEY
  - OPENROUTER_API_KEY

This test forces at least 2 sequential tool calls using existing tools.
"""

import asyncio
import os
import sys
import time
import json

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.base import BaseTrader, BaseTraderConfig  # noqa: E402
from src.agents.tools import ToolContext  # noqa: E402
from src.config import load_config  # noqa: E402
from src.data.market_data import MarketDataIngestor  # noqa: E402
from src.data.mongo import MongoManager  # noqa: E402
from src.data.news_connector import TavilyNewsConnector  # noqa: E402
from src.features.market_state import MarketStateBuilder  # noqa: E402


ROLE_PROMPT = """
You are the Technical/Quant trader.
Trade repeatable technical setups on 5m-1h horizons.
Stay strategy-neutral in description; propose trades only if edge is clear.
"""

MODEL_ENV_KEYS = ["LLM_MODEL_TRADER_1", "LLM_MODEL_TRADER_2", "LLM_MODEL_TRADER_3"]


def _models_from_env() -> list[str]:
    models: list[str] = []
    for k in MODEL_ENV_KEYS:
        v = os.getenv(k)
        if v and v.strip():
            models.append(v.strip())
    uniq: list[str] = []
    for m in models:
        if m not in uniq:
            uniq.append(m)
    return uniq


def _print_table(rows: list[dict]) -> None:
    cols = [
        "rank",
        "model",
        "score",
        "schema_ok",
        "tool_calls",
        ">=2_tools",
        "trades",
        "all_trades_have_sl",
        "min_rr",
        "time_s",
        "error",
    ]
    widths = {c: len(c) for c in cols}
    for r in rows:
        for c in cols:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))

    def fmt_row(r: dict) -> str:
        return " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols)

    print("\n== Model comparison ==")
    print(fmt_row({c: c for c in cols}))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(fmt_row(r))


async def main() -> None:
    print("== BaseTrader REACT integration test ==")

    cfg = load_config()
    models = _models_from_env()
    if not models:
        raise RuntimeError(
            "No trader models found in env. Set at least one of "
            + ", ".join(MODEL_ENV_KEYS)
        )

    base_run_id = "test_base_trader_run"

    mongo = MongoManager(db_name="investment_test")
    await mongo.connect()
    await mongo.ensure_indexes()
    print("[OK] Mongo ready.")

    ingestor = MarketDataIngestor.from_app_config(cfg, mongo=mongo, run_id=base_run_id)
    snapshot = await ingestor.fetch_and_store_snapshot()
    builder = MarketStateBuilder()
    _full_market_brief = builder.build_market_brief(snapshot)
    print("[OK] Market snapshot stored for tools.")

    news_connector = TavilyNewsConnector.from_app_config(cfg, mongo=mongo, run_id=base_run_id)
    await news_connector.fetch_recent_news(cfg.trading.symbols, lookback_hours=24, max_results=3)
    print("[OK] Recent news stored.")

    # Do not spoon-feed specific tools. The model should decide autonomously.
    # We only require that it grounds its decision with multiple tool calls.
    extra = (
        "You are evaluating a potential trade on BTCUSDT right now.\n"
        "The Market Brief you received is intentionally PARTIAL and may be stale.\n"
        "Goal: produce a high-quality, fully grounded TradeProposal.\n"
        "Constraints:\n"
        "- Do NOT fabricate any facts; use tools to fetch any missing/uncertain info.\n"
        "- You MUST make at least two distinct tool calls before your final JSON, "
        "choosing whichever tools you deem necessary.\n"
        "- Then return ONLY the TradeProposal JSON.\n"
    )

    rows: list[dict] = []
    passes = 0

    def extract_mark_price(messages: list[dict]) -> float | None:
        for m in messages:
            if m.get("role") == "tool" and m.get("name") == "get_market_brief":
                try:
                    obj = json.loads(m.get("content") or "{}")
                    ps = (obj.get("per_symbol") or {}).get("BTCUSDT") or {}
                    mp = ps.get("mark_price")
                    if isinstance(mp, (int, float)):
                        return float(mp)
                except Exception:
                    continue
        return None

    def compute_rr(side: str, entry: float, stop: float, take_profit: float) -> float | None:
        if entry <= 0 or stop <= 0 or take_profit <= 0:
            return None
        if side == "long":
            risk = entry - stop
            reward = take_profit - entry
        else:
            risk = stop - entry
            reward = entry - take_profit
        if risk <= 0 or reward <= 0:
            return None
        return reward / risk

    def score_decision(
        *,
        schema_ok: bool,
        tool_calls: int,
        trades: list,
        mark_price: float | None,
    ) -> tuple[float, dict]:
        details: dict = {}
        if not schema_ok:
            return 0.0, {"reason": "schema_invalid"}

        score = 50.0
        score += min(tool_calls, 6) * 5.0  # reward grounding

        actionable_trades = [t for t in trades if t.get("action") in ("open", "add")]
        all_have_sl = all(t.get("stop_loss") for t in actionable_trades) if actionable_trades else True
        details["all_trades_have_sl"] = all_have_sl
        if actionable_trades and not all_have_sl:
            score -= 40.0  # heavy penalty: profit-seeking without stops is disallowed

        rrs: list[float] = []
        for t in actionable_trades:
            side = t.get("side")
            entry = t.get("limit_price") if t.get("order_type") == "limit" else mark_price
            stop = t.get("stop_loss")
            tp = t.get("take_profit")
            if isinstance(entry, (int, float)) and isinstance(stop, (int, float)) and isinstance(tp, (int, float)):
                rr = compute_rr(str(side), float(entry), float(stop), float(tp))
                if rr is not None:
                    rrs.append(rr)
        min_rr = min(rrs) if rrs else None
        details["min_rr"] = min_rr
        if min_rr is not None:
            if min_rr < 1.0:
                score -= 20.0
            elif min_rr >= 1.5:
                score += 10.0
            elif min_rr >= 2.0:
                score += 15.0

        # If no trades, keep it viable but not top unless tools strongly justify.
        if not actionable_trades:
            score -= 5.0

        return max(0.0, score), details

    for model in models:
        run_id = f"{base_run_id}:{model}"
        tools_ctx = ToolContext(
            mongo=mongo,
            config=cfg,
            market_state_builder=builder,
            news_connector=news_connector,
            run_id=run_id,
        )

        trader = BaseTrader(
            agent_id="tech_trader",
            role_prompt=ROLE_PROMPT,
            config=BaseTraderConfig(model=model, temperature=0.0, max_tool_turns=6),
            tools_context=tools_ctx,
        )

        print(f"\n[INFO] Calling trader.decide() with model={model} ...")
        start = time.perf_counter()
        error = ""
        schema_ok = False
        tool_calls = 0
        trades_dump: list[dict] = []
        try:
            proposal = await trader.decide(
                market_brief={
                    "timestamp": _full_market_brief.get("timestamp"),
                    "symbols": ["BTCUSDT"],
                    "note": "partial brief; call tools for details",
                },
                firm_state=None,
                position_summary=None,
                extra_instructions=extra,
            )
            schema_ok = True
            trades_dump = [t.model_dump(mode="json") for t in proposal.trades]
            print("[OK] Trader returned TradeProposal. trades:", len(trades_dump))

            tcalls = trader.last_tool_calls
            tool_calls = len(tcalls)
            print(f"[OK] Tool calls made: {tool_calls}")
            for i, tc in enumerate(tcalls, 1):
                print(f"  {i}) {tc['name']} args={tc['args']}")

            if trades_dump:
                print("[INFO] Trade snippets:")
                for i, t in enumerate(trades_dump[:3], 1):
                    snippet = {
                        "symbol": t.get("symbol"),
                        "side": t.get("side"),
                        "action": t.get("action"),
                        "size_usdt": t.get("size_usdt"),
                        "leverage": t.get("leverage"),
                        "order_type": t.get("order_type"),
                        "limit_price": t.get("limit_price"),
                        "stop_loss": t.get("stop_loss"),
                        "take_profit": t.get("take_profit"),
                        "confidence": t.get("confidence"),
                        "time_horizon": t.get("time_horizon"),
                    }
                    print(f"  {i}) {snippet}")
        except Exception as e:
            error = str(e)
            # Print last assistant message snippet if available for debugging.
            last_assistant = None
            for m in reversed(trader.last_messages or []):
                if m.get("role") == "assistant" and m.get("content"):
                    last_assistant = m.get("content")
                    break
            if last_assistant:
                print("[DEBUG] Last assistant content snippet:")
                print("  ", str(last_assistant)[:500])

        elapsed = time.perf_counter() - start
        meets_tools = tool_calls >= 2
        if schema_ok and meets_tools:
            passes += 1

        mark_price = extract_mark_price(trader.last_messages)
        score, details = score_decision(
            schema_ok=schema_ok,
            tool_calls=tool_calls,
            trades=trades_dump,
            mark_price=mark_price,
        )

        all_trades_have_sl = details.get("all_trades_have_sl")
        min_rr = details.get("min_rr")

        rows.append(
            {
                "model": model,
                "score": f"{score:.1f}",
                "schema_ok": "yes" if schema_ok else "no",
                "tool_calls": str(tool_calls),
                ">=2_tools": "yes" if meets_tools else "no",
                "trades": str(len(trades_dump)),
                "all_trades_have_sl": "yes" if all_trades_have_sl else "no",
                "min_rr": f"{min_rr:.2f}" if isinstance(min_rr, (int, float)) else "",
                "time_s": f"{elapsed:.2f}",
                "error": (error[:60] + "…") if len(error) > 60 else error,
            }
        )

    # Rank by score desc, then tool_calls desc, then time asc.
    rows.sort(
        key=lambda r: (
            float(r.get("score") or 0.0),
            int(r.get("tool_calls") or 0),
            float(r.get("time_s") or 0.0),
        ),
        reverse=True,
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = str(i)

    _print_table(rows)

    assert passes >= 1, "no configured model met schema + >=2 tool-call requirements"
    print("\n[PASS] At least one model met schema + tool-call requirements.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback

        print("\n[FAIL] BaseTrader test raised an exception:")
        print(str(e))
        traceback.print_exc()
        sys.exit(1)
