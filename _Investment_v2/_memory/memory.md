# Project Memory Log & Roadmap

**Date:** December 20, 2025
**Project:** Autonomous Investment Agent (MSc Data Science Thesis)
**Current Phase:** Multi-Agent Architecture & Prototype Validation

## 1. Project Overview & Goal
The ultimate goal is to build a flagship **Autonomous Coding Agent** for crypto trading. Unlike standard "black box" trading bots, this agent uses a **"CodeAct"** architecture (Think-Code-Observe) to perform rigorous, verifiable quantitative analysis in a Python sandbox before making decisions.

**Key Thesis differentiator:** The agent does not just "predict"; it **researches**, writes code to validate hypotheses, and executes based on data-driven evidence.

## 2. Architecture Implemented
We have established a robust **Manager-Worker** architecture to solve the "Context Window" and "Hallucination" problems.

### **A. The Manager Agent (`agent/manager.py`)**
*   **Role:** Portfolio Manager & Risk Controller.
*   **Responsibility:** High-level strategy, capital allocation, and final decision making.
*   **State:** Holds the "Long Term Memory" (Portfolio State, Market Context).
*   **Tools:**
    *   `get_portfolio_state()`: Real-time balance and positions.
    *   `get_market_snapshot(symbol)`: Instant price/change data.
    *   `consult_quant_researcher(question)`: The bridge to the Quant Agent.
    *   `execute_order(symbol, side, amount)`: Execution (Market orders).

### **B. The Quant Researcher (`agent/core.py`)**
*   **Role:** Execution Specialist & Data Analyst.
*   **Responsibility:** Answering specific questions by writing and executing Python code.
*   **Architecture:** **CodeAct** (Iterative Think -> Code -> Observe loop).
*   **Key Feature:** **Fresh Context**. Every time the Manager calls the Quant, the Quant starts with a blank slate. This ensures high accuracy, zero context bloat from previous tasks, and "fresh eyes" on the data.
*   **Self-Correction:** Validated capability to catch syntax errors (e.g., extra parenthesis) and library errors (e.g., Pandas indexing) and self-heal during the analysis loop.

### **C. The Tooling Layer (`tools/market_data.py`)**
*   **Wrapper:** Custom `BinanceTestnetWrapper` mimicking `ccxt` but using `python-binance` for stability on the Futures Testnet.
*   **Status:** Fully functional for OHLCV fetching, Ticker data, and Account Balance.

## 3. Accomplishments & Verification

### **Phase 1: Core Logic (CLI)**
*   **[DONE] Connection:** Successfully connected to Binance Futures Testnet (Balance: ~10k USDT).
*   **[DONE] Unit Testing:** Verified `fetch_ohlcv` and `fetch_balance` via `tests/test_binance.py`.
*   **[DONE] Manager Tools:** Fixed attribute errors in `fetch_ticker` and standardized `fetch_balance` to support both `['USDT']['free']` and `['free']['USDT']` access patterns.
*   **[DONE] End-to-End Workflow:** Manager -> Quant (9-turn analysis) -> Manager Decision.

### **Phase 2: The "Glass" Interface (UI/UX)**
*   **[DONE] Architecture Shift:** Refactored agents from `print()` logging to **Generator-based Event Streaming**.
*   **[DONE] Backend:** Built a **FastAPI** server with WebSocket (`/ws/chat`) support, Robust CORS, and Health Checks (`/health`).
*   **[DONE] Frontend:** Developed a **Next.js 14** "Glass" Interface:
    *   **Majestic UI:** Split-pane layout (Chat Stream vs. Code/Artifact Viewer).
    *   **Real-time Streaming:** Nested visualization of Manager delegating to Quant.
    *   **Smart Rendering:** `react-markdown` for thought chains, collapsible chips for Tool Calls, and auto-switching tabs (Code/Console).
    *   **UX Polish:** "System Ready" indicators, User message injection, and clean error handling.

## 4. Critical Focus Area: Context & Memory Management
**This is the key success factor for the Thesis.**

*   **Challenge:** As the Manager runs for days, its context window will fill up with tool outputs and Quant reports.
*   **Solution Required:** We cannot just append forever. We need:
    1.  **Summarization:** After every ~5 turns, condense the history into a "Narrative State".
    2.  **Structured Memory:** A database (JSON/SQLite) to store "Active Hypotheses", "Pending Orders", and "Daily P&L" so the Manager can look them up instead of remembering them in the context window.
    3.  **Token Optimization:** Stripping verbose tool outputs (like raw JSON of 500 candles) before passing them to the Manager.

## 5. Next Steps
1.  **Context Management:** Implement the "Context Cleaner" logic to prevent overflow during long sessions.
2.  **Live Trading Verification:** Execute a real test trade (Buy/Sell) via the UI to verify `execute_order` in the loop.
3.  **Database Integration:** Move from ephemeral logs to a persistent database (e.g., SQLite or MongoDB).
4.  **Risk Module:** Implement hard-coded risk checks (e.g., Max Drawdown, Max Position Size) in `agent/manager.py`.

## 6. Current Status
*   **System Health:** **Excellent (Green)**.
*   **Interface:** **Live & Polished (Phase 2 Complete + Dashboard V2)**.
*   **Testnet Funds:** ~10,768 USDT / 0.01 BTC Long.
*   **Immediate Action:** Ready for Context Engineering and Long-running tests.

## Update — Persistence & Autonomous Loop (2025-12-18)

Small operational update after adding persistent storage and the autonomous cycle loop:

- **Database:** Added MongoDB persistence using `motor`.
    - **DB name:** `investment_agent_v2`.
    - **Collections:** `sessions` and `cycles` (each cycle embeds the generated `memory_generated`).
    - Files: `database/connection.py`, `database/models.py` were added to define the connection and Pydantic schemas.

- **Agent Memory Schema:** The summariser now emits structured JSON matching `AgentMemory` with fields:
    - `short_term_summary` (string)
    - `active_hypotheses` (list of strings)
    - `pending_orders` (list of strings)
    - `next_steps` (string)

- **Autonomous Loop:** Implemented a background loop in `server/main.py` that:
    1. Checks for an active `session` document.
    2. Loads the latest cycle memory and injects it into the Manager's system prompt.
    3. Creates a new `cycle` document, streams events live via WebSocket, and saves the cycle when complete.

- **Cycle Cadence:** Controlled by env var `CYCLE_CADENCE` (minutes). Default added to `.env` as `CYCLE_CADENCE=10`.

- **API Endpoints (new):**
    - `POST /start` — create and start a session (accepts `initial_balance`).
    - `POST /stop` — stop the active session.
    - `GET /history` — list recent sessions.
    - `GET /session/{session_id}` — return cycles for a session.

- **WebSocket:** The frontend now subscribes to a live `ws_manager` broadcast (no longer starting new conversational threads). The `/ws/chat` route joins the live feed.

- **Requirements:** `motor` added to `requirements.txt` for async MongoDB access.

## Update — Dashboard & Navigation Polish (2025-12-20)
*   **Dashboard View:** Implemented a new Dashboard interface with KPI cards (Balance, PnL, Active Trades), market charts (recharts), and an Activity Stream.
*   **Focus View:** Repurposed the previous chat interface into an "Agent Focus" view, specific to "Quant Trader 1".
*   **Sidebar Navigation:** 
    *   Added seamless navigation between Dashboard and Focus views.
    *   **New Chat Experience:** Clicking "Quant Trader 1" now calls `resetSession`, clearing the ephemeral state to ensure a "New Run" screen is shown (instead of the last viewed session).
    *   **Session History:** Past runs are grouped by date and load correctly when clicked.
*   **Synchronization:** Implemented real-time synchronization of the "Run Once" / "Start Loop" buttons using WebSocket status updates.
*   **Data Persistence (Fix):**
    *   **Last Decision:** Dashboard now fetches the `last_decision` from the session history via the backend API to ensure it persists across refreshes and when no run is active.
    *   **Robust Formatting:** Implemented strict regex extraction for decisions to prevent long text from breaking the UI layout (e.g., ensuring "HOLD", "BUY", "SELL" are shown concisely).

## Update — Session Routing & Timestamp Integrity (2025-12-20)
*   **Session-Based Routing:**
    *   **Dynamic URLs:** Implemented a proper URL structure for sessions: `/active-agent/[agentId]/session/[sessionId]`.
    *   **Automatic Redirection:** Starting a "Run Once" or "Start Loop" now automatically redirects the user to the specific session URL, ensuring a persistent and shareable state.
    *   **Historical View:** Past runs now load via their unique URLs, and the UI automatically hides execution controls (Start/Stop) when viewing history to prevent state confusion.
*   **Timestamp Fix (Historical Accuracy):**
    *   **Backend Injection:** Updated the `AgentEvent` schema and server logic to inject UTC timestamps into every event at the moment of creation.
    *   **Frontend Polyfill:** Refactored the frontend to prioritize these server-side timestamps. This fixed the bug where all past runs showed "Today's" date, ensuring historical data displays its actual execution time.
*   **API Enhancements:**
    *   Updated `GET /session/{session_id}` to return full session metadata, allowing the UI to display session-level details (like start time and initial balance) accurately.
*   **Next.js 15 Compatibility:** Fixed `params` Promise unwrapping issues in dynamic routes to align with the latest Next.js standards.
## Update — UI Overhaul for 2025 Premium Experience (2025-12-20)
*   **Event-Type Specific Rendering:**
    *   **Thoughts:** Now render in a **purple glass container** with proper markdown support, numbered lists properly spaced with newlines.
    *   **Code Blocks:** Rendered as terminal-style blocks with **macOS traffic lights**, copy button, and syntax highlighting.
    *   **Observations:** Smart parser splits **STDOUT** and **STDERR** into separate, color-coded sections.
    *   **Tool Calls:** Humanized names (e.g., `get_portfolio_state` → "Get Portfolio State"), collapsible arguments.
    *   **Tool Results:** JSON auto-detection with pretty-printing, collapsible for large payloads.
    *   **Decisions:** Prominent **Strategic Decision** cards with action badges (BUY/SELL/HOLD) color-coded.
    *   **Manager Prompts:** Important "Manager started" and "Consulting Quant" messages now highlighted in **indigo containers** with larger, readable text (text-sm) instead of tiny uppercase labels.
*   **Workspace Artifacts Panel:**
    *   **Code History:** Jupyter-style numbered cells (`IN [1]`, `IN [2]`) with copy buttons.
    *   **Console Output:** Smart rendering with STDOUT/STDERR separation and JSON pretty-printing for tool results.
*   **Technical Improvements:**
    *   Fixed Tailwind dynamic class issues by using explicit color maps.
    *   Preserved server timestamps instead of overwriting with local time.
    *   Added custom scrollbar styling for a premium feel.
    *   Standardized font sizes across all message types (text-sm baseline).

## Update — Production-Ready Foundations (2025-12-20)
*   **Redis as Source of Truth:**
    *   Added Redis-backed shared state for `is_running`, `mode`, and `session_id`.
    *   Added pub/sub channels for `agent:events` and `agent:status_updates` to keep all API nodes and UI tabs in sync.
*   **Distributed Locking (Safe Multi-Worker):**
    *   Implemented token-based Redis locks to ensure only the lock owner can release it.
    *   Prevents duplicate cycles when multiple workers are running.
*   **Worker Decoupling:**
    *   API server is now a thin gateway that sets Redis flags.
    *   `worker.py` is the only process that executes trading cycles.
*   **Global Cadence Control:**
    *   Cadence is now stored in Redis (`agent:cadence_minutes`) so all workers share the same schedule.
    *   Ensures one cycle per cadence window even if multiple workers exist.
*   **Run Limit (Optional):**
    *   Added `run_limit` and `run_count` in Redis to support "run N cycles then stop".
    *   If run limit is blank, the agent runs indefinitely until manual stop.
*   **State Cleanup:**
    *   Stop actions now update both Redis and Mongo so UI and DB cannot drift.

## Update — Drill-Down Navigation Architecture (2025-12-20)
*   **Pattern:** Implemented a context-aware "Drill-Down" sidebar similar to ChatGPT/iOS.
*   **Global View (/dashboard):**
    *   Shows "Investment Dashboard" link.
    *   Displays list of "Active Agents" (e.g., Quant Trader 1).
    *   **Hides** all history to maintain a clean high-level overview.
*   **Agent View (/active-agent/...):**
    *   **Back Navigation:** Dashboard link transforms to include a "Back Arrow".
    *   **Context Header:** Shows prominent Agent Name & Status.
    *   **Focused History:** Displays ONLY the history relevant to the current agent.
    *   **Quick Actions:** Introduced a primary "+ Start New Run" button at the top of the list.
*   **Visual Polish:**
    *   Moved from gradients to a solid, flat, professional aesthetic (Neutral-950).
    *   Implemented a dedicated scroll container for the list, ensuring the sidebar header and footer remain fixed.
    *   Verified "View vs. Truth" safety: Clicking "Start New Run" is a purely client-side navigation reset and does not create empty sessions in the backend.

## Update — Graph State Contracts & Audit Trail (2025-12-21)
*   **State Graph Contracts:** Implemented structured state handoffs in the graph runner:
    *   New `Plan` schema (objective, assets, quant_question, timeframes, constraints, expected_outputs).
    *   PLANNING now runs a tooling pass (portfolio/market only) then a JSON-only Plan output pass.
    *   ANALYZING consumes Plan and requires QuantReport JSON.
    *   DECIDING consumes Plan + QuantReport and emits PortfolioDecision JSON.
*   **Audit Trail:** Added a new `state_events` Mongo collection and instrumented micro-step logging:
    *   LLM requests/responses, tool calls/results, parse outcomes, errors, and state transitions.
    *   Quant LLM calls are logged via a new audit hook in `run_quant_agent`.
*   **Standalone CLI Note:** `graph_runner` is currently a standalone CLI runner, so audit events store `run_id` only.
    *   `session_id` / `cycle_id` are intentionally left unset until the graph runner replaces the main engine.

## Update — State-Scoped Prompts & Production Hardening (2025-12-21)
*   **Decoupled Prompts:** Graph runner no longer inherits the legacy manager `SYSTEM_PROMPT`.
*   **State-Scoped System Prompts:**
    *   PLANNING tooling: production-grade tool usage guidance and chaining rules.
    *   PLANNING output: strict Plan schema rules with examples.
    *   DECIDING output: strict PortfolioDecision rules with decision heuristics.
*   **Status:** Core graph infrastructure is now in place; future work focuses on iterative improvements (prompt tuning per state, retry logic, and production hardening).
