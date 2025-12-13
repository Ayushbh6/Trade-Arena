import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from src.portfolio.metrics import (
    calculate_roi,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_win_rate,
    calculate_profit_factor
)
from src.portfolio.portfolio import AgentPortfolio, PortfolioManager, PortfolioTrade

# --- Metrics Tests ---

def test_calculate_roi():
    assert calculate_roi(100, 110) == 10.0
    assert calculate_roi(100, 90) == -10.0
    assert calculate_roi(100, 100) == 0.0
    assert calculate_roi(0, 100) == 0.0

def test_calculate_max_drawdown():
    # Peak 100 -> 90 (-10%) -> 110 -> 99 (-10%)
    curve = [100.0, 95.0, 90.0, 105.0, 110.0, 99.0]
    assert calculate_max_drawdown(curve) == 10.0
    
    # Monotonic increase
    curve_up = [100.0, 101.0, 102.0]
    assert calculate_max_drawdown(curve_up) == 0.0

def test_calculate_sharpe_ratio():
    # Flat returns -> 0 vol -> 0 Sharpe
    assert calculate_sharpe_ratio([0.01, 0.01, 0.01]) == 0.0
    
    # Simple positive
    returns = [0.01, 0.02, 0.01, -0.005, 0.015]
    sharpe = calculate_sharpe_ratio(returns)
    assert sharpe > 0

def test_calculate_win_rate():
    trades = [
        {'realized_pnl': 10},
        {'realized_pnl': -5},
        {'realized_pnl': 20},
        {'realized_pnl': -10}
    ]
    assert calculate_win_rate(trades) == 50.0

# --- Portfolio Accounting Tests ---

def test_portfolio_long_lifecycle():
    p = AgentPortfolio("test_agent", 1000.0)
    
    # 1. Buy 1 unit @ 100
    # In Futures accounting, Wallet Balance (Cash) only changes due to Fees or Realized PnL.
    # Buying uses margin, but doesn't spend cash.
    p.on_fill(symbol="BTC", side="buy", quantity=1.0, price=100.0, fee=0.0)
    assert p.cash_balance == 1000.0
    assert p.positions["BTC"].quantity == 1.0
    assert p.positions["BTC"].avg_entry_price == 100.0
    
    # 2. Mark to market @ 110
    # Equity = 1000 (Cash) + (110 - 100) * 1 (Unrealized) = 1010
    p.mark_to_market({"BTC": 110.0})
    metrics = p.get_metrics()
    assert metrics["total_equity"] == 1010.0 # 1000 cash + 10 unrealized
    assert metrics["roi_pct"] == 1.0
    
    # 3. Sell 0.5 unit @ 120 (Partial Take Profit)
    # Realized PnL = (120 - 100) * 0.5 = 10.0
    # New Cash = 1000 + 10 = 1010.
    p.on_fill(symbol="BTC", side="sell", quantity=0.5, price=120.0, fee=0.0)
    assert p.cash_balance == 1010.0

def test_portfolio_futures_accounting_fix():
    """
    Verifying the logic for USDT-M Futures style accounting.
    1. Wallet Balance (Cash) changes ONLY on Realized PnL and Fees.
    2. Equity = Wallet Balance + Unrealized PnL.
    """
    p = AgentPortfolio("test_agent", 1000.0)
    
    # 1. Buy 1 BTC @ 100.
    # Wallet Balance should NOT change (ignoring fees).
    p.on_fill(symbol="BTC", side="buy", quantity=1.0, price=100.0, fee=1.0)
    
    assert p.cash_balance == 999.0 # Only fee deducted
    assert p.positions["BTC"].quantity == 1.0
    
    # 2. Mark to Market @ 110.
    # Unrealized PnL = (110 - 100) * 1 = 10.
    # Equity = 999 + 10 = 1009.
    p.mark_to_market({"BTC": 110.0})
    metrics = p.get_metrics()
    assert metrics["total_equity"] == 1009.0
    
    # 3. Sell 0.5 BTC @ 120.
    # Realized PnL = (120 - 100) * 0.5 = 10.
    # Wallet Balance += 10.
    # New Wallet Balance = 999 + 10 = 1009.
    p.on_fill(symbol="BTC", side="sell", quantity=0.5, price=120.0, fee=1.0)
    
    assert p.cash_balance == 1008.0 # 999 + 10 (profit) - 1 (fee)
    assert p.positions["BTC"].quantity == 0.5
    
    # 4. Mark to Market @ 120.
    # Remaining pos: 0.5 BTC from entry 100.
    # Unrealized PnL = (120 - 100) * 0.5 = 10.
    # Equity = 1008 (Wallet) + 10 (Unrealized) = 1018.
    p.mark_to_market({"BTC": 120.0})
    metrics = p.get_metrics()
    assert metrics["total_equity"] == 1018.0

def test_portfolio_short_lifecycle():
    p = AgentPortfolio("test_agent", 1000.0)
    
    # 1. Short 1 BTC @ 100
    p.on_fill(symbol="BTC", side="sell", quantity=1.0, price=100.0, fee=0.0)
    assert p.cash_balance == 1000.0
    assert p.positions["BTC"].quantity == -1.0
    
    # 2. Price drops to 90 (Profit)
    # Unrealized = (100 - 90) * 1 = 10.
    p.mark_to_market({"BTC": 90.0})
    metrics = p.get_metrics()
    assert metrics["total_equity"] == 1010.0
    
    # 3. Cover 0.5 @ 80.
    # Realized PnL = (100 - 80) * 0.5 = 10.
    p.on_fill(symbol="BTC", side="buy", quantity=0.5, price=80.0, fee=0.0)
    
    assert p.cash_balance == 1010.0
    assert p.positions["BTC"].quantity == -0.5
