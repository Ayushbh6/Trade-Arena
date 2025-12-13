import pytest
import asyncio
from datetime import datetime, timedelta
from src.data.mongo import MongoManager, PNL_REPORTS, MARKET_SNAPSHOTS
from src.portfolio.harness import EvaluationHarness, AgentEvaluation

def run_async(coro):
    return asyncio.run(coro)

def test_evaluate_agent_alpha():
    async def _test():
        mongo = MongoManager(db_name="investment_test_harness")
        await mongo.connect()
        # Clean up before test
        await mongo.collection(PNL_REPORTS).delete_many({})
        await mongo.collection(MARKET_SNAPSHOTS).delete_many({})

        run_id = "test_run_alpha"
        agent_id = "tech_trader_1"
        
        start_ts = datetime(2025, 1, 1, 10, 0, 0)
        end_ts = datetime(2025, 1, 1, 12, 0, 0)
        
        # 1. Seed Market Data (Benchmark BTC rises 10%)
        await mongo.collection(MARKET_SNAPSHOTS).insert_many([
            {
                "timestamp": start_ts,
                "run_id": run_id,
                "per_symbol": {
                    "BTCUSDT": {"last_price": 50000.0}
                }
            },
            {
                "timestamp": end_ts,
                "run_id": run_id,
                "per_symbol": {
                    "BTCUSDT": {"last_price": 55000.0}
                }
            }
        ])
        
        # 2. Seed PnL Reports (Agent makes 15%)
        # Start: 10000
        # End: 11500
        await mongo.collection(PNL_REPORTS).insert_many([
            {
                "timestamp": start_ts,
                "run_id": run_id,
                "agent_metrics": {
                    agent_id: {
                        "total_equity": 10000.0,
                        "max_drawdown_pct": 0.0
                    }
                }
            },
            {
                "timestamp": start_ts + timedelta(hours=1),
                "run_id": run_id,
                "agent_metrics": {
                    agent_id: {
                        "total_equity": 10500.0,
                        "max_drawdown_pct": 1.0
                    }
                }
            },
            {
                "timestamp": end_ts,
                "run_id": run_id,
                "agent_metrics": {
                    agent_id: {
                        "total_equity": 11500.0,
                        "max_drawdown_pct": 2.0
                    }
                }
            }
        ])
        
        harness = EvaluationHarness(mongo)
        eval_result = await harness.evaluate_agent(run_id, agent_id, "BTCUSDT")
        
        # Assertions
        assert eval_result.agent_id == agent_id
        
        # Benchmark Return: (55000 - 50000)/50000 = 10.0%
        assert eval_result.benchmark_return_pct == 10.0
        
        # Agent Return: (11500 - 10000)/10000 = 15.0%
        assert eval_result.total_return_pct == 15.0
        
        # Alpha: 15.0 - 10.0 = 5.0%
        assert abs(eval_result.alpha_pct - 5.0) < 1e-9
        
        # Max DD from report
        assert eval_result.max_drawdown_pct == 2.0
        
        # Sharpe: Returns are [0.05, 0.095]. 
        # Just checking it's calculated (not 0.0 unless returns are 0)
        assert eval_result.sharpe_ratio > 0.0

    run_async(_test())

def test_evaluate_agent_negative_alpha():
    async def _test():
        mongo = MongoManager(db_name="investment_test_harness")
        await mongo.connect()
        # Clean up
        await mongo.collection(PNL_REPORTS).delete_many({})
        await mongo.collection(MARKET_SNAPSHOTS).delete_many({})

        run_id = "test_run_neg"
        agent_id = "tech_trader_1"
        
        start_ts = datetime(2025, 1, 1, 10, 0, 0)
        end_ts = datetime(2025, 1, 1, 12, 0, 0)
        
        # Benchmark rises 10%
        await mongo.collection(MARKET_SNAPSHOTS).insert_many([
            {"timestamp": start_ts, "per_symbol": {"BTCUSDT": {"last_price": 100.0}}},
            {"timestamp": end_ts, "per_symbol": {"BTCUSDT": {"last_price": 110.0}}}
        ])
        
        # Agent loses 10%
        await mongo.collection(PNL_REPORTS).insert_many([
            {"timestamp": start_ts, "run_id": run_id, "agent_metrics": {agent_id: {"total_equity": 100.0}}},
            {"timestamp": end_ts, "run_id": run_id, "agent_metrics": {agent_id: {"total_equity": 90.0}}}
        ])
        
        harness = EvaluationHarness(mongo)
        eval_result = await harness.evaluate_agent(run_id, agent_id, "BTCUSDT")
        
        # Benchmark: +10%
        # Agent: -10%
        # Alpha: -10 - 10 = -20%
        assert eval_result.benchmark_return_pct == 10.0
        assert eval_result.total_return_pct == -10.0
        assert abs(eval_result.alpha_pct - (-20.0)) < 1e-9

    run_async(_test())
