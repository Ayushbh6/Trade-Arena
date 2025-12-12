"""Position synchronization from Binance into MongoDB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.data.audit import AuditContext, AuditManager
from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import ORDERS, POSITIONS
from src.execution.binance_client import BinanceFuturesClient


@dataclass(frozen=True)
class PositionTrackerConfig:
    include_flat: bool = False


class PositionTracker:
    def __init__(
        self,
        *,
        mongo: MongoManager,
        client: BinanceFuturesClient,
        config: Optional[PositionTrackerConfig] = None,
    ):
        self.mongo = mongo
        self.client = client
        self.config = config or PositionTrackerConfig()

    async def sync_positions(
        self,
        *,
        run_id: str,
        cycle_id: Optional[str] = None,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch exchange positions and upsert into Mongo `positions`.

        Attribution:
        - Binance positions are firm-level. For MVP we attempt best-effort
          attribution by looking up the most recent order doc for the symbol.
        """

        await self.mongo.connect()
        audit = AuditManager(self.mongo)
        audit_ctx = AuditContext(run_id=run_id, agent_id="position_tracker")
        pos_col = self.mongo.collection(POSITIONS)
        ord_col = self.mongo.collection(ORDERS)

        rows = self.client.get_positions()
        out: List[Dict[str, Any]] = []

        await audit.log(
            "positions_sync_start",
            {"cycle_id": cycle_id, "symbols": symbols, "rows": len(rows)},
            ctx=audit_ctx,
        )

        for p in rows:
            symbol = p.get("symbol")
            if not symbol:
                continue
            if symbols is not None and symbol not in symbols:
                continue

            amt = float(p.get("positionAmt") or 0.0)
            if abs(amt) < 1e-12 and not self.config.include_flat:
                continue

            # Best-effort attribution from most recent order we’ve seen for this symbol.
            last_order = (
                await ord_col.find({"symbol": symbol, "run_id": run_id})
                .sort("timestamp", -1)
                .limit(1)
                .to_list(length=1)
            )
            agent_owner = last_order[0].get("agent_owner") if last_order else None

            doc: Dict[str, Any] = {
                "run_id": run_id,
                "cycle_id": cycle_id,
                "timestamp": utc_now(),
                "symbol": symbol,
                "qty": amt,
                "position_side": p.get("positionSide"),
                "avg_entry_price": float(p.get("entryPrice") or 0.0),
                "mark_price": float(p.get("markPrice") or 0.0),
                "unrealized_pnl": float(p.get("unRealizedProfit") or 0.0),
                "leverage": float(p.get("leverage") or 0.0),
                "agent_owner": agent_owner,
                "raw": jsonify(p),
            }

            await pos_col.replace_one(
                {"run_id": run_id, "symbol": symbol},
                jsonify(doc),
                upsert=True,
            )

            out.append(doc)

        await audit.log(
            "positions_sync_complete",
            {"cycle_id": cycle_id, "synced": len(out), "symbols": [d.get("symbol") for d in out]},
            ctx=audit_ctx,
        )
        return jsonify(out)
