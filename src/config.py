"""Central configuration loader.

Phase 0.4: read env vars, expose typed config objects and defaults.
Keep this strategy-neutral: only wiring, limits, and model routing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return int(val)


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return float(val)


def _env_list(name: str, default: List[str], sep: str = ",") -> List[str]:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return [v.strip() for v in val.split(sep) if v.strip()]


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    trader_models: Dict[str, str]
    manager_model: str
    manager_model_fast: str
    manager_model_thinking: str


@dataclass(frozen=True)
class RiskLimits:
    firm_max_total_notional_mult: float
    firm_max_leverage_per_position: float
    firm_daily_stop_pct: float
    agent_budget_notional_usd: float
    agent_max_risk_pct_per_trade: float
    agent_cooldown_cycles_after_stop: int
    vol_spike_size_reduction_mult: float


@dataclass(frozen=True)
class TradingConfig:
    symbols: List[str]
    cadence_minutes: int
    candle_timeframes: List[str]


@dataclass(frozen=True)
class BinanceConfig:
    testnet: bool
    base_url: str
    recv_window: int
    allow_mainnet: bool


@dataclass(frozen=True)
class AppConfig:
    models: ModelConfig
    risk: RiskLimits
    trading: TradingConfig
    binance: BinanceConfig
    mongodb_uri: Optional[str]


def load_config() -> AppConfig:
    """Load configuration from environment."""
    provider = os.getenv("LLM_PROVIDER", "openrouter")

    trader_models: Dict[str, str] = {}
    for i in range(1, 6):
        key = f"LLM_MODEL_TRADER_{i}"
        if os.getenv(key):
            trader_models[f"trader_{i}"] = os.getenv(key, "")

    manager_model = os.getenv("LLM_MODEL_MANAGER", "deepseek/deepseek-chat")
    manager_model_fast = os.getenv("LLM_MODEL_MANAGER_FAST", "") or manager_model
    manager_model_thinking = os.getenv("LLM_MODEL_MANAGER_THINKING", "") or manager_model

    models = ModelConfig(
        provider=provider,
        trader_models=trader_models,
        manager_model=manager_model,
        manager_model_fast=manager_model_fast,
        manager_model_thinking=manager_model_thinking,
    )

    risk = RiskLimits(
        firm_max_total_notional_mult=_env_float("FIRM_MAX_TOTAL_NOTIONAL_MULT", 2.0),
        firm_max_leverage_per_position=_env_float("FIRM_MAX_LEVERAGE_PER_POSITION", 3.0),
        firm_daily_stop_pct=_env_float("FIRM_DAILY_STOP_PCT", 0.05),
        agent_budget_notional_usd=_env_float("AGENT_BUDGET_NOTIONAL_USD", 10000.0),
        agent_max_risk_pct_per_trade=_env_float("AGENT_MAX_RISK_PCT_PER_TRADE", 0.01),
        agent_cooldown_cycles_after_stop=_env_int("AGENT_COOLDOWN_CYCLES_AFTER_STOP", 2),
        vol_spike_size_reduction_mult=_env_float("VOL_SPIKE_SIZE_REDUCTION_MULT", 0.5),
    )

    trading = TradingConfig(
        symbols=_env_list("TRADING_SYMBOLS", ["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        cadence_minutes=_env_int("TRADING_CADENCE_MINUTES", 6),
        candle_timeframes=_env_list("CANDLE_TIMEFRAMES", ["1m", "5m", "15m", "1h"]),
    )

    binance_base = os.getenv("BINANCE_BASE_URL", "https://testnet.binancefuture.com")
    binance = BinanceConfig(
        testnet=_env_bool("BINANCE_TESTNET", True),
        base_url=binance_base,
        recv_window=_env_int("BINANCE_RECV_WINDOW", 5000),
        allow_mainnet=_env_bool("BINANCE_ALLOW_MAINNET", False),
    )

    mongodb_uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")

    return AppConfig(
        models=models,
        risk=risk,
        trading=trading,
        binance=binance,
        mongodb_uri=mongodb_uri,
    )


__all__ = ["AppConfig", "load_config"]
