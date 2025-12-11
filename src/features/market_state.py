"""Market state packer.

Builds a compact, strategy-neutral Market Brief from a raw market snapshot
produced by Phase 1.1. This module computes descriptive statistics (indicators,
correlations, breadth) and a deterministic neutral summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .indicators import compute_indicator_pack


def _returns_from_closes(closes: List[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    prev = closes[-2]
    if prev == 0:
        return None
    return (closes[-1] / prev) - 1.0


def _pct_change(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return (a / b) - 1.0


@dataclass(frozen=True)
class MarketStateConfig:
    summary_timeframe: str = "1h"
    return_window_bars: int = 60
    top_movers_n: int = 3


class MarketStateBuilder:
    def __init__(self, config: Optional[MarketStateConfig] = None):
        self.config = config or MarketStateConfig()

    def per_symbol_state(self, symbol: str, symbol_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        candles_by_tf: Dict[str, List[Dict[str, Any]]] = symbol_snapshot.get("candles", {})
        per_tf: Dict[str, Any] = {}
        for tf, candles in candles_by_tf.items():
            if not candles:
                continue
            pack = compute_indicator_pack(candles, tf).to_dict()
            # small OHLCV summary
            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            vols = [c["volume"] for c in candles]
            per_tf[tf] = {
                "last_close": closes[-1],
                "high": max(highs[-10:]) if len(highs) >= 10 else max(highs),
                "low": min(lows[-10:]) if len(lows) >= 10 else min(lows),
                "volume_last_bar": vols[-1],
                "return_last_bar": _returns_from_closes(closes),
                "indicators": pack,
            }

        return {
            "mark_price": symbol_snapshot.get("mark_price"),
            "funding_rate": symbol_snapshot.get("funding_rate"),
            "open_interest": symbol_snapshot.get("open_interest"),
            "top_of_book": symbol_snapshot.get("top_of_book", {}),
            "timeframes": per_tf,
        }

    def compute_breadth(self, per_symbol: Dict[str, Any]) -> Dict[str, Any]:
        tf = self.config.summary_timeframe
        rets: Dict[str, float] = {}
        for sym, data in per_symbol.items():
            candles = data.get("candles", {}).get(tf) or []
            closes = [c["close"] for c in candles]
            if len(closes) < 2:
                continue
            r = _pct_change(closes[-1], closes[-2])
            if r is not None:
                rets[sym] = r

        pos = sum(1 for r in rets.values() if r > 0)
        neg = sum(1 for r in rets.values() if r < 0)
        return {
            "timeframe": tf,
            "n_symbols": len(rets),
            "positive": pos,
            "negative": neg,
            "flat": len(rets) - pos - neg,
        }

    def compute_correlations(self, per_symbol: Dict[str, Any]) -> Dict[str, Any]:
        tf = self.config.summary_timeframe
        series: List[List[float]] = []
        symbols: List[str] = []
        for sym, data in per_symbol.items():
            candles = data.get("candles", {}).get(tf) or []
            closes = [c["close"] for c in candles][-self.config.return_window_bars :]
            if len(closes) < 3:
                continue
            symbols.append(sym)
            series.append(closes)

        if len(series) < 2:
            return {"timeframe": tf, "matrix": {}, "symbols": symbols}

        # log returns for corr stability
        rets = np.diff(np.log(np.asarray(series, dtype=np.float64)), axis=1)
        corr = np.corrcoef(rets)
        matrix: Dict[str, Dict[str, float]] = {}
        for i, a in enumerate(symbols):
            matrix[a] = {}
            for j, b in enumerate(symbols):
                matrix[a][b] = float(corr[i, j])
        return {"timeframe": tf, "symbols": symbols, "matrix": matrix}

    def top_movers(self, per_symbol: Dict[str, Any]) -> List[Tuple[str, float]]:
        tf = self.config.summary_timeframe
        movers: List[Tuple[str, float]] = []
        for sym, data in per_symbol.items():
            candles = data.get("candles", {}).get(tf) or []
            closes = [c["close"] for c in candles]
            if len(closes) < 2:
                continue
            r = _pct_change(closes[-1], closes[-2])
            if r is not None:
                movers.append((sym, r))
        movers.sort(key=lambda x: abs(x[1]), reverse=True)
        return movers[: self.config.top_movers_n]

    def neutral_summary(self, per_symbol_state: Dict[str, Any], breadth: Dict[str, Any], movers: List[Tuple[str, float]]) -> str:
        tf = self.config.summary_timeframe
        n = breadth.get("n_symbols", 0)
        pos = breadth.get("positive", 0)
        neg = breadth.get("negative", 0)

        lines: List[str] = []
        if n:
            lines.append(
                f"Over the last {tf}, breadth is {pos} up / {neg} down across {n} symbols."
            )

        if movers:
            mover_str = ", ".join([f"{s} {r*100:.2f}%" for s, r in movers])
            lines.append(f"Top movers ({tf} bar-to-bar): {mover_str}.")

        # Per-symbol regime notes (trend/vol from indicator packs)
        regimes: List[str] = []
        for sym, st in per_symbol_state.items():
            tf_state = st.get("timeframes", {}).get(tf) or {}
            ind = tf_state.get("indicators") or {}
            trend = ind.get("trend")
            vol = ind.get("vol_regime")
            if trend or vol:
                regimes.append(f"{sym}: {trend or 'range'}, {vol or 'normal'} vol")
        if regimes:
            lines.append("Regimes: " + "; ".join(regimes) + ".")

        return " ".join(lines) if lines else "No significant market changes detected."

    def build_market_brief(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        per_symbol_raw: Dict[str, Any] = snapshot.get("per_symbol", {})
        per_symbol_state: Dict[str, Any] = {}
        for sym, sym_snap in per_symbol_raw.items():
            per_symbol_state[sym] = self.per_symbol_state(sym, sym_snap)

        breadth = self.compute_breadth(per_symbol_raw)
        correlations = self.compute_correlations(per_symbol_raw)
        movers = self.top_movers(per_symbol_raw)
        neutral = self.neutral_summary(per_symbol_state, breadth, movers)

        brief: Dict[str, Any] = {
            "timestamp": snapshot.get("timestamp"),
            "symbols": snapshot.get("symbols", []),
            "per_symbol": per_symbol_state,
            "market_metrics": {
                "breadth": breadth,
                "correlations": correlations,
            },
            "events": {
                "news": [],
            },
            "neutral_summary": neutral,
        }
        if snapshot.get("run_id"):
            brief["run_id"] = snapshot["run_id"]
        return brief


__all__ = ["MarketStateBuilder", "MarketStateConfig"]

