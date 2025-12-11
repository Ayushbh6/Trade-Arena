"""News ingestion via Tavily search.

Strategy-neutral connector that pulls recent crypto news and stores normalized
events in MongoDB. It never fabricates data; missing keys or request failures
result in empty outputs and audit warnings.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import requests

from ..config import AppConfig
from .mongo import MongoManager, utc_now
from .schemas import NEWS_EVENTS


class TavilyError(RuntimeError):
    pass


def _env_str(name: str) -> Optional[str]:
    val = os.getenv(name)
    if not val or val.strip() == "":
        return None
    return val.strip()


@dataclass(frozen=True)
class NewsConnectorConfig:
    api_key: str
    max_results: int = 8
    recency_hours: int = 24
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None


class TavilyNewsConnector:
    """Thin Tavily Search wrapper with optional Mongo persistence."""

    def __init__(
        self,
        *,
        config: NewsConnectorConfig,
        mongo: Optional[MongoManager] = None,
        run_id: Optional[str] = None,
    ):
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
    ) -> "TavilyNewsConnector":
        key = _env_str("TAVILY_API_KEY")
        if not key:
            raise TavilyError("TAVILY_API_KEY is not set in env.")
        cfg = NewsConnectorConfig(api_key=key)
        return cls(config=cfg, mongo=mongo, run_id=run_id)

    def search(self, query: str, *, max_results: Optional[int] = None, recency_hours: Optional[int] = None) -> List[Dict[str, Any]]:
        """Run Tavily search and return raw results list."""
        max_r = max_results or self.config.max_results
        recency = recency_hours or self.config.recency_hours
        payload: Dict[str, Any] = {
            "api_key": self.config.api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_r,
            "topic": "news",
            "days": max(1, int(recency / 24)),
            "include_raw_content": False,
        }
        if self.config.include_domains:
            payload["include_domains"] = self.config.include_domains
        if self.config.exclude_domains:
            payload["exclude_domains"] = self.config.exclude_domains

        start = time.perf_counter()
        try:
            res = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
            res.raise_for_status()
        except Exception as e:
            if self.mongo:
                # audit warning only, no synthetic data
                try:
                    import asyncio

                    asyncio.create_task(
                        self.mongo.log_audit_event(
                            "news_search_error",
                            {"query": query, "error": str(e)},
                            run_id=self.run_id,
                        )
                    )
                except Exception:
                    pass
            raise TavilyError(f"Tavily request failed: {e}") from e

        data = res.json()
        results = data.get("results") or []
        if self.mongo:
            try:
                import asyncio

                asyncio.create_task(
                    self.mongo.log_audit_event(
                        "news_search",
                        {
                            "query": query,
                            "count": len(results),
                            "latency_s": time.perf_counter() - start,
                        },
                        run_id=self.run_id,
                    )
                )
            except Exception:
                pass
        return results

    def normalize_results(
        self,
        results: Sequence[Dict[str, Any]],
        *,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Normalize Tavily results into NEWS_EVENTS docs."""
        now = utc_now()
        docs: List[Dict[str, Any]] = []
        for r in results:
            title = r.get("title") or ""
            url = r.get("url") or ""
            content = r.get("content") or ""
            published = r.get("published_date")
            ts = now
            if published:
                try:
                    ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except Exception:
                    ts = now
            doc: Dict[str, Any] = {
                "timestamp": ts,
                "source": "tavily",
                "title": title,
                "url": url,
                "summary": content[:1000],
                "symbols": symbols or [],
                "fetched_at": now,
                "raw": r,
            }
            if self.run_id:
                doc["run_id"] = self.run_id
            docs.append(doc)
        return docs

    async def fetch_recent_news(
        self,
        symbols: Sequence[str],
        *,
        lookback_hours: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch and optionally persist recent crypto news for given symbols."""
        lookback = lookback_hours or self.config.recency_hours
        sym_list = list(symbols)
        query = "crypto " + " OR ".join(sym_list)
        results = self.search(query, max_results=max_results, recency_hours=lookback)
        docs = self.normalize_results(results, symbols=sym_list)

        if self.mongo:
            for d in docs:
                await self.mongo.insert_one(NEWS_EVENTS, d)
        return docs

    async def prune_old_news(self, *, older_than_hours: int = 72) -> int:
        """Delete old news events (optional maintenance)."""
        if not self.mongo:
            return 0
        cutoff = utc_now() - timedelta(hours=older_than_hours)
        col = self.mongo.collection(NEWS_EVENTS)
        res = await col.delete_many({"timestamp": {"$lt": cutoff}})
        return int(res.deleted_count)


__all__ = ["TavilyNewsConnector", "NewsConnectorConfig", "TavilyError"]

