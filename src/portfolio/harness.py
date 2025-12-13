from typing import Dict, List, Optional, Any
from datetime import datetime
import pandas as pd
import numpy as np
from dataclasses import dataclass

from src.portfolio.metrics import calculate_roi, calculate_sharpe_ratio
from src.data.mongo import MongoManager, PNL_REPORTS
from src.data.market_data import MarketDataIngestor # To fetch benchmark candles

@dataclass
class AgentEvaluation:
    agent_id: str
    run_id: str
    period_start: datetime
    period_end: datetime
    
    # Absolute Metrics
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    
    # Benchmark Comparison (vs BTC Buy & Hold)
    benchmark_return_pct: float
    alpha_pct: float # Agent Return - Benchmark Return
    beta: float # Correlation * (Agent Vol / Benchmark Vol)
    
    # Efficiency
    turnover_rate: float
    fee_drag_pct: float # Total Fees / Total Equity

class EvaluationHarness:
    """
    Evaluates agent performance against market benchmarks.
    """
    def __init__(self, mongo: MongoManager):
        self.mongo = mongo

    async def evaluate_agent(self, run_id: str, agent_id: str, benchmark_symbol: str = "BTCUSDT") -> AgentEvaluation:
        """
        Computes a comprehensive scorecard for a specific agent in a specific run.
        """
        # 1. Fetch PnL Reports for this run
        reports = await self._fetch_run_reports(run_id)
        if not reports:
            raise ValueError(f"No PnL reports found for run_id: {run_id}")
            
        # 2. Extract Agent Equity Curve
        equity_curve = []
        timestamps = []
        fees_paid = 0.0
        
        start_equity = 0.0
        
        for r in reports:
            ts = r["timestamp"]
            agents = r.get("agent_metrics", {})
            metrics = agents.get(agent_id)
            if metrics:
                equity = metrics.get("total_equity", 0.0)
                if not start_equity:
                    start_equity = equity # simplistic start detection
                    
                equity_curve.append(equity)
                timestamps.append(ts)
                # Note: Fee tracking needs to be cumulative or delta. 
                # PortfolioManager tracks 'cash_balance' which includes fees deducted.
                # To get exact fees, we'd need to sum 'fee' from trade history.
                # For MVP harness, we might estimate or skip fee_drag if not explicitly logged in pnl_reports.
                # (PortfolioManager DOES track trade history internally, but pnl_reports only snapshots summary metrics).
                # Future improvement: Include cumulative_fees in PnL report.

        if not equity_curve:
             raise ValueError(f"No data found for agent: {agent_id}")
             
        start_ts = timestamps[0]
        end_ts = timestamps[-1]
        
        final_equity = equity_curve[-1]
        agent_return = calculate_roi(start_equity, final_equity)
        
        # 3. Fetch Benchmark Data (BTCUSDT)
        # We need opening price at start_ts and closing price at end_ts
        # For MVP, we can query market_snapshots collection if we stored them,
        # or just use the ingestor/client to fetch history.
        # Ideally, we look at the 'market_snapshots' stored during the run.
        
        bench_start_price = await self._get_price_at_time(benchmark_symbol, start_ts)
        bench_end_price = await self._get_price_at_time(benchmark_symbol, end_ts)
        
        benchmark_return = 0.0
        if bench_start_price > 0:
            benchmark_return = ((bench_end_price - bench_start_price) / bench_start_price) * 100.0
            
        # 4. Compute Alpha/Beta
        # Beta requires covariance. We need aligned returns series.
        # This is complex for MVP. Simplification:
        alpha = agent_return - benchmark_return
        
        # 5. Sharpe Calculation (Re-verify)
        returns = pd.Series(equity_curve).pct_change().dropna().tolist()
        sharpe = calculate_sharpe_ratio(returns)
        
        # 6. Drawdown
        # We can take from the last report or recompute
        max_dd = reports[-1].get("agent_metrics", {}).get(agent_id, {}).get("max_drawdown_pct", 0.0)

        return AgentEvaluation(
            agent_id=agent_id,
            run_id=run_id,
            period_start=start_ts,
            period_end=end_ts,
            total_return_pct=agent_return,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            benchmark_return_pct=benchmark_return,
            alpha_pct=alpha,
            beta=0.0, # Todo: Implement beta calculation
            turnover_rate=0.0, # Todo: Implement turnover
            fee_drag_pct=0.0 # Todo: Implement fee drag
        )

    async def _fetch_run_reports(self, run_id: str) -> List[Dict]:
        col = self.mongo.collection(PNL_REPORTS)
        cursor = col.find({"run_id": run_id}).sort("timestamp", 1)
        return await cursor.to_list(length=10000)

    async def _get_price_at_time(self, symbol: str, dt: datetime) -> float:
        # Best effort: find nearest market snapshot
        col = self.mongo.collection("market_snapshots")
        # Find snapshot closest to dt
        # Try finding one <= dt
        doc = await col.find_one(
            {"timestamp": {"$lte": dt}},
            sort=[("timestamp", -1)]
        )
        if not doc:
            # Try >= dt
            doc = await col.find_one(
                {"timestamp": {"$gte": dt}},
                sort=[("timestamp", 1)]
            )
            
        if doc:
            # Extract price for symbol
            # doc structure: per_symbol -> symbol -> last_price
            price = doc.get("per_symbol", {}).get(symbol, {}).get("last_price")
            if price:
                return float(price)
        
        return 0.0
