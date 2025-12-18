import sys
import os
import unittest
import ccxt
from dotenv import load_dotenv

# Add project root to sys.path so we can import tools
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.market_data import get_binance_testnet

class TestBinanceConnection(unittest.TestCase):
    def setUp(self):
        load_dotenv()
        self.exchange = get_binance_testnet()

    def test_connection_and_balance(self):
        print("\nTesting Binance Connection...")
        try:
            # This requires valid API keys
            balance = self.exchange.fetch_balance()
            print("Successfully fetched balance.")
            
            # Print all non-zero assets
            print("Assets with > 0 balance:")
            found_assets = False
            for asset, amount in balance['total'].items():
                if amount > 0:
                    print(f"- {asset}: {amount}")
                    found_assets = True
            
            if not found_assets:
                print("No assets with balance > 0 found.")
            
            self.assertIsInstance(balance, dict)
            self.assertTrue('total' in balance)
        except ccxt.AuthenticationError:
            self.fail("Authentication failed! Check your BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY in .env")
        except Exception as e:
            self.fail(f"Connection failed with error: {e}")

    def test_fetch_ohlcv(self):
        print("\nTesting Market Data Fetch (OHLCV)...")
        try:
            # Fetch 1 hour candles for BTC/USDT
            symbol = 'BTC/USDT'
            timeframe = '1h'
            limit = 5
            
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            print(f"Fetched {len(ohlcv)} candles for {symbol}.")
            for candle in ohlcv:
                print(f"Time: {candle[0]}, Close: {candle[4]}")
                
            self.assertEqual(len(ohlcv), limit)
            self.assertEqual(len(ohlcv[0]), 6) # Timestamp, Open, High, Low, Close, Volume
            
        except Exception as e:
            self.fail(f"Failed to fetch OHLCV data: {e}")

if __name__ == '__main__':
    unittest.main()
