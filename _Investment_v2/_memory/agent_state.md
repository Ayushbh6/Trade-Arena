# Agent State Graph Plan

Goal: Make each graph state produce a structured output contract so transitions are deterministic and quant is always called when required.

## State Flow (Target)
1. SCANNING -> market_data (existing dict)
2. PLANNING -> Plan (new Pydantic schema)
3. ANALYZING -> QuantReport (existing schema)
4. DECIDING -> PortfolioDecision (existing schema)
5. VALIDATING_DECISION -> DecisionValidation (optional, can be simple pass/fail for now)
6. EXECUTING -> ExecutionResult (optional, or reuse tool response)
7. MEMORIZING -> AgentMemory (existing schema)

## Prompt + Tool Segregation Rules
- Do not mix tool calls and structured output in the same LLM call.
- Each state has its own scoped prompt and tool access.
- Tools are only available in states that need them.

### SCANNING + PLANNING
- Tooling pass: allow portfolio/market tools only (no quant).
- Output pass: tools disabled, must emit Plan JSON only.

### ANALYZING
- Quant receives Plan.quant_question + minimal context (assets, timeframe, expected outputs).
- Quant must return QuantReport JSON as final_answer.

### DECIDING
- Manager receives full state so far (market_data + Plan + QuantReport).
- Tools disabled, must emit PortfolioDecision JSON only.

### VALIDATING/EXECUTING
- Deterministic validation; execution tool only if decision passes.

### MEMORIZING
- Summarizer uses full state to produce AgentMemory.

## Contracts to Reuse
- QuantReport (agent/schema.py): output of ANALYZING (quant final_answer must be this schema).
- PortfolioDecision (agent/schema.py): output of DECIDING (manager JSON output).
- AgentMemory (agent/schema.py): output of MEMORIZING.

## New Contract to Add
### Plan (Pydantic)
Minimum fields:
- objective: str (manager’s intent in one sentence)
- assets: list[str] (symbols to analyze, e.g., ["ETH/USDT"])
- quant_question: str (explicit instruction to quant, required)
- timeframes: list[str] (e.g., ["1h"])
- constraints: dict (risk constraints or notes)
- expected_outputs: list[str] (e.g., ["RSI", "MACD"])

Notes:
- PLANNING must always produce Plan JSON.
- If plan cannot be formed, loop in PLANNING with a retry prompt.

## State Handoff Rules
- SCANNING -> PLANNING: append market_data context once.
- PLANNING -> ANALYZING: pass Plan.quant_question to quant.
- ANALYZING -> DECIDING: pass QuantReport + Plan to manager.
- DECIDING -> VALIDATING_DECISION: validate PortfolioDecision vs constraints.
- VALIDATING_DECISION -> EXECUTING: proceed only if validation pass.
- EXECUTING -> MEMORIZING: store final decision + execution status.

## Known Fixes Required
- Ensure quant final_answer returns QuantReport JSON, not a string.
- Ensure planner output is stored on state and appended to messages.
- Ensure tool calls (portfolio/market) are executed in PLANNING and results appended before Plan generation.

## Sequential Plan of Action (Pending Approval)
1. Define the new `Plan` Pydantic schema in `agent/schema.py` and document required fields.
2. Update `graph_nodes.py` to enforce state contracts:
   - PLANNING: run a tool-calling pass (portfolio/market only) then a JSON-only Plan pass.
   - ANALYZING: pass Plan to quant and require QuantReport JSON.
   - DECIDING: JSON-only PortfolioDecision pass using market_data + Plan + QuantReport.
3. Update quant output handling so `final_answer` is parsed into `QuantReport` reliably.
4. Add a new MongoDB collection `state_events` with a write helper in `database/connection.py`.
5. Instrument every state transition and micro-step to write audit events:
   - LLM calls (request/response/tool_calls/usage)
   - Tool calls and tool results
   - Validation outcomes and errors
6. Extend the runtime (graph runner + server engine if needed) to attach `session_id`, `cycle_id`, `state`, and timestamps to every audit event.
7. Verify logging end-to-end by running a single graph cycle and checking `state_events` in Mongo.
