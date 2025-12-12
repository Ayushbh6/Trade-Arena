"""Semantic memory search backend.

Phase 2 implementation stores vectors in Mongo docs when available.
If embeddings are missing, they are computed on the fly (read-only) using OpenRouter.
No vector DB required; cosine similarity is computed in Python.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from Utils.embeddings import embed_texts
from ...data.mongo import MongoManager, jsonify, utc_now
from ...data.schemas import (
    AUDIT_LOG,
    LLM_CALLS,
    MANAGER_DECISIONS,
    TRADE_PROPOSALS,
)


COLLECTIONS_DEFAULT = [TRADE_PROPOSALS, MANAGER_DECISIONS, AUDIT_LOG, LLM_CALLS]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _extract_text(doc: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("symbol", "symbols", "action", "side", "role", "event_type", "decision"):
        val = doc.get(key)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            parts.extend([str(v) for v in val if v is not None])
    for key in ("thesis", "rationale", "notes", "summary", "content"):
        val = doc.get(key)
        if isinstance(val, str):
            parts.append(val)
    payload = doc.get("payload")
    if isinstance(payload, dict):
        for k in ("summary", "notes", "reason", "text"):
            v = payload.get(k)
            if isinstance(v, str):
                parts.append(v)
    return " | ".join(parts)[:4000]


@dataclass
class MemoryMatch:
    timestamp: Any
    source: str
    score: float
    symbol: Optional[str] = None
    summary: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "score": self.score,
            "symbol": self.symbol,
            "summary": self.summary,
            "data": self.data or {},
        }


class MongoEmbeddingBackend:
    """Semantic search over Mongo collections."""

    def __init__(
        self,
        *,
        mongo: MongoManager,
        embedding_model: Optional[str] = None,
        api_key: Optional[str] = None,
        collections: Optional[List[str]] = None,
    ):
        self.mongo = mongo
        self.embedding_model = embedding_model
        self.api_key = api_key
        self.collections = collections or list(COLLECTIONS_DEFAULT)

    async def _fetch_candidates(
        self,
        *,
        agent_id: str,
        lookback_days: int,
        filters: Optional[Dict[str, Any]] = None,
        max_per_collection: int = 200,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        await self.mongo.connect()
        cutoff = utc_now() - timedelta(days=lookback_days)
        filters = filters or {}
        symbols = set(filters.get("symbols") or [])
        event_types = set(filters.get("event_types") or [])

        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for col_name in self.collections:
            col = self.mongo.collection(col_name)
            q: Dict[str, Any] = {"agent_id": agent_id, "timestamp": {"$gte": cutoff}}
            if col_name == AUDIT_LOG:
                q = {"agent_id": agent_id, "timestamp": {"$gte": cutoff}}
                if event_types:
                    q["event_type"] = {"$in": list(event_types)}
            if symbols:
                # Different collections store symbols differently.
                # Prefer strict top-level match when possible, but also support audit payloads.
                if col_name == AUDIT_LOG:
                    q["$or"] = [
                        {"symbols": {"$in": list(symbols)}},
                        {"symbol": {"$in": list(symbols)}},
                        {"payload.symbol": {"$in": list(symbols)}},
                        {"payload.symbols": {"$in": list(symbols)}},
                    ]
                else:
                    q["$or"] = [
                        {"symbols": {"$in": list(symbols)}},
                        {"symbol": {"$in": list(symbols)}},
                    ]
            cursor = col.find(q).sort("timestamp", -1).limit(max_per_collection)
            docs = await cursor.to_list(length=max_per_collection)
            for d in docs:
                candidates.append((col_name, d))
        return candidates

    async def search(
        self,
        *,
        agent_id: str,
        query: str,
        lookback_days: int = 7,
        max_items: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        if min_score is None:
            try:
                min_score = float(os.getenv("MEMORY_MIN_SCORE", "0.2"))
            except Exception:
                min_score = 0.2
        candidates = await self._fetch_candidates(
            agent_id=agent_id, lookback_days=lookback_days, filters=filters
        )
        if not candidates:
            return {
                "agent_id": agent_id,
                "query": query,
                "lookback_days": lookback_days,
                "matches": [],
            }

        texts: List[str] = []
        vectors: List[Optional[List[float]]] = []
        for _, doc in candidates:
            emb = doc.get("embedding")
            if isinstance(emb, list) and emb and isinstance(emb[0], (int, float)):
                vectors.append([float(x) for x in emb])
                texts.append("")
            else:
                vectors.append(None)
                texts.append(_extract_text(doc))

        # Embed query + any missing candidate embeddings in one batch.
        batch_texts = [query] + [t for t in texts if t]
        batch_vecs = embed_texts(
            batch_texts, model=self.embedding_model, api_key=self.api_key
        )
        query_vec = batch_vecs[0]

        missing_iter = iter(batch_vecs[1:])
        filled_vectors: List[List[float]] = []
        for v, t in zip(vectors, texts):
            if v is not None:
                filled_vectors.append(v)
            else:
                filled_vectors.append(next(missing_iter))

        scored: List[MemoryMatch] = []
        for (source, doc), vec in zip(candidates, filled_vectors):
            score = _cosine(query_vec, vec)
            if score < (min_score or 0.0):
                continue
            symbol = doc.get("symbol")
            if symbol is None:
                syms = doc.get("symbols") or []
                if isinstance(syms, list) and syms:
                    symbol = str(syms[0])
            if symbol is None and isinstance(doc.get("payload"), dict):
                payload = doc["payload"]
                ps = payload.get("symbol")
                if isinstance(ps, str):
                    symbol = ps
                else:
                    psl = payload.get("symbols")
                    if isinstance(psl, list) and psl:
                        symbol = str(psl[0])

            summary = doc.get("summary") or doc.get("notes")
            if summary is None and isinstance(doc.get("payload"), dict):
                summary = doc["payload"].get("summary")
            if summary is None:
                summary = doc.get("event_type")
            scored.append(
                MemoryMatch(
                    timestamp=doc.get("timestamp"),
                    source=source,
                    score=float(score),
                    symbol=symbol,
                    summary=summary,
                    data=doc,
                )
            )

        scored.sort(key=lambda m: m.score, reverse=True)
        return jsonify(
            {
                "agent_id": agent_id,
                "query": query,
                "lookback_days": lookback_days,
                "matches": [m.to_dict() for m in scored[:max_items]],
            }
        )


__all__ = ["MongoEmbeddingBackend"]
