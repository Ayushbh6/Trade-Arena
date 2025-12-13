from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
from src.data.mongo import utc_now
from src.portfolio.metrics import (
    calculate_roi,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_win_rate,
    calculate_profit_factor,
    calculate_avg_rr
)

@dataclass
class PortfolioPosition:
    symbol: str
    quantity: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    market_value: float = 0.0
    
    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "long"
        elif self.quantity < 0:
            return "short"
        return "flat"

@dataclass
class PortfolioTrade:
    symbol: str
    side: str  # 'buy' or 'sell'
    quantity: float
    price: float
    timestamp: datetime
    fee: float = 0.0
    realized_pnl: float = 0.0  # Only relevant for closing trades

@dataclass
class EquityPoint:
    timestamp: datetime
    total_equity: float
    cash_balance: float
    unrealized_pnl: float

class AgentPortfolio:
    """
    Tracks financial state for a single agent.
    Separates financial accounting from execution state.
    """
    def __init__(self, agent_id: str, initial_capital: float):
        self.agent_id = agent_id
        self.initial_capital = initial_capital
        self.cash_balance = initial_capital
        
        # Key: symbol, Value: PortfolioPosition
        self.positions: Dict[str, PortfolioPosition] = {}
        
        # History
        self.trade_history: List[PortfolioTrade] = []
        self.equity_curve: List[EquityPoint] = []
        
        # Record initial state
        self._record_equity_point()

    def _record_equity_point(self):
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_equity = self.cash_balance + total_unrealized
        
        self.equity_curve.append(EquityPoint(
            timestamp=utc_now(),
            total_equity=total_equity,
            cash_balance=self.cash_balance,
            unrealized_pnl=total_unrealized
        ))

    def on_fill(self, symbol: str, side: str, quantity: float, price: float, fee: float):
        """
        Handle a trade fill. Updates cash, positions, and logs trade.
        FIFO accounting could be complex, so we use Weighted Average Price (WAP) for simplicity.
        """
        # 1. Update Cash (Deduct fees immediately)
        self.cash_balance -= fee
        
        # 2. Log Trade
        trade = PortfolioTrade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            timestamp=utc_now(),
            fee=fee
        )
        
        # 3. Update Position Logic
        if symbol not in self.positions:
            self.positions[symbol] = PortfolioPosition(symbol=symbol)
            
        pos = self.positions[symbol]
        
        # Determine direction
        is_buy = side.lower() == 'buy'
        signed_qty = quantity if is_buy else -quantity
        
        # Logic for P&L realization vs Avg Entry update
        # Case A: Increasing position (or opening) -> Update Avg Entry
        if pos.quantity == 0 or (pos.quantity > 0 and is_buy) or (pos.quantity < 0 and not is_buy):
            total_cost = (pos.quantity * pos.avg_entry_price) + (signed_qty * price)
            new_qty = pos.quantity + signed_qty
            
            if new_qty != 0:
                pos.avg_entry_price = abs(total_cost / new_qty)
            else:
                pos.avg_entry_price = 0.0
            
            pos.quantity = new_qty
            
        # Case B: Decreasing position (closing) -> Realize P&L
        else:
            # Calculate P&L on the portion being closed
            # For a long position, closing means selling (price - entry)
            # For a short position, closing means buying (entry - price)
            
            closed_qty = abs(signed_qty)
            
            if pos.quantity > 0: # Long closing
                pnl = (price - pos.avg_entry_price) * closed_qty
            else: # Short closing
                pnl = (pos.avg_entry_price - price) * closed_qty
                
            # Add realized P&L to cash
            self.cash_balance += pnl
            trade.realized_pnl = pnl
            
            # Update quantity
            pos.quantity += signed_qty # signed_qty is negative for sell, positive for buy
            
            # If position flipped (e.g. long 10 -> sell 20 -> short 10)
            # The code above simplifies this. A flip is: Close 10, Open 10 short.
            # Ideally, the executor shouldn't flip in one fill, but if it does:
            # This logic handles "reducing to zero". Flipping requires splitting the trade.
            # MVP Assumption: Fills don't flip positions in a single tick without closing first.
            
            if pos.quantity == 0:
                pos.avg_entry_price = 0.0

        self.trade_history.append(trade)
        # Equity point will be updated at the end of the cycle via mark_to_market

    def mark_to_market(self, market_prices: Dict[str, float]):
        """
        Updates unrealized P&L for all positions based on current market prices.
        """
        for symbol, pos in self.positions.items():
            if symbol in market_prices:
                current_price = market_prices[symbol]
                pos.current_price = current_price
                
                if pos.quantity > 0:
                    pos.market_value = pos.quantity * current_price
                    pos.unrealized_pnl = (current_price - pos.avg_entry_price) * pos.quantity
                elif pos.quantity < 0:
                    pos.market_value = pos.quantity * current_price
                    pos.unrealized_pnl = (pos.avg_entry_price - current_price) * abs(pos.quantity)
                else:
                    pos.market_value = 0.0
                    pos.unrealized_pnl = 0.0
                    
        self._record_equity_point()

    def get_metrics(self) -> Dict:
        """
        Returns a dictionary of current performance metrics.
        """
        current_equity = self.equity_curve[-1].total_equity
        equity_series = [e.total_equity for e in self.equity_curve]
        
        # Calculate returns series for Sharpe
        returns = pd.Series(equity_series).pct_change().dropna().tolist()
        
        return {
            "total_equity": current_equity,
            "cash_balance": self.cash_balance,
            "roi_pct": calculate_roi(self.initial_capital, current_equity),
            "max_drawdown_pct": calculate_max_drawdown(equity_series),
            "sharpe_ratio": calculate_sharpe_ratio(returns),
            "win_rate_pct": calculate_win_rate([t.__dict__ for t in self.trade_history if t.realized_pnl != 0]),
            "profit_factor": calculate_profit_factor([t.__dict__ for t in self.trade_history if t.realized_pnl != 0]),
            "avg_rr": calculate_avg_rr([t.__dict__ for t in self.trade_history if t.realized_pnl != 0]),
            "total_trades": len(self.trade_history)
        }

class PortfolioManager:
    """
    Central registry for all agent portfolios and the firm-wide view.
    """
    def __init__(self):
        self.portfolios: Dict[str, AgentPortfolio] = {}
        self.firm_capital = 0.0
        
    def initialize_agent(self, agent_id: str, initial_capital: float):
        if agent_id in self.portfolios:
            # If re-initializing, deciding whether to reset or keep history is a policy choice.
            # MVP: Reset if called.
            pass
        self.portfolios[agent_id] = AgentPortfolio(agent_id, initial_capital)
        self.firm_capital += initial_capital

    def on_fill(self, agent_id: str, symbol: str, side: str, quantity: float, price: float, fee: float = 0.0):
        if agent_id in self.portfolios:
            self.portfolios[agent_id].on_fill(symbol, side, quantity, price, fee)
            
    def mark_to_market(self, market_prices: Dict[str, float]):
        for portfolio in self.portfolios.values():
            portfolio.mark_to_market(market_prices)

    def get_portfolio_summary(self) -> Dict:
        """
        Aggregates metrics across all agents.
        """
        summary = {}
        firm_equity = 0.0
        
        for agent_id, portfolio in self.portfolios.items():
            metrics = portfolio.get_metrics()
            summary[agent_id] = metrics
            firm_equity += metrics["total_equity"]
            
        summary["firm_total"] = {
            "total_equity": firm_equity,
            "roi_pct": calculate_roi(self.firm_capital, firm_equity)
        }
        return summary
