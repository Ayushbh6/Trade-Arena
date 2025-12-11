"""Integration-ish test for Binance Futures Testnet client.

Run:
  python tests/test_binance_testnet.py

Requires:
  BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY (or BINANCE_API_KEY/SECRET_KEY)
  BINANCE_TESTNET=true
"""

import os
import sys
import time
from decimal import Decimal, ROUND_UP

from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.execution.binance_client import BinanceFuturesClient  # noqa: E402


def main() -> None:
    client = BinanceFuturesClient()

    print("== Ping ==")
    client.ping()
    print("[OK] ping")

    symbol = os.getenv("BINANCE_TEST_SYMBOL", "BTCUSDT")
    qty = float(os.getenv("BINANCE_TEST_QTY", "0.001"))

    print("\n== Balance ==")
    try:
        bal = client.get_account_balance("USDT")
        print(f"[OK] USDT balance: {bal.get('balance')}")
    except Exception as e:
        msg = str(e)
        if "code=-2015" in msg or "Invalid API-key" in msg:
            raise RuntimeError(
                "Binance rejected the key for futures testnet (-2015). "
                "Make sure these are USD-M Futures testnet keys from https://testnet.binancefuture.com "
                "and BINANCE_TESTNET=true."
            ) from e
        raise

    print("\n== Positions ==")
    pos = client.get_positions(symbol=symbol)
    print(f"[OK] positions fetched for {symbol}: {len(pos)} rows")

    print("\n== Place + cancel limit order ==")
    mark = Decimal(str(client.get_mark_price(symbol)))
    limit_price_dec = (mark * Decimal("0.5")).quantize(Decimal("0.1"))
    limit_price = float(limit_price_dec)

    # Futures testnet enforces a minimum notional (often 100 USDT).
    min_notional = Decimal(os.getenv("BINANCE_MIN_NOTIONAL_USD", "100"))
    step = Decimal(os.getenv("BINANCE_QTY_STEP", "0.001"))
    notional = Decimal(str(qty)) * limit_price_dec
    if notional < min_notional:
        raw_qty = min_notional / limit_price_dec
        # Round UP to the nearest step so notional clears the minimum.
        adj_qty = (raw_qty / step).to_integral_value(rounding=ROUND_UP) * step
        new_notional = adj_qty * limit_price_dec
        print(
            f"[WARN] qty*price={notional:.2f} < {min_notional}; "
            f"bumping qty to {adj_qty} (new notional ~{new_notional:.2f})"
        )
        qty = float(adj_qty)
    order = client.place_order(
        symbol=symbol,
        side="BUY",
        order_type="LIMIT",
        quantity=qty,
        price=limit_price,
        time_in_force="GTC",
        reduce_only=False,
        client_order_id=f"test_{int(time.time())}",
    )
    order_id = order.get("orderId")
    print(f"[OK] placed LIMIT BUY {symbol} qty={qty} price={limit_price} id={order_id}")

    time.sleep(1)
    cancel = client.cancel_order(symbol=symbol, order_id=order_id)
    print(f"[OK] canceled order id={cancel.get('orderId')}")

    print("\n[PASS] Binance testnet connectivity verified.")


if __name__ == "__main__":
    main()
