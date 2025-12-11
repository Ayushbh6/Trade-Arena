"""Indicator and regime computation.

These functions are strategy-neutral: they transform OHLCV into descriptive
statistics for LLM reasoning, without encoding trade rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _to_float_array(values: Sequence[Any]) -> np.ndarray:
    return np.asarray([float(v) for v in values], dtype=np.float64)


def sma(series: Sequence[float], window: int) -> Optional[float]:
    if len(series) < window or window <= 0:
        return None
    arr = _to_float_array(series)
    return float(arr[-window:].mean())


def ema(series: Sequence[float], window: int) -> Optional[float]:
    if len(series) < window or window <= 0:
        return None
    arr = _to_float_array(series)
    alpha = 2.0 / (window + 1.0)
    e = arr[0]
    for x in arr[1:]:
        e = alpha * x + (1.0 - alpha) * e
    return float(e)


def rsi(closes: Sequence[float], window: int = 14) -> Optional[float]:
    if len(closes) <= window:
        return None
    arr = _to_float_array(closes)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[-window:].mean()
    avg_loss = losses[-window:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> Optional[float]:
    if len(highs) <= window or len(lows) <= window or len(closes) <= window:
        return None
    h = _to_float_array(highs)
    l = _to_float_array(lows)
    c = _to_float_array(closes)
    prev_close = np.roll(c, 1)
    prev_close[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    return float(tr[-window:].mean())


def bollinger_bands(closes: Sequence[float], window: int = 20, num_std: float = 2.0) -> Optional[Tuple[float, float, float]]:
    if len(closes) < window:
        return None
    arr = _to_float_array(closes)
    slice_ = arr[-window:]
    mid = slice_.mean()
    sd = slice_.std(ddof=0)
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    return float(lower), float(mid), float(upper)


def realized_volatility(closes: Sequence[float], window: int = 60) -> Optional[float]:
    if len(closes) <= window:
        return None
    arr = _to_float_array(closes)
    rets = np.diff(np.log(arr))
    slice_ = rets[-window:]
    return float(slice_.std(ddof=0) * np.sqrt(window))


def trend_classifier(
    closes: Sequence[float],
    short_window: int = 20,
    long_window: int = 60,
    threshold: float = 0.0025,
) -> str:
    """Classify trend as trend_up/trend_down/range based on MA slope and separation."""
    if len(closes) < long_window:
        return "range"
    s = sma(closes, short_window)
    l = sma(closes, long_window)
    if s is None or l is None:
        return "range"
    diff = (s - l) / l
    if diff > threshold:
        return "trend_up"
    if diff < -threshold:
        return "trend_down"
    return "range"


def vol_regime_classifier(
    closes: Sequence[float],
    short_window: int = 30,
    long_window: int = 180,
    spike_mult: float = 1.5,
) -> str:
    """Classify vol as high_vol/normal using short vs long realized vol."""
    if len(closes) < long_window:
        return "normal"
    short_vol = realized_volatility(closes, short_window) or 0.0
    long_vol = realized_volatility(closes, long_window) or 0.0
    if long_vol == 0.0:
        return "normal"
    if short_vol > spike_mult * long_vol:
        return "high_vol"
    return "normal"


@dataclass(frozen=True)
class IndicatorPack:
    timeframe: str
    last_close: float
    rsi_14: Optional[float]
    atr_14: Optional[float]
    sma_20: Optional[float]
    sma_60: Optional[float]
    ema_20: Optional[float]
    bb_lower: Optional[float]
    bb_mid: Optional[float]
    bb_upper: Optional[float]
    realized_vol: Optional[float]
    trend: str
    vol_regime: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "last_close": self.last_close,
            "rsi_14": self.rsi_14,
            "atr_14": self.atr_14,
            "sma_20": self.sma_20,
            "sma_60": self.sma_60,
            "ema_20": self.ema_20,
            "bb_lower": self.bb_lower,
            "bb_mid": self.bb_mid,
            "bb_upper": self.bb_upper,
            "realized_vol": self.realized_vol,
            "trend": self.trend,
            "vol_regime": self.vol_regime,
        }


def compute_indicator_pack(candles: List[Dict[str, Any]], timeframe: str) -> IndicatorPack:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    bb = bollinger_bands(closes)
    if bb is None:
        bb_lower = bb_mid = bb_upper = None
    else:
        bb_lower, bb_mid, bb_upper = bb

    pack = IndicatorPack(
        timeframe=timeframe,
        last_close=float(closes[-1]),
        rsi_14=rsi(closes, 14),
        atr_14=atr(highs, lows, closes, 14),
        sma_20=sma(closes, 20),
        sma_60=sma(closes, 60),
        ema_20=ema(closes, 20),
        bb_lower=bb_lower,
        bb_mid=bb_mid,
        bb_upper=bb_upper,
        realized_vol=realized_volatility(closes, 60),
        trend=trend_classifier(closes),
        vol_regime=vol_regime_classifier(closes),
    )
    return pack


__all__ = [
    "IndicatorPack",
    "atr",
    "bollinger_bands",
    "compute_indicator_pack",
    "ema",
    "realized_volatility",
    "rsi",
    "sma",
    "trend_classifier",
    "vol_regime_classifier",
]

