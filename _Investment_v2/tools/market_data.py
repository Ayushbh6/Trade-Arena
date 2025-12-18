import os
import pandas as pd
from binance.client import Client
from binance.enums import KLINE_INTERVAL_1HOUR
from dotenv import load_dotenv

load_dotenv()

class BinanceTestnetWrapper:
    """
    A CCXT-like wrapper for python-binance, specialized for Futures Testnet.
    """
    def __init__(self):
        api_key = os.getenv('BINANCE_TESTNET_API_KEY')
        secret_key = os.getenv('BINANCE_TESTNET_SECRET_KEY')
        
        # Initialize client
        self.client = Client(api_key, secret_key, testnet=True)
        
        # Force Futures Testnet URL
        self.client.FUTURES_URL = 'https://testnet.binancefuture.com/'
        
    def fetch_balance(self):
        """Mimics ccxt.fetch_balance()"""
        # In futures, we use futures_account_balance
        balances = self.client.futures_account_balance()
        
        # CCXT structure:
        # {
        #   'info': ...,
        #   'USDT': {'free': 100, 'used': 0, 'total': 100},
        #   'BTC': ...,
        #   'total': {'USDT': 100, ...},
        #   'free': {'USDT': 100, ...},
        #   'used': {'USDT': 0, ...}
        # }
        result = {'total': {}, 'free': {}, 'used': {}}
        
        for asset in balances:
            symbol = asset['asset']
            total = float(asset['balance'])
            available = float(asset['availableBalance']) 
            used = total - available
            
            # Add top-level asset key (e.g., result['USDT'])
            result[symbol] = {
                'free': available,
                'used': used,
                'total': total
            }
            
            # Add to aggregators
            result['total'][symbol] = total
            result['free'][symbol] = available
            result['used'][symbol] = used
            
        return result

    def fetch_ticker(self, symbol):
        """Mimics ccxt.fetch_ticker()"""
        clean_symbol = symbol.replace('/', '')
        ticker = self.client.futures_symbol_ticker(symbol=clean_symbol)
        
        # Need 24h change too? 'futures_symbol_ticker' only gives price.
        # Use futures_ticker for 24h stats.
        stats = self.client.futures_ticker(symbol=clean_symbol)
        
        return {
            'symbol': symbol,
            'last': float(stats['lastPrice']),
            'percentage': float(stats['priceChangePercent']),
            'info': stats
        }

    def fetch_ohlcv(self, symbol, timeframe='1h', limit=100):
        """Mimics ccxt.fetch_ohlcv()"""
        # Map timeframe string to binance constants
        # Simple mapping for now
        tf_map = {
            '1m': Client.KLINE_INTERVAL_1MINUTE,
            '1h': Client.KLINE_INTERVAL_1HOUR,
            '4h': Client.KLINE_INTERVAL_4HOUR,
            '1d': Client.KLINE_INTERVAL_1DAY,
        }
        
        interval = tf_map.get(timeframe, Client.KLINE_INTERVAL_1HOUR)
        
        # python-binance expects symbol without '/'
        clean_symbol = symbol.replace('/', '')
        
        klines = self.client.futures_klines(symbol=clean_symbol, interval=interval, limit=limit)
        
        # CCXT format: [timestamp, open, high, low, close, volume]
        # Binance format: [Open time, Open, High, Low, Close, Volume, Close time, ...]
        data = []
        for k in klines:
            data.append([
                int(k[0]),       # Timestamp
                float(k[1]),     # Open
                float(k[2]),     # High
                float(k[3]),     # Low
                float(k[4]),     # Close
                float(k[5])      # Volume
            ])
            
        return data
        
    def create_order(self, symbol, type, side, amount, price=None):
        """Mimics ccxt.create_order, currently supporting market orders"""
        clean_symbol = symbol.replace('/', '')
        
        if type.lower() == 'market':
            response = self.client.futures_create_order(
                symbol=clean_symbol,
                side=side.upper(),
                type='MARKET',
                quantity=amount
            )
            # Normalize response ID
            response['id'] = response['orderId']
            return response
        else:
            raise NotImplementedError("Only MARKET orders are currently supported in this wrapper.")

def get_binance_testnet():
    return BinanceTestnetWrapper()
