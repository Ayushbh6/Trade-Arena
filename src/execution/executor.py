"""Order executor for Binance Futures testnet.

Phase 4.2 scope:
- Idempotent placement using Mongo `orders` collection and Binance `newClientOrderId`.
- Basic sequencing: entry first, then SL/TP.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from src.data.mongo import MongoManager, jsonify, utc_now
from src.data.schemas import ORDERS
from src.data.audit import AuditContext, AuditManager
from src.execution.binance_client import BinanceFuturesClient
from src.execution.schemas import (
    ExecutionOrderType,
    ExecutionReport,
    ExecutionStatus,
    OrderExecutionResult,
    OrderIntent,
    OrderLeg,
    OrderPlan,
)


@dataclass(frozen=True)
class ExecutorConfig:
    max_retries: int = 3
    retry_base_delay_s: float = 0.75
    wait_fill_timeout_s: float = 15.0
    poll_interval_s: float = 0.75


class ExecutionError(RuntimeError):
    pass


def _round_step_down(value: float, step: float) -> float:
    if step <= 0:
        return value
    d = Decimal(str(value))
    s = Decimal(str(step))
    rounded = (d / s).to_integral_value(rounding=ROUND_DOWN) * s
    return float(rounded)


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


class BinanceFilters:
    def __init__(self, *, step_size: float, tick_size: float, min_qty: float, min_notional: float):
        self.step_size = step_size
        self.tick_size = tick_size
        self.min_qty = min_qty
        self.min_notional = min_notional


def _extract_filters(exchange_info: Dict[str, Any], symbol: str) -> BinanceFilters:
    sym = next((s for s in exchange_info.get("symbols", []) if s.get("symbol") == symbol), None)
    if not sym:
        raise ExecutionError(f"Symbol not found in futures_exchange_info: {symbol}")
    filters = {f.get("filterType"): f for f in sym.get("filters", []) if isinstance(f, dict)}
    lot = filters.get("LOT_SIZE") or {}
    price = filters.get("PRICE_FILTER") or {}
    notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    step = _safe_float(lot.get("stepSize")) or 0.0
    min_qty = _safe_float(lot.get("minQty")) or 0.0
    tick = _safe_float(price.get("tickSize")) or 0.0
    min_notional = (
        _safe_float(notional.get("notional"))
        or _safe_float(notional.get("minNotional"))
        or _safe_float(notional.get("min_notional"))
        or 0.0
    )
    if step <= 0 or tick <= 0:
        # Fall back to symbol precision if filters are missing (rare).
        qp = sym.get("quantityPrecision")
        pp = sym.get("pricePrecision")
        step = float(10 ** (-int(qp))) if qp is not None else 0.001
        tick = float(10 ** (-int(pp))) if pp is not None else 0.1
    return BinanceFilters(
        step_size=step,
        tick_size=tick,
        min_qty=max(min_qty, step),
        min_notional=max(min_notional, 0.0),
    )


def _group_key(i: OrderIntent) -> Tuple[Optional[str], Optional[int], str]:
    return i.agent_id, i.trade_index, i.symbol


class OrderExecutor:
    def __init__(
        self,
        *,
        mongo: MongoManager,
        client: BinanceFuturesClient,
        config: Optional[ExecutorConfig] = None,
    ):
        self.mongo = mongo
        self.client = client
        self.config = config or ExecutorConfig()

    async def _orders_col(self):
        await self.mongo.connect()
        return self.mongo.collection(ORDERS)

    async def _find_existing_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        col = await self._orders_col()
        return await col.find_one({"client_order_id": client_order_id})

    async def _insert_order_doc(self, intent: OrderIntent, exchange_res: Dict[str, Any]) -> None:
        col = await self._orders_col()
        doc: Dict[str, Any] = {
            "run_id": intent.run_id,
            "cycle_id": intent.cycle_id,
            "timestamp": utc_now(),
            "agent_id": intent.agent_id,
            "agent_owner": intent.agent_id,
            "trade_index": intent.trade_index,
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": exchange_res.get("origQty") or exchange_res.get("executedQty") or exchange_res.get("quantity"),
            "order_type": exchange_res.get("type"),
            "status": exchange_res.get("status"),
            "exchange_order_id": exchange_res.get("orderId"),
            "client_order_id": intent.client_order_id,
            "intent_id": intent.intent_id,
            "leg": intent.leg,
            "raw": jsonify(exchange_res),
        }
        await col.insert_one(jsonify(doc))

    def _compute_quantity(self, *, symbol: str, notional_usdt: float, mark_price: float, step: float, min_qty: float) -> float:
        if mark_price <= 0:
            raise ExecutionError(f"Invalid mark price for {symbol}: {mark_price}")
        qty = notional_usdt / mark_price
        qty = _round_step_down(qty, step)
        if qty < min_qty:
            # For MVP, we hard-fail rather than silently upsizing.
            raise ExecutionError(
                f"Computed quantity {qty} < min_qty {min_qty} for {symbol}. "
                f"Increase notional_usdt or adjust test sizing."
            )
        return qty

    def _round_price(self, price: float, tick: float) -> float:
        return _round_step_down(price, tick)

    def _place_with_retries(self, *, intent: OrderIntent, qty: float, filters: BinanceFilters) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(self.config.max_retries):
            try:
                order_type = {
                    ExecutionOrderType.market: "MARKET",
                    ExecutionOrderType.limit: "LIMIT",
                    ExecutionOrderType.stop_market: "STOP_MARKET",
                    ExecutionOrderType.take_profit_market: "TAKE_PROFIT_MARKET",
                }[intent.order_type]

                kwargs: Dict[str, Any] = {}
                price: Optional[float] = None
                tif: str = "GTC"

                if intent.order_type == ExecutionOrderType.limit:
                    price = self._round_price(float(intent.limit_price), filters.tick_size)  # type: ignore[arg-type]
                    tif = intent.time_in_force or "GTC"

                if intent.order_type in {ExecutionOrderType.stop_market, ExecutionOrderType.take_profit_market}:
                    kwargs["stopPrice"] = self._round_price(float(intent.trigger_price), filters.tick_size)  # type: ignore[arg-type]

                res = self.client.place_order(
                    symbol=intent.symbol,
                    side=intent.side,
                    order_type=order_type,
                    quantity=qty,
                    price=price,
                    time_in_force=tif,
                    reduce_only=bool(intent.reduce_only),
                    leverage=int(intent.leverage) if intent.leverage is not None and intent.leg == OrderLeg.entry else None,
                    client_order_id=intent.client_order_id,
                    **kwargs,
                )
                return res
            except Exception as e:  # pylint: disable=broad-exception-caught
                last_err = e
                delay = self.config.retry_base_delay_s * (2 ** attempt)
                time.sleep(delay)
        raise ExecutionError(f"Failed to place order after retries: {last_err}") from last_err

    def _get_order_with_retries(self, *, symbol: str, client_order_id: str) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(self.config.max_retries):
            try:
                return self.client.get_order(symbol=symbol, client_order_id=client_order_id)
            except Exception as e:  # pylint: disable=broad-exception-caught
                last_err = e
                delay = self.config.retry_base_delay_s * (2 ** attempt)
                time.sleep(delay)
        raise ExecutionError(f"Failed to get order after retries: {last_err}") from last_err

    async def _wait_for_fill(self, *, symbol: str, client_order_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.config.wait_fill_timeout_s
        last: Optional[Dict[str, Any]] = None
        while time.time() < deadline:
            res = self._get_order_with_retries(symbol=symbol, client_order_id=client_order_id)
            last = res
            status = (res.get("status") or "").upper()
            if status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return res
            await asyncio.sleep(self.config.poll_interval_s)
        return last or {"status": "UNKNOWN"}

    async def execute_plan(self, plan: OrderPlan) -> ExecutionReport:
        await self.mongo.ensure_indexes()
        audit = AuditManager(self.mongo)
        audit_ctx = AuditContext(run_id=plan.run_id, agent_id="execution")
        await audit.log(
            "execution_plan_start",
            {"cycle_id": plan.cycle_id, "intents": [i.model_dump() for i in plan.intents]},
            ctx=audit_ctx,
        )
        col = await self._orders_col()

        # Fetch exchange info once per execution.
        exchange_info = self.client.client.futures_exchange_info()
        filters_by_symbol: Dict[str, BinanceFilters] = {}

        # Group intents by trade so we can sequence entry then exits.
        grouped: Dict[Tuple[Optional[str], Optional[int], str], List[OrderIntent]] = {}
        for intent in plan.intents:
            grouped.setdefault(_group_key(intent), []).append(intent)

        results: List[OrderExecutionResult] = []

        for _, intents in grouped.items():
            entry = next((i for i in intents if i.leg == OrderLeg.entry), None)
            if not entry:
                for i in intents:
                    await audit.log(
                        "execution_intent_skipped",
                        {"reason": "missing_entry_intent", "intent": i.model_dump()},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=i.intent_id,
                            client_order_id=i.client_order_id,
                            symbol=i.symbol,
                            leg=i.leg,
                            status=ExecutionStatus.skipped,
                            error="missing entry intent for trade group",
                        )
                    )
                continue

            symbol = entry.symbol
            if symbol not in filters_by_symbol:
                filters_by_symbol[symbol] = _extract_filters(exchange_info, symbol)
            sym_filters = filters_by_symbol[symbol]

            # Compute quantity from entry notional at current mark.
            mark = self.client.get_mark_price(symbol)
            qty = self._compute_quantity(
                symbol=symbol,
                notional_usdt=entry.notional_usdt,
                mark_price=mark,
                step=sym_filters.step_size,
                min_qty=sym_filters.min_qty,
            )
            notional_after_rounding = qty * mark
            if sym_filters.min_notional > 0 and notional_after_rounding + 1e-9 < sym_filters.min_notional:
                await audit.log(
                    "execution_entry_preflight_failed",
                    {
                        "symbol": symbol,
                        "intent": entry.model_dump(),
                        "mark_price": mark,
                        "qty": qty,
                        "notional_after_rounding": notional_after_rounding,
                        "min_notional": sym_filters.min_notional,
                    },
                    ctx=audit_ctx,
                )
                results.append(
                    OrderExecutionResult(
                        intent_id=entry.intent_id,
                        client_order_id=entry.client_order_id,
                        symbol=symbol,
                        leg=entry.leg,
                        status=ExecutionStatus.failed,
                        error=(
                            f"Order notional after rounding ({notional_after_rounding:.2f}) "
                            f"is below exchange min_notional ({sym_filters.min_notional:.2f}). "
                            f"Increase approved size_usdt for {symbol}."
                        ),
                    )
                )
                continue

            # Place entry idempotently.
            existing = await self._find_existing_by_client_id(entry.client_order_id)
            if existing:
                await audit.log(
                    "execution_order_exists",
                    {"intent": entry.model_dump(), "exchange_order_id": existing.get("exchange_order_id")},
                    ctx=audit_ctx,
                )
                results.append(
                    OrderExecutionResult(
                        intent_id=entry.intent_id,
                        client_order_id=entry.client_order_id,
                        symbol=symbol,
                        leg=entry.leg,
                        status=ExecutionStatus.already_exists,
                        exchange_order_id=existing.get("exchange_order_id"),
                    )
                )
            else:
                try:
                    res = self._place_with_retries(intent=entry, qty=qty, filters=sym_filters)
                    await self._insert_order_doc(entry, res)
                    await audit.log(
                        "execution_order_placed",
                        {"intent": entry.model_dump(), "exchange_res": jsonify(res)},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=entry.intent_id,
                            client_order_id=entry.client_order_id,
                            symbol=symbol,
                            leg=entry.leg,
                            status=ExecutionStatus.placed,
                            exchange_order_id=res.get("orderId"),
                        )
                    )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    await audit.log(
                        "execution_order_failed",
                        {"intent": entry.model_dump(), "error": str(e)},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=entry.intent_id,
                            client_order_id=entry.client_order_id,
                            symbol=symbol,
                            leg=entry.leg,
                            status=ExecutionStatus.failed,
                            error=str(e),
                        )
                    )
                    continue

            # Wait for entry fill (best effort).
            await self._wait_for_fill(symbol=symbol, client_order_id=entry.client_order_id)

            # Place exits (SL/TP) idempotently.
            for exit_intent in [i for i in intents if i.leg in {OrderLeg.stop_loss, OrderLeg.take_profit}]:
                existing = await self._find_existing_by_client_id(exit_intent.client_order_id)
                if existing:
                    await audit.log(
                        "execution_order_exists",
                        {"intent": exit_intent.model_dump(), "exchange_order_id": existing.get("exchange_order_id")},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=exit_intent.intent_id,
                            client_order_id=exit_intent.client_order_id,
                            symbol=symbol,
                            leg=exit_intent.leg,
                            status=ExecutionStatus.already_exists,
                            exchange_order_id=existing.get("exchange_order_id"),
                        )
                    )
                    continue
                try:
                    res = self._place_with_retries(intent=exit_intent, qty=qty, filters=sym_filters)
                    await self._insert_order_doc(exit_intent, res)
                    await audit.log(
                        "execution_order_placed",
                        {"intent": exit_intent.model_dump(), "exchange_res": jsonify(res)},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=exit_intent.intent_id,
                            client_order_id=exit_intent.client_order_id,
                            symbol=symbol,
                            leg=exit_intent.leg,
                            status=ExecutionStatus.placed,
                            exchange_order_id=res.get("orderId"),
                        )
                    )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    await audit.log(
                        "execution_order_failed",
                        {"intent": exit_intent.model_dump(), "error": str(e)},
                        ctx=audit_ctx,
                    )
                    results.append(
                        OrderExecutionResult(
                            intent_id=exit_intent.intent_id,
                            client_order_id=exit_intent.client_order_id,
                            symbol=symbol,
                            leg=exit_intent.leg,
                            status=ExecutionStatus.failed,
                            error=str(e),
                        )
                    )

        # Touch col so linter doesn't complain about unused variable (keeps connect alive)
        _ = col
        report = ExecutionReport(run_id=plan.run_id, cycle_id=plan.cycle_id, results=results, notes=plan.notes)
        await audit.log("execution_plan_complete", {"report": report.model_dump()}, ctx=audit_ctx)
        return report
