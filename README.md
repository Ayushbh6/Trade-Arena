# AI-Native Trader Company

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**The world's first AI-native trading company where multiple LLM "traders" run differentiated portfolios, compete on risk-adjusted performance, and operate inside a governed office structure with a CIO/Manager agent supervising execution.**

> **⚠️ Safety First:** This system runs on **Binance Futures Testnet** with fake capital. No real money trading.

## 🎯 Vision

Build an open-source benchmark and platform for AI-native trading, where:
- Multiple LLM agents with different strategies compete for capital allocation
- A Manager agent enforces risk rules and maximizes firm-level returns  
- Every decision is logged, auditable, and replayable
- Agents earn trust through consistent, rule-abiding performance

## 🏗️ Architecture

### The "Office" Model

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Trading Office                         │
├──────────────┬──────────────┬──────────────┬──────────────┤
│   Macro/News  │  On-Chain/   │  Technical/  │  Structure/  │
│   Trader      │  Flows       │  Quant       │  Funding     │
│  Agent 1      │  Trader      │  Trader      │  Trader      │
│               │  Agent 2     │  Agent 3     │  Agent 4     │
└──────────────┴──────────────┴──────────────┴──────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Manager/CIO Agent (LLM + Rules)                │
│  - Risk validation & compliance                             │
│  - Conflict resolution                                      │
│  - Capital reallocation                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Execution Engine (Binance Testnet)              │
│  - Order placement & reconciliation                         │
│  - Position tracking                                        │
└─────────────────────────────────────────────────────────────┘
```

### System Components

- **Data Layer**: Market data, news, on-chain events → MongoDB
- **Feature Engine**: Indicators, volatility regime, market state
- **Agent Orchestrator**: Parallel trader execution, proposal collection
- **Risk & Governance**: Hard rules + Manager LLM approval
- **Execution**: Binance Futures Testnet adapter
- **Portfolio Tracking**: Per-agent and firm-level P&L
- **Audit & Replay**: Immutable event log, replayable runs

## 📊 Trading Strategy

- **Venue**: Binance Futures Testnet (USDT-M perps)
- **Instruments**: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT
- **Cadence**: 6-minute decision cycles
- **Timeframes**: 1m, 5m, 15m, 1h candles
- **Horizon**: 5-30 minute trades (no HFT)

### Agent Mandates

1. **Macro/News Trader**: Medium-horizon narrative swings, high conviction
2. **On-Chain/Flows Trader**: Flow-based edges, regime confirmation
3. **Technical/Quant Trader**: Repeatable setups, frequent small trades  
4. **Market-Structure Trader**: Funding/basis mean reversion (optional)

## 🛠️ Tech Stack

- **Language**: Python 3.9+
- **LLM Router**: OpenRouter (DeepSeek, GPT, Claude support)
- **Database**: MongoDB (Motor async driver)
- **Exchange**: Binance Futures Testnet
- **Data**: yfinance, python-binance, Tavily (news), custom on-chain
- **Orchestration**: APScheduler
- **API**: FastAPI (dashboard)

## 📦 Installation

```bash
# Clone repository
git clone https://github.com/yourusername/ai-native-trader.git
cd ai-native-trader

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your API keys
```

### Required API Keys

```bash
# LLM Access (OpenRouter recommended)
OPENROUTER_API_KEY=sk-or-...
LLM_PROVIDER=openrouter
LLM_MODEL_TRADER_1=deepseek/deepseek-chat
LLM_MODEL_TRADER_2=deepseek/deepseek-chat
LLM_MODEL_TRADER_3=deepseek/deepseek-chat  # macro/news trader
LLM_MODEL_TRADER_4=deepseek/deepseek-chat  # market-structure/funding trader
LLM_MODEL_MANAGER=deepseek/deepseek-chat

# MongoDB
MONGODB_URI=mongodb://localhost:27017

# Binance Testnet
BINANCE_TESTNET=true
BINANCE_TESTNET_API_KEY=...
BINANCE_TESTNET_SECRET_KEY=...

# Trading Config
TRADING_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
TRADING_CADENCE_MINUTES=6
```

## 🚀 Quick Start

### 1. Verify MongoDB Connection

```bash
python tests/test_mongo.py
```

Expected output:
```
[OK] market_snapshot inserted id=...
[OK] llm_call inserted id=...
[OK] audit_log mirror event found
[PASS] Mongo layer sanity checks passed.
```

### 2. Verify OpenRouter Integration

```bash
python tests/test_openrouter.py
```

Expected output:
```
== Tool calling test ==
Assistant raw content: ...
Tool calls from model: ...
Tool result temperature_search(paris) -> 8C
...

== Structured output test (TradeProposal) ==
Schema adherence: OK
{
  "run_id": "...",
  "timestamp": "...",
  "agent_id": "trader_technical_01",
  "role": "technical",
  "action": "none",
  ...
}
```

### 3. Verify Binance Testnet

```bash
python tests/test_binance_testnet.py
```

Expected output:
```
[OK] ping
[OK] USDT balance: 10000.0
[OK] positions fetched for BTCUSDT: 0 rows
[OK] placed LIMIT BUY BTCUSDT qty=0.001 price=...
[OK] canceled order id=...
[PASS] Binance testnet connectivity verified.
```

### 4. Run the Trading System

```bash
# Start the main trading loop
python run.py

# Or with custom config
python run.py --config configs/production.yaml
```

## 📖 Documentation

- **[MVP Specification](Plan/ai-native-trader-comp.md)** - Full technical spec, agent protocols, risk rules
- **[Development Tasks](Plan/tasklist.md)** - Implementation roadmap with 12 phases
- **[Memory Log](docs/MEMORY_LOG.md)** - Development history and decisions
- **[Architecture](docs/ARCHITECTURE.md)** - System design and data flows

## 🔧 Development

### Project Structure

```
ai-native-trader/
├── src/
│   ├── data/           # Market data ingestion & MongoDB layer
│   ├── features/       # Indicators & market state computation
│   ├── agents/         # Trader & Manager agent implementations
│   ├── risk/           # Risk rules & compliance engine
│   ├── execution/      # Binance testnet order execution
│   ├── portfolio/      # Position tracking & P&L calculation
│   ├── orchestrator/   # Main trading loop & agent coordination
│   └── ui/             # Dashboard (FastAPI + frontend)
├── tests/              # Integration tests
├── Plan/               # Specifications & task lists
├── docs/               # Documentation & memory log
├── runs/               # Saved trading sessions for replay
└── Utils/              # Shared utilities (OpenRouter wrapper)
```

### Testing

```bash
# Run all tests
pytest tests/

# Run specific test
python tests/test_openrouter.py
python tests/test_mongo.py
python tests/test_binance_testnet.py

# Check code quality
python -m py_compile src/**/*.py
```

### Adding a New Trader Agent

1. Create agent in `src/agents/your_trader.py`
2. Implement `BaseTrader` interface
3. Define role prompt and tool access
4. Add to orchestrator configuration
5. Write tests in `tests/test_your_trader.py`

Example structure:
```python
from src.agents.base import BaseTrader
from src.data.schemas import TradeProposal

class YourTrader(BaseTrader):
    def __init__(self, agent_id: str, model: str):
        super().__init__(agent_id, model)
        self.role = "your_strategy"
        
    async def generate_proposal(self, market_brief: dict) -> TradeProposal:
        # Your trading logic here
        pass
```

## 🎮 Competition Mechanics

### Trust Score System
- **Increases**: Positive risk-adjusted returns, rule compliance
- **Decreases**: Violations, large drawdowns, copy-paste rationales
- **Impact**: Higher trust = larger position sizes, more weight in manager decisions

### Weekly Rebalancing
Every 7 days, the Manager:
1. Computes risk-adjusted performance per agent
2. Updates trust scores  
3. Reallocates capital (top performer +X%, bottom -X%)
4. Provides feedback to each trader

### Performance Metrics
- ROI, Sharpe/Sortino ratios
- Max drawdown, hit rate
- Average risk:reward
- Rule violations count
- "Originality score" (penalizes generic rationales)

## 🔒 Safety & Risk Management

### Hard Rules (Non-LLM)
- Max firm exposure: 2.0× capital
- Max leverage per position: 3×
- Daily firm stop: -5% drawdown
- Per-trade risk: 0.5-1.0% of agent capital
- Cooldown after stop-out: 2 cycles

### Circuit Breakers
- Volatility spike regime: 50% size reduction
- Consecutive loss streak: auto-reduce budget
- Manager veto power on all trades

### Security
- LLMs never see exchange API keys
- Execution module only component with secrets
- All orders pass through risk checks + manager approval
- Full audit trail in MongoDB

## 📊 Dashboard

Live UI showing:
- **Office Floor**: Agent cards with budget, trust, open positions
- **Proposal Queue**: Pending/approved/vetoed proposals with confidence
- **Manager Console**: Compliance reports, conflicts, final order plan
- **Positions & P&L**: Equity curve, drawdowns, per-agent breakdown
- **Timeline**: Scrollable history of decisions

Access at: `http://localhost:8000`

## 🔄 Replay Mode

Replay any trading session from stored snapshots:

```bash
python -m src.orchestrator.replay --run-id 2025-12-11-session-001
```

Compare original vs replay decisions for evaluation and debugging.

## 🤝 Contributing

1. Read the [MVP Spec](Plan/ai-native-trader-comp.md)
2. Check [Task List](Plan/tasklist.md) for open items
3. Follow the [Memory Log](docs/MEMORY_LOG.md) conventions
4. Write tests for new features
5. Update documentation

### Development Workflow

```bash
# Create feature branch
git checkout -b feature/your-feature-name

# Make changes, write tests

# Run tests and linting
pytest tests/

# Update memory log
echo "## $(date +%Y-%m-%d) — Your Name" >> docs/MEMORY_LOG.md

# Commit with conventional commits
git commit -m "feat: add your feature"

# Push and create PR
git push origin feature/your-feature-name
```

## 📈 Roadmap

### MVP (Current)
- [x] OpenRouter integration with tool calling
- [x] MongoDB schemas and audit logging
- [ ] Single-agent paper trading
- [ ] Multi-agent office with competition
- [ ] Basic dashboard
- [ ] Replay capability

### Post-MVP
- [ ] Custom model fine-tuning on trading data
- [ ] Cross-exchange arbitrage
- [ ] Advanced on-chain analytics
- [ ] Options strategies
- [ ] Community agent marketplace
- [ ] Real-time sentiment analysis

## 📄 License

MIT License - see LICENSE file for details

## ⚠️ Disclaimer

This is research software for testing AI trading strategies on **testnet only**. Not financial advice. No real capital trading. Use at your own risk.

## 🙏 Acknowledgments

- OpenRouter team for LLM routing
- Binance for testnet access
- DeepSeek, OpenAI, Anthropic for model access
- Contributors and testers

---

**Ready to build the future of AI-native trading?** 🚀

For questions or issues, please open a GitHub issue or check the [documentation](docs/).
