"""Binance Futures Testnet client wrapper.

Only this layer should ever touch Binance keys. Keep it strategy-neutral and
callable from the orchestrator/executor.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException


class BinanceConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class BinanceCredentials:
    api_key: str
    secret_key: str
    testnet: bool


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_binance_credentials(
    *,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    testnet: Optional[bool] = None,
) -> BinanceCredentials:
    """Resolve credentials from explicit args or environment."""
    use_testnet = testnet if testnet is not None else _env_bool("BINANCE_TESTNET", True)

    if api_key and secret_key:
        return BinanceCredentials(api_key=api_key, secret_key=secret_key, testnet=use_testnet)

    if use_testnet:
        key = os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
        sec = os.getenv("BINANCE_TESTNET_SECRET_KEY") or os.getenv("BINANCE_SECRET_KEY")
    else:
        key = os.getenv("BINANCE_API_KEY")
        sec = os.getenv("BINANCE_SECRET_KEY")

    if not key or not sec:
        raise BinanceConfigError(
            "Missing Binance keys. Set BINANCE_TESTNET_API_KEY/BINANCE_TESTNET_SECRET_KEY "
            "(preferred for futures testnet) or BINANCE_API_KEY/BINANCE_SECRET_KEY in .env."
        )

    return BinanceCredentials(api_key=key, secret_key=sec, testnet=use_testnet)


class BinanceFuturesClient:
    """Thin wrapper over python-binance futures endpoints with testnet support."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        testnet: Optional[bool] = None,
        base_url: Optional[str] = None,
        recv_window: int = 5000,
        audit_mgr: Optional[Any] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        allow_mainnet: Optional[bool] = None,
    ):
        creds = load_binance_credentials(
            api_key=api_key, secret_key=secret_key, testnet=testnet
        )
        self.testnet = creds.testnet
        if allow_mainnet is None:
            allow_mainnet = _env_bool("BINANCE_ALLOW_MAINNET", False)
        self.allow_mainnet = allow_mainnet

        if not self.testnet and not self.allow_mainnet:
            raise BinanceConfigError(
                "Refusing to initialize on mainnet. Set BINANCE_TESTNET=true. "
                "To explicitly allow mainnet (not recommended), set BINANCE_ALLOW_MAINNET=true."
            )
        self.recv_window = recv_window
        self.audit_mgr = audit_mgr
        self.agent_id = agent_id
        self.run_id = run_id

        # python-binance uses testnet flag for spot URLs; we also pass it so any
        # internal URL selection stays consistent.
        self.client = Client(creds.api_key, creds.secret_key, testnet=self.testnet)

        # For futures testnet, override the base URL if provided or use default.
        if self.testnet:
            futures_base = (
                base_url
                or os.getenv("BINANCE_BASE_URL")
                or getattr(Client, "FUTURES_TESTNET_URL", None)
                or "https://testnet.binancefuture.com/fapi"
            )
            futures_base = futures_base.rstrip("/")
            if not futures_base.endswith("/fapi"):
                futures_base = futures_base + "/fapi"
            self.client.FUTURES_URL = futures_base
            # Keep any internal references aligned if present.
            if hasattr(self.client, "FUTURES_TESTNET_URL"):
                self.client.FUTURES_TESTNET_URL = futures_base
        else:
            # Even if allow_mainnet=true, make sure we are not accidentally pointing at testnet.
            if "testnet" in self.client.FUTURES_URL:
                raise BinanceConfigError(
                    f"Mainnet mode but FUTURES_URL looks like testnet: {self.client.FUTURES_URL}"
                )

        # Safety belt: in testnet mode, FUTURES_URL must include 'testnet'.
        if self.testnet and "testnet" not in self.client.FUTURES_URL:
            raise BinanceConfigError(
                f"Testnet mode but FUTURES_URL does not look like testnet: {self.client.FUTURES_URL}. "
                "Check BINANCE_BASE_URL."
            )

        self._audit("binance_init", {"testnet": self.testnet, "futures_url": self.client.FUTURES_URL})

    def _audit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.audit_mgr:
            return
        try:
            # Fire-and-forget; orchestrator can await if desired.
            coro = self.audit_mgr.log_audit_event(
                event_type,
                payload,
                run_id=self.run_id,
                agent_id=self.agent_id,
            )
            if hasattr(coro, "__await__"):
                # schedule on running loop if present
                try:
                    import asyncio

                    loop = asyncio.get_running_loop()
                    loop.create_task(coro)
                except Exception:
                    pass
        except Exception:
            pass

    def ping(self) -> Dict[str, Any]:
        """Light connectivity check."""
        start = time.perf_counter()
        try:
            res = self.client.futures_ping()
            self._audit("binance_ping", {"ok": True, "latency_s": time.perf_counter() - start})
            return res
        except (BinanceAPIException, BinanceRequestException) as e:
            self._audit("binance_ping", {"ok": False, "error": str(e)})
            raise

    def get_account_balance(self, asset: str = "USDT") -> Dict[str, Any]:
        """Return balance dict for a given asset."""
        start = time.perf_counter()
        balances = self.client.futures_account_balance(recvWindow=self.recv_window)
        bal = next((b for b in balances if b.get("asset") == asset), None)
        self._audit(
            "binance_balance",
            {
                "asset": asset,
                "latency_s": time.perf_counter() - start,
                "found": bal is not None,
            },
        )
        if bal is None:
            raise RuntimeError(f"Asset {asset} not found in futures_account_balance")
        return bal

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return futures position info, optionally filtered by symbol."""
        start = time.perf_counter()
        positions = self.client.futures_position_information(
            symbol=symbol, recvWindow=self.recv_window
        )
        self._audit(
            "binance_positions",
            {"symbol": symbol, "count": len(positions), "latency_s": time.perf_counter() - start},
        )
        return positions

    def get_mark_price(self, symbol: str) -> float:
        res = self.client.futures_mark_price(symbol=symbol)
        return float(res["markPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage for a symbol."""
        start = time.perf_counter()
        res = self.client.futures_change_leverage(
            symbol=symbol, leverage=leverage, recvWindow=self.recv_window
        )
        self._audit(
            "binance_set_leverage",
            {"symbol": symbol, "leverage": leverage, "latency_s": time.perf_counter() - start},
        )
        return res

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        leverage: Optional[int] = None,
        client_order_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Place a futures order."""
        if leverage is not None:
            self.set_leverage(symbol, leverage)

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "recvWindow": self.recv_window,
            "reduceOnly": reduce_only,
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("price is required for LIMIT orders")
            params["price"] = price
            params["timeInForce"] = time_in_force

        params.update(kwargs)

        start = time.perf_counter()
        try:
            res = self.client.futures_create_order(**params)
            self._audit(
                "binance_place_order",
                {
                    "symbol": symbol,
                    "side": side,
                    "type": order_type,
                    "qty": quantity,
                    "price": price,
                    "latency_s": time.perf_counter() - start,
                    "order_id": res.get("orderId"),
                },
            )
            return res
        except (BinanceAPIException, BinanceRequestException) as e:
            self._audit(
                "binance_place_order",
                {"symbol": symbol, "side": side, "type": order_type, "error": str(e)},
            )
            raise

    def get_order(
        self,
        *,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch order status."""
        if order_id is None and client_order_id is None:
            raise ValueError("order_id or client_order_id required")
        start = time.perf_counter()
        res = self.client.futures_get_order(
            symbol=symbol,
            orderId=order_id,
            origClientOrderId=client_order_id,
            recvWindow=self.recv_window,
        )
        self._audit(
            "binance_get_order",
            {"symbol": symbol, "order_id": order_id, "latency_s": time.perf_counter() - start},
        )
        return res

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an existing order."""
        if order_id is None and client_order_id is None:
            raise ValueError("order_id or client_order_id required")
        start = time.perf_counter()
        res = self.client.futures_cancel_order(
            symbol=symbol,
            orderId=order_id,
            origClientOrderId=client_order_id,
            recvWindow=self.recv_window,
        )
        self._audit(
            "binance_cancel_order",
            {"symbol": symbol, "order_id": order_id, "latency_s": time.perf_counter() - start},
        )
        return res

    def get_recent_trades(
        self, *, symbol: str, limit: int = 50, start_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch recent trades (fills) for a specific symbol.
        
        Note: This endpoint returns the user's trades, not public market trades.
        """
        start = time.perf_counter()
        params = {"symbol": symbol, "limit": limit, "recvWindow": self.recv_window}
        if start_time:
            params["startTime"] = start_time

        try:
            res = self.client.futures_account_trades(**params)
            self._audit(
                "binance_get_account_trades",
                {"symbol": symbol, "count": len(res), "latency_s": time.perf_counter() - start},
            )
            return res
        except (BinanceAPIException, BinanceRequestException) as e:
            self._audit(
                "binance_get_account_trades",
                {"symbol": symbol, "error": str(e)},
            )
            raise


__all__ = [
    "BinanceConfigError",
    "BinanceCredentials",
    "load_binance_credentials",
    "BinanceFuturesClient",
]
