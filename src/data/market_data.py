"""Market data ingestion from Binance Futures testnet.

Strategy-neutral connector that fetches raw market inputs needed for the Market
Brief. It does not compute indicators or embed any trading logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..config import AppConfig
from ..execution.binance_client import BinanceFuturesClient
from .mongo import MongoManager, utc_now
from .schemas import MARKET_SNAPSHOTS


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _parse_klines(raw: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    for row in raw:
        open_time_ms = int(row[0])
        close_time_ms = int(row[6])
        candles.append(
            {
                "open_time_ms": open_time_ms,
                "open_time_iso": _ms_to_iso(open_time_ms),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time_ms": close_time_ms,
                "close_time_iso": _ms_to_iso(close_time_ms),
                "trades": int(row[8]),
                "quote_volume": float(row[7]),
            }
        )
    return candles


@dataclass(frozen=True)
class MarketDataConfig:
    symbols: List[str]
    timeframes: List[str]
    lookback_bars: int = 180
    orderbook_limit: int = 5


class MarketDataIngestor:
    """Fetch and store raw market snapshots."""

    def __init__(
        self,
        *,
        binance: BinanceFuturesClient,
        config: MarketDataConfig,
        mongo: Optional[MongoManager] = None,
        run_id: Optional[str] = None,
    ):
        self.binance = binance
        self.config = config
        self.mongo = mongo
        self.run_id = run_id

    @classmethod
    def from_app_config(
        cls,
        app_config: AppConfig,
        *,
        mongo: Optional[MongoManager] = None,
        run_id: Optional[str] = None,
        binance: Optional[BinanceFuturesClient] = None,
    ) -> "MarketDataIngestor":
        cfg = MarketDataConfig(
            symbols=list(app_config.trading.symbols),
            timeframes=list(app_config.trading.candle_timeframes),
        )
        bn = binance or BinanceFuturesClient(
            testnet=app_config.binance.testnet,
            base_url=app_config.binance.base_url,
            recv_window=app_config.binance.recv_window,
            allow_mainnet=app_config.binance.allow_mainnet,
            audit_mgr=mongo,
            run_id=run_id,
        )
        return cls(binance=bn, config=cfg, mongo=mongo, run_id=run_id)

    def fetch_candles(self, symbol: str, interval: str) -> List[Dict[str, Any]]:
        raw = self.binance.client.futures_klines(
            symbol=symbol, interval=interval, limit=self.config.lookback_bars
        )
        return _parse_klines(raw)

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            rates = self.binance.client.futures_funding_rate(symbol=symbol, limit=1)
            if not rates:
                return None
            return float(rates[-1]["fundingRate"])
        except Exception:
            return None

    def fetch_open_interest(self, symbol: str) -> Optional[float]:
        try:
            res = self.binance.client.futures_open_interest(symbol=symbol)
            return float(res["openInterest"])
        except Exception:
            return None

    def fetch_top_of_book(self, symbol: str) -> Dict[str, Any]:
        try:
            res = self.binance.client.futures_orderbook_ticker(symbol=symbol)
            bid = float(res["bidPrice"])
            ask = float(res["askPrice"])
            return {
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "bid_qty": float(res.get("bidQty", 0.0)),
                "ask_qty": float(res.get("askQty", 0.0)),
            }
        except Exception:
            try:
                book = self.binance.client.futures_order_book(
                    symbol=symbol, limit=self.config.orderbook_limit
                )
                bid = float(book["bids"][0][0])
                ask = float(book["asks"][0][0])
                return {
                    "bid": bid,
                    "ask": ask,
                    "spread": ask - bid,
                    "bid_qty": float(book["bids"][0][1]),
                    "ask_qty": float(book["asks"][0][1]),
                }
            except Exception:
                return {}

    def fetch_symbol_snapshot(self, symbol: str) -> Dict[str, Any]:
        per_tf: Dict[str, Any] = {}
        for tf in self.config.timeframes:
            per_tf[tf] = self.fetch_candles(symbol, tf)

        mark_price = None
        try:
            mark_price = self.binance.get_mark_price(symbol)
        except Exception:
            pass

        return {
            "mark_price": mark_price,
            "candles": per_tf,
            "funding_rate": self.fetch_funding_rate(symbol),
            "open_interest": self.fetch_open_interest(symbol),
            "top_of_book": self.fetch_top_of_book(symbol),
        }

    def build_snapshot(self) -> Dict[str, Any]:
        start = time.perf_counter()
        ts = utc_now()
        per_symbol: Dict[str, Any] = {}
        for sym in self.config.symbols:
            per_symbol[sym] = self.fetch_symbol_snapshot(sym)

        snapshot: Dict[str, Any] = {
            "timestamp": ts,
            "symbols": list(self.config.symbols),
            "per_symbol": per_symbol,
            "latency_s": time.perf_counter() - start,
        }
        if self.run_id:
            snapshot["run_id"] = self.run_id
        return snapshot

    async def fetch_and_store_snapshot(self) -> Dict[str, Any]:
        snapshot = self.build_snapshot()
        if self.mongo is not None:
            inserted_id = await self.mongo.insert_one(MARKET_SNAPSHOTS, snapshot)
            # Motor returns inserted_id separately; attach so downstream audit/tests can reference it.
            snapshot["_id"] = inserted_id
            await self.mongo.log_audit_event(
                "market_snapshot_fetched",
                {"symbols": snapshot["symbols"], "latency_s": snapshot.get("latency_s")},
                run_id=self.run_id,
            )
        return snapshot


__all__ = ["MarketDataIngestor", "MarketDataConfig"]
