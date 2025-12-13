import numpy as np
import pandas as pd
from typing import List, Dict, Union

def calculate_roi(start_value: float, end_value: float) -> float:
    """
    Calculates Return on Investment (ROI) as a percentage.
    """
    if start_value == 0:
        return 0.0
    return ((end_value - start_value) / start_value) * 100.0

def calculate_max_drawdown(equity_curve: List[float]) -> float:
    """
    Calculates Maximum Drawdown from peak equity.
    Returns a positive percentage (e.g., 5.0 for 5% drawdown) or 0.0.
    """
    if not equity_curve or len(equity_curve) < 2:
        return 0.0
    
    peak = equity_curve[0]
    max_dd = 0.0
    
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
            
    return max_dd

def calculate_sharpe_ratio(returns: List[float], risk_free_rate: float = 0.0, periods_per_year: int = 365*24*10) -> float:
    """
    Calculates Annualized Sharpe Ratio.
    Assumes 'returns' is a list of percentage returns per period (e.g. per 6-min cycle).
    Default periods_per_year assumes 6-minute cycles (~87600 periods/year).
    """
    if not returns or len(returns) < 2:
        return 0.0
    
    returns_array = np.array(returns)
    std_dev = np.std(returns_array)
    
    if std_dev == 0:
        return 0.0
    
    avg_return = np.mean(returns_array)
    # Annualize
    annualized_sharpe = (avg_return - risk_free_rate) / std_dev * np.sqrt(periods_per_year)
    return float(annualized_sharpe)

def calculate_sortino_ratio(returns: List[float], target_return: float = 0.0, periods_per_year: int = 365*24*10) -> float:
    """
    Calculates Annualized Sortino Ratio (downside risk only).
    """
    if not returns or len(returns) < 2:
        return 0.0
        
    returns_array = np.array(returns)
    avg_return = np.mean(returns_array)
    
    # Calculate downside deviation
    downside_returns = returns_array[returns_array < target_return]
    
    if len(downside_returns) == 0:
        return 0.0
        
    downside_std = np.std(downside_returns)
    
    if downside_std == 0:
        return 0.0
        
    annualized_sortino = (avg_return - target_return) / downside_std * np.sqrt(periods_per_year)
    return float(annualized_sortino)

def calculate_win_rate(trades: List[Dict]) -> float:
    """
    Calculates Win Rate (percentage of profitable trades).
    Expects trades to have a 'pnl' key.
    """
    if not trades:
        return 0.0
        
    wins = sum(1 for t in trades if t.get('realized_pnl', 0) > 0)
    return (wins / len(trades)) * 100.0

def calculate_profit_factor(trades: List[Dict]) -> float:
    """
    Calculates Profit Factor (Gross Profit / Gross Loss).
    """
    gross_profit = sum(t.get('realized_pnl', 0) for t in trades if t.get('realized_pnl', 0) > 0)
    gross_loss = abs(sum(t.get('realized_pnl', 0) for t in trades if t.get('realized_pnl', 0) < 0))
    
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
        
    return gross_profit / gross_loss

def calculate_avg_rr(trades: List[Dict]) -> float:
    """
    Calculates Average Risk:Reward Ratio based on realized trades.
    Using average win / average loss.
    """
    winning_trades = [t.get('realized_pnl', 0) for t in trades if t.get('realized_pnl', 0) > 0]
    losing_trades = [abs(t.get('realized_pnl', 0)) for t in trades if t.get('realized_pnl', 0) < 0]
    
    if not losing_trades:
        return 0.0 # or inf, but 0 is safer for reporting
    if not winning_trades:
        return 0.0
        
    avg_win = np.mean(winning_trades)
    avg_loss = np.mean(losing_trades)
    
    if avg_loss == 0:
        return 0.0
        
    return float(avg_win / avg_loss)
