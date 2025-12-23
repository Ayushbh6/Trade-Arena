# AI Architecture Upgrade - Phase 2 Roadmap (Dec 2025)

**Goal:** Elevate context engineering, memory management, and agent reliability from "Functional MVP" to "2025 Production Standard."

10. **The "Independent Summarizer" Agent (The "Intern")**
    *Why waste High-IQ tokens on low-IQ administrative work?*
    - **Role:** A cheap, fast model (Gemini Flash / GPT-4.1-mini) dedicated to compression.
    - **Task A: Quant Output Compression (Intra-Cycle):** 
        - **Trigger:** Quant finishes analysis.
        - **Input:** 500 lines of Python debug logs, dataframes, and charts.
        - **Output:** A clean 5-line summary for the Manager. ("Analyzed BTC. Trend is Bullish. RSI 70. Recommendation: BUY").
        - **Benefit:** The Manager context stays pristine.
    - **Task B: Session Archival (Inter-Cycle):**
        - **Trigger:** Manager finishes the cycle.
        - **Input:** The full conversation history of the cycle.
        - **Output:** The `AgentMemory` JSON (Next Steps, Active Hypotheses).
        - **Benefit:** Async execution. User sees "Done" immediately, while the Intern files the paperwork in the background.

## 1. Intra-Session Memory (Cycle-to-Cycle)
**Current Status:**
- Uses `AgentMemory` schema passed into the system prompt.
- **Mechanism:** "Rolling Summary" (The output of Cycle N becomes the input of Cycle N+1).
- **Critique:**
    - **"Chinese Whispers" Effect:** Repeatedly summarizing a summary leads to information degradation. Critical details from Cycle 1 might be lost by Cycle 10.
    - **Context Window Inefficiency:** We are just dumping text.
    - **Lack of "Raw" Access:** The agent can't verify *exactly* what price it saw 3 cycles ago, only what it *remembered* seeing.

**2025 Improvements:**
- **Sliding Window Log:** Instead of just a summary, pass the last N raw events (decisions/trades) + the summary.
- **State Graph:** Move towards a state-machine approach (e.g., `Searching` -> `Validating` -> `Executing`) rather than a generic prompt every time.

## 2. Structured Output Schema (The "Action" Layer)
**Current Status:**
- Relies on `final_answer` string and regex parsing for keywords like `*HOLD*` or `*BUY*`.
- **Risk:** LLMs are chatty. Parsing is brittle. Hard to use this data for fine-tuning later.

**Proposed Schema (Strict Pydantic):**
```python
class TradeDecision(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

class FinalDecision(BaseModel):
    decision: TradeDecision
    asset: Optional[str] # e.g., "BTC/USDT"
    quantity: Optional[float]
    confidence_score: float # 0.0 to 1.0
    rationale: str # Structured reasoning for the log
    strategy_used: str # e.g., "RSI Divergence", "Trend Following"
```
**Benefits:**
- Deterministic execution.
- Creates a clean, labeled dataset (`{inputs} -> {decision: "buy", strategy: "RSI"}`) for future fine-tuning/RLHF.

## 3. Cross-Session Memory (Long-Term Persistence)
**Current Status:**
- **Non-existent.** Every "Run" (Session) starts with a blank brain (Lobotomy).
- **Impact:** The agent makes the same mistakes every day. It doesn't learn from yesterday's losses.

**Strategy:**
- **Tier 1 (MVP - "The Notepad"):** 
    - At the start of a new session, inject the `AgentMemory` from the *last completed session*.
    - Gives continuity of "Next Steps".
    - **Tier 2 (The "Hippocampus" - Best Bang for Buck):**
    - **Recommendation:** **ChromaDB (Local / Embedded Mode)**.
    - **Why:** 
        - **True Semantic Power:** Matches "Market Crash" with "Bearish Downturn" (MongoDB Regex can't do this).
        - **Zero Infra:** Runs inside your Python process (just `pip install chromadb`). Saves data to a local folder. No new Docker containers or cloud bills.
        - **Scalable:** Identical API to server-based vector DBs for when you upgrade to Supabase/Pinecone later.
    - **Workflow:**
        1. **Archiving:** Store Cycle Summaries + PnL in Chroma.
        2. **Retrieval:** Query: *"Have I seen high volatility with successful RSI trades?"* -> Returns top 3 most similar past cycles.
        3. **Context Injection:** "Recall: In Cycle #42 (Similary: 92%), you traded RSI in high vol and WON. Plan: Repeat strategy."

## 4. The "State Graph" Architecture (LangGraph Style)
**Current Status:** 
- **"The Generalist" Prompting:** One giant system prompt handles Scanning, Thinking, Coding, and Trading simultaneously.
- **Risk:** High "distraction" rate, token waste, and potential to skip critical verification steps (e.g., trading without a backtest).

**The 2025 Standard: "The Assembly Line"**
Break the agent's logic into distinct, deterministic **Nodes** (States). The agent can only exist in one state at a time, with clear transition rules.

### Proposed States:
1.  **`SCANNING` (The Watchman):** 
    - Fast, low-cost market scan. 
    - *Transition:* If trigger found -> `VALIDATING`. If not -> `SLEEP`.
2.  **`VALIDATING` (The Scientist):** 
    - Runs Python backtests/quant logic on the specific trigger found. 
    - *Transition:* If win-rate > threshold -> `EXECUTING`. If not -> `SCANNING`.
3.  **`EXECUTING` (The Sniper):** 
    - Calculates position size based on risk rules and places orders.
    - *Transition:* -> `MONITORING`.
4.  **`MONITORING` (The Risk Manager):** 
    - Tracks open positions, P/L, and Stop-Loss/Take-Profit triggers.

### Why This Wins:
- **Deterministic Reliability:** Forces the agent to follow a specific "First verify, then trade" workflow.
- **Prompt Optimization:** Use small, specialized prompts for each state (improves LLM accuracy).
- **Cost Efficiency:** Stops execution early if the `SCANNING` phase returns "Boring Market."
- **Granular Debugging:** You can see exactly where the agent failed (e.g., "The Scanner found a lead, but the Scientist rejected it").

## 5. Context Engineering & Token Optimization
**The Challenge:** The "Manager" calling the "Quant" multiple times can lead to massive token bloat if we just dump everything into one context window.

**Current Behavior (Verified):**
- **Token Usage:** When Manager calls Quant multiple times, **NO** previous conversation history (tokens) is passed. Each call is initialized with a fresh "System" + "User" prompt.
    - *Verdict:* Safe from token bloat, but bad for continuity.
- **Python State (Hidden Risk):** The `exec(code, globals())` in `agent/core.py` shares the *module-level* global dictionary.
    - *Verdict:* Variables from Turn 1 *do* persist in the background, but the Agent *doesn't know* they exist (hallucination risk).

**Proposed 2025 Optimizations (Ranked by "Bang for Buck"):**

6.  **Intra-Quant Context Engineering (The 100k Limit)**
    *Maximizing "Bang for Buck" within a Single Quant Loop.*

    **The Goal:** Never exceed 100k Input Tokens, even during complex, multi-step analysis.

    **Strategy A: The "Result-Only" History (For Successful Turns)**
    - **Concept:** Once a turn is effectively completed (Code ran -> Output produced), the Agent doesn't need to see the full raw Python code it just wrote. It only needs to know *what it did* and *what the result was*.
    - **Mechanism:**
        - **Turn N (Input):** Full System Prompt + History.
        - **Turn N (Output):** Generated `Thought` + `Code` + `Stdout`.
        - **Action (Post-Turn):** Collapse the history entry for Turn N.
        - **Compressed Entry:** 
            ```json
            {
                "role": "assistant",
                "content": "SUMMARY: I calculated RSI for BTC. Used `pandas_ta`. \nRESULT: RSI is 72.3 (Overbought)." 
            }
            ```
        - **Savings:** Drops the potentially massive Python code block and verbose intermediate thoughts.

    **Strategy B: The "Error-Pruning" Pipeline (For Failed Turns)**
    - **Current Flow:** Bad Code -> Error -> Agent tries to fix -> History keeps (Bad Code, Error, Fix). This is garbage.
    - **New Flow:**
        1. Agent writes `Bad Code` (Turn 2).
        2. Execution fails with `Error`.
        3. **Reviewer Agent** (Side Process) takes `(Bad Code, Error)` and returns `Fixed Code`.
        4. **History Rewrite:** We retroactively **REPLACE** the Agent's Turn 2 output with the `Fixed Code` from the Reviewer.
        5. **Effect:** The main context window *never sees the failure*. It looks like the Agent got it right on the first try.


    
    7.  **Smart Context Compression (The "Handoff" Context)**
    - **Goal:** Allow Quant Turn 2 to build on Turn 1 without paying the full token cost.
    - **Definition of "Turn":** Here, "Turn" means a **distinct function call** by the Manager (e.g., `consult_quant("Analyze BTC")` is Turn 1; `consult_quant("Analyze ETH")` is Turn 2). It does *not* refer to the internal iterations loop.
    - **Creation Mechanism (Crucial):** 
        1. **End of Turn 1:** Quant returns "Bullish".
        2. **Instant Action:** The raw log of Turn 1 is sent to the **Independent Summarizer (Intern)**.
        3. **Intern Output:** Returns the `Compressed Summary JSON`.
        4. **Storage:** Manager holds this JSON in `previous_quant_context` variable.
    - **Injection Mechanism:**
        - **Start of Turn 2:** Manager calls `run_quant(prompt="Analyze ETH", context=previous_quant_context)`.
        - The Quant sees: "System: You previously analyzed BTC (Bullish). Now analyze ETH."
    - **Schema for Handoff:**
        ```json
        {
            "prev_thought": "Validated BTC trend as bullish.",
            "prev_code_summary": "Defined functions: calculate_rsi, check_trend. Variables: btc_df.",
            "prev_result": "BTC RSI is 72 (Overbought)."
        }
        ```
    - **Benefit:** Reduces 10k context -> ~200 tokens.

8.  **The "Reflexion" Pattern (Error Handling)**
    - **Workflow:** 
        - If `exec()` returns an error (Traceback), **STOP**.
        - Do **NOT** feed the error back into the main "Quant" context (which is expensive GPT-4/Claude).
        - **Fork:** Send `(Bad Code + Traceback)` to a cheap "Junior Fixer" agent (Gemini Flash / GPT-3.5).
        - **Return:** The Jr. Fixer returns the *corrected code*.
        - **Resume:** The main Quant executes the corrected code as if it wrote it perfectly.
    - **Benefit:** Keeps the main context clean and "High IQ".


4.  **Token Budgeting & Limits (Hard Caps):**
    - **Quant Budget:** Hard cap at 100k tokens. 
    - **Strategy:** If `history > 80k`, trigger a "Summarize & Clear" event. Store the summary, wipe the messages list, keep the Python variables (if `globals` safe).
    - **Manager Budget:** 120k tokens. Uses "Sliding Window" of Cycle Summaries (keep last 5 raw summaries + 1 master summary).

11. **AI Architecture & Execution Environment (The "Brain" Upgrade)**

    **A. Language Choice: Python vs. C++**
    - **Verdict:** Stick to **Python** for V1/V2.
    - **Why:** 
        - The bottleneck is the *LLM Thinking Time* (seconds), not the *Math Calculation Time* (milliseconds).
        - Python has the best ecosystem (`pandas_ta`, `numpy`, `scikit-learn`) for AI agents.
        - **Optimization:** If backtests get slow, we use `@jit` (Numba) or `vectorbt` (Vectorized Backtesting) within Python. C++ is overkill complexity right now.

    **B. The Execution Environment (The "Sandbox")**
    - **Current Status:** `exec()` running in the main process (Dangerous, Shared State, "Lightweight").
    - **The Upgrade:** **Persistent Isolated Sandboxes (Docker).**
    - **Capabilities (The "Goldman" Stack):**
        - We build a custom Docker Image with pre-installed "Heavy Information" libraries:
        - **Core:** `pandas`, `numpy`, `scipy` (Advanced Stats).
        - **Financial:** `pandas_ta` (Technical Indicators), `vectorbt` (Fast Backtesting).
        - **Machine Learning:** `scikit-learn` (Regressions, Clustering), `prophet` (Time Series Forecasting).
        - **Visuals:** `matplotlib`, `mplfinance` (Candlestick Charts).
    - **Benefit:** Real, rigorous science. Not just text. "Show me the chart."

12. **Schema Updates & Tool Integration Strategy**
    *Making the new toys play nicely together.*

    **A. Schema Updates (Supporting Images):**
    - **Current:** `AgentEvent.content` is just a string.
    - **New:** We need to support **Multi-Modal Content**.
    - **Update:** 
        ```python
        class AgentEvent(BaseModel):
            type: str
            content: str # Text summary
            image_base64: Optional[str] = None # For Charts
            metadata: dict = {}
        ```
    - **UI Implication:** Frontend needs to render the Base64 image if present.



    **C. "Rigorous Analysis" (Solving the "Hold" Problem)**
    - **Problem:** Agents are lazy. They look at one SMA, say "Ambiguous", and decide HOLD.
    - **Solution: Force Deep Drill-Downs.**
    - **Mechanism:** The **Research Protocol**.
    - **Protocol:**
        1. **Mandatory Multi-Frame Check:** You *cannot* decide until you check 15m, 1h, 4h.
        2. **Mandatory Statistical Significance:** "Do not show me a trend unless Correlation > 0.8".
        3. **The "Devil's Advocate" Loop:**
            - Agent: "I see a Buy signal."
            - System Prompt: "Okay, now write a Python script to prove why this trade might FAIL (drawdown analysis)."
            - Agent: *Runs risk analysis* -> "Actually, downside is too high. HOLD." (This is a *Smart* Hold, not a Lazy Hold).
