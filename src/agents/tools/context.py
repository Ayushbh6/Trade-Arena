"""Shared tool context.

Tools are read-only in Phase 2. They accept only JSON args from the LLM,
while dependencies (Mongo, config, builders, connectors) are provided via context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...config import AppConfig
from ...data.mongo import MongoManager
from ...data.news_connector import TavilyNewsConnector
from ...features.market_state import MarketStateBuilder


@dataclass
class ToolContext:
    mongo: Optional[MongoManager] = None
    config: Optional[AppConfig] = None
    market_state_builder: Optional[MarketStateBuilder] = None
    news_connector: Optional[TavilyNewsConnector] = None
    run_id: Optional[str] = None

