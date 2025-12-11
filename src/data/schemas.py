"""MongoDB collection names, minimal schemas, and index specs.

Schemas here are *descriptors* for consistency and index creation.
We keep them permissive because LLM/tool payloads evolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymongo import ASCENDING, DESCENDING


IndexSpec = Sequence[Tuple[str, int]]


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    required_keys: Sequence[str]
    indexes: Sequence[IndexSpec]


# Core collections from MVP spec
MARKET_SNAPSHOTS = "market_snapshots"
NEWS_EVENTS = "news_events"
ONCHAIN_EVENTS = "onchain_events"
AGENT_STATES = "agent_states"
TRADE_PROPOSALS = "trade_proposals"
MANAGER_DECISIONS = "manager_decisions"
ORDERS = "orders"
POSITIONS = "positions"
PNL_REPORTS = "pnl_reports"
AUDIT_LOG = "audit_log"

# Added for full LLM observability (raw request/response + metadata)
LLM_CALLS = "llm_calls"


COLLECTION_SPECS: Dict[str, CollectionSpec] = {
    MARKET_SNAPSHOTS: CollectionSpec(
        name=MARKET_SNAPSHOTS,
        required_keys=("timestamp", "symbols"),
        indexes=(
            (("timestamp", DESCENDING),),
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    NEWS_EVENTS: CollectionSpec(
        name=NEWS_EVENTS,
        required_keys=("timestamp", "source"),
        indexes=(
            (("timestamp", DESCENDING),),
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("symbols", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    ONCHAIN_EVENTS: CollectionSpec(
        name=ONCHAIN_EVENTS,
        required_keys=("timestamp", "event_type"),
        indexes=(
            (("timestamp", DESCENDING),),
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("symbols", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    AGENT_STATES: CollectionSpec(
        name=AGENT_STATES,
        required_keys=("agent_id", "role"),
        indexes=(
            (("agent_id", ASCENDING),),
            (("run_id", ASCENDING), ("agent_id", ASCENDING)),
        ),
    ),
    TRADE_PROPOSALS: CollectionSpec(
        name=TRADE_PROPOSALS,
        required_keys=("run_id", "timestamp", "agent_id", "symbol", "action"),
        indexes=(
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("agent_id", ASCENDING), ("timestamp", DESCENDING)),
            (("symbol", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    MANAGER_DECISIONS: CollectionSpec(
        name=MANAGER_DECISIONS,
        required_keys=("run_id", "timestamp"),
        indexes=(
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    ORDERS: CollectionSpec(
        name=ORDERS,
        required_keys=("run_id", "timestamp", "symbol", "side", "qty"),
        indexes=(
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("exchange_order_id", ASCENDING),),
        ),
    ),
    POSITIONS: CollectionSpec(
        name=POSITIONS,
        required_keys=("run_id", "symbol", "qty"),
        indexes=(
            (("run_id", ASCENDING), ("symbol", ASCENDING)),
            (("agent_owner", ASCENDING), ("symbol", ASCENDING)),
        ),
    ),
    PNL_REPORTS: CollectionSpec(
        name=PNL_REPORTS,
        required_keys=("run_id", "timestamp"),
        indexes=(
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    AUDIT_LOG: CollectionSpec(
        name=AUDIT_LOG,
        required_keys=("timestamp", "event_type", "payload"),
        indexes=(
            (("timestamp", DESCENDING),),
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("agent_id", ASCENDING), ("timestamp", DESCENDING)),
            (("event_type", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
    LLM_CALLS: CollectionSpec(
        name=LLM_CALLS,
        required_keys=("timestamp", "provider", "model", "messages", "response"),
        indexes=(
            (("timestamp", DESCENDING),),
            (("run_id", ASCENDING), ("timestamp", DESCENDING)),
            (("agent_id", ASCENDING), ("timestamp", DESCENDING)),
            (("trace_id", ASCENDING),),
            (("model", ASCENDING), ("timestamp", DESCENDING)),
        ),
    ),
}


def get_collection_spec(name: str) -> Optional[CollectionSpec]:
    return COLLECTION_SPECS.get(name)

