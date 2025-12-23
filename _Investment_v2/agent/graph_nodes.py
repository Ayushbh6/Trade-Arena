import json
import time
import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, List
from termcolor import colored

from agent.graph_state import AgentState
from agent.schema import AgentEvent, TokenUsage, PortfolioDecision, QuantReport, TradeAction, AgentMemory, Plan
from agent.core import run_quant_agent
from agent.summarizer import summarize_quant_cycle, generate_cycle_memory
from utils.openrouter import get_completion
from tools.market_data import get_binance_testnet

# Reuse existing tools from manager.py (Refactoring would be cleaner, but importing for speed)
# Ideally we move tools to `agent/tools.py` later.
from agent.manager import get_portfolio_state, get_market_snapshot, execute_order, TOOLS
from database.connection import Database

PLANNING_TOOLS = [
    tool for tool in TOOLS
    if tool.get("function", {}).get("name") in {"get_portfolio_state", "get_market_snapshot"}
]

PLANNING_TOOL_SYSTEM_PROMPT = """
[PLANNING_TOOL_PROMPT]
ROLE: Portfolio Manager (Planning/Tooling).
GOAL: Gather complete market context for planning. No decisions or analysis conclusions here.

TOOL RULES:
- Use ONLY the available tools in this call.
- You may chain tools across multiple turns.
- If a tool returns an error or empty data, call the tool again or fetch an alternative symbol.

TOOL USAGE STRATEGY:
1) Always fetch portfolio first.
2) Then fetch market snapshot(s) for symbols relevant to the user’s instruction.
3) If the user asks about a symbol not yet fetched, fetch it explicitly.
4) If a price snapshot is stale or missing, re-fetch it.

INTERPRETING TOOL OUTPUTS:
- Portfolio: extract free USDT and any open positions.
- Market snapshot: extract symbol, last price, 24h % change.
- If tool output contains an error string, treat it as a failure and retry or pivot.

OUTPUT:
- Do NOT provide any plan or decision here.
- Only tool calls and tool results are expected in this state.
"""

PLAN_OUTPUT_SYSTEM_PROMPT = """
[PLAN_OUTPUT_PROMPT]
ROLE: Portfolio Manager (Planning/Output).
GOAL: Produce ONLY a single JSON object that matches the Plan schema.

STRICT OUTPUT RULES:
- Output JSON only. No prose, no markdown.
- Use only valid symbols for assets (e.g., "BTC/USDT").
- quant_question MUST be explicit and actionable.
- If user asked for specific indicators/timeframes, include them in expected_outputs/timeframes.

Plan JSON Schema:
{plan_schema}

SCHEMA GUIDANCE:
- objective: one sentence intent.
- assets: list of symbols to analyze.
- quant_question: exact instruction for the Quant.
- timeframes: list of timeframes (e.g., ["1h"]).
- constraints: risk or execution notes.
- expected_outputs: list of required indicators/outputs.

Examples:
Example 1:
{{"objective":"Assess ETH/USDT trend on 1h","assets":["ETH/USDT"],"quant_question":"Analyze ETH/USDT 1h trend and compute RSI and MACD. Return signal and indicators.","timeframes":["1h"],"constraints":{{"max_risk_pct":50}},"expected_outputs":["RSI","MACD"]}}

Example 2:
{{"objective":"Check BTC/USDT momentum on 4h","assets":["BTC/USDT"],"quant_question":"Evaluate BTC/USDT 4h momentum using RSI and SMA. Return signal and indicators.","timeframes":["4h"],"constraints":{{}},"expected_outputs":["RSI","SMA_50"]}}
"""

DECISION_OUTPUT_SYSTEM_PROMPT = """
[DECISION_OUTPUT_PROMPT]
ROLE: Portfolio Manager (Decision/Output).
GOAL: Produce ONLY a single JSON object that matches the PortfolioDecision schema.

STRICT OUTPUT RULES:
- Output JSON only. No prose, no markdown.
- action must be one of: "buy", "sell", "hold" (lowercase).
- If action is "hold", quantity must be 0.0.
- If action is "buy" or "sell", quantity must be > 0.

DECISION GUIDELINES:
- Use the Plan + QuantReport as the only sources of truth.
- If QuantReport signal is bearish and there is no position, prefer hold.
- If QuantReport signal is bullish and cash is available, prefer buy.
- If signal is neutral/uncertain, prefer hold.
- If data is missing or contradictory, choose hold with lower confidence.

PortfolioDecision JSON Schema:
{decision_schema}

Example:
{{"action":"hold","asset":"ETH/USDT","quantity":0.0,"confidence":0.62,"reasoning":"RSI/MACD mixed; no clear edge.","strategy_used":"Momentum Check"}}
"""

def _serialize_llm_response(response: Any) -> Any:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "__dict__"):
        return response.__dict__
    return str(response)

def _serialize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    serialized = []
    for msg in messages:
        if hasattr(msg, "model_dump"):
            serialized.append(msg.model_dump())
        elif isinstance(msg, dict):
            serialized.append(msg)
        elif hasattr(msg, "__dict__"):
            serialized.append(msg.__dict__)
        else:
            serialized.append({"value": str(msg)})
    return serialized

def log_state_event(state: AgentState, event_type: str, payload: Dict[str, Any]):
    event = {
        "id": str(uuid.uuid4()),
        "run_id": state.get("run_id"),
        "session_id": state.get("session_id"),
        "cycle_id": state.get("cycle_id"),
        "state": state.get("current_node"),
        "type": event_type,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    }

    async def _write():
        try:
            Database.connect()
            await Database.add_state_event(event)
        except Exception as exc:
            if state.get("verbose"):
                print(f"[Audit] Failed to log state event: {exc}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_write())
    except RuntimeError:
        async def _write_once():
            try:
                # In CLI mode (no persistent loop), we must reset the client 
                # because the previous client is bound to a closed loop.
                if Database.client:
                    Database.client.close()
                    Database.client = None
                
                Database.connect()
                await Database.add_state_event(event)
            except Exception as exc:
                if state.get("verbose"):
                    print(f"[Audit] Failed to log state event: {exc}")
            finally:
                Database.close()

        asyncio.run(_write_once())

def node_scan(state: AgentState) -> AgentState:
    """
    State: SCANNING
    Fetches portfolio and market data.
    """
    if state['verbose']:
        print(colored("\n--- STATE: SCANNING ---", "blue", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "SCANNING"})
        
    # Get Portfolio
    pf_raw = get_portfolio_state()
    portfolio = json.loads(pf_raw)
    
    # Get Market Data
    # STRATEGY CHANGE: Instead of hardcoding a watchlist, we only fetch BTC/USDT as a "Market Proxy" (The Index).
    # The Agent (Manager) is explicitly responsible for deciding what else to fetch based on the user's instruction.
    market_proxy = "BTC/USDT"
    mkt_raw = get_market_snapshot(market_proxy)
    prices = {market_proxy: json.loads(mkt_raw)}
        
    state['market_data'] = {
        "portfolio": portfolio,
        "prices": prices,
        "note": "Agent must explicitly fetch data for other assets using tools." 
    }

    log_state_event(state, "scan_result", {"market_data": state["market_data"]})
    
    # Transition
    state['current_node'] = "PLANNING"
    return state

def node_plan(state: AgentState) -> AgentState:
    """
    State: PLANNING
    Manager decides what to do based on Market Data.
    Similar to the first part of the manager loop.
    """
    if state['verbose']:
        print(colored("\n--- STATE: PLANNING ---", "blue", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "PLANNING"})

    context_msg = f"""
    ### MARKET UPDATE:
    Portfolio: {json.dumps(state['market_data']['portfolio'])}
    Prices: {json.dumps(state['market_data']['prices'])}
    Note: {state['market_data'].get('note', '')}
    """

    tool_messages = [
        {"role": "system", "content": PLANNING_TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": state["instruction"]},
        {"role": "user", "content": context_msg}
    ]

    state_messages = state["messages"]
    state_messages.append({"role": "user", "content": context_msg})
    
    # DEBUG: Print messages to see what LLM sees
    if state['verbose']:
        print(colored("--- MANAGER INPUT MESSAGES ---", "yellow"))
        print(f"Message Count: {len(tool_messages)}")
        print(f"Last Message: {tool_messages[-1]['content'][:200]}...")

    # Tooling pass: allow only portfolio/market tools (no quant, no execution)
    max_tool_turns = 5
    for _ in range(max_tool_turns):
        log_state_event(state, "llm_request", {
            "model": "google/gemini-3-flash-preview",
            "tools": PLANNING_TOOLS,
            "messages": _serialize_messages(tool_messages)
        })
        response = get_completion(tool_messages, tools=PLANNING_TOOLS, model="google/gemini-3-flash-preview")
        log_state_event(state, "llm_response", {"response": _serialize_llm_response(response)})

        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_messages.append(response)

            for tc in response.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)

                log_state_event(state, "tool_call", {"name": func_name, "args": args})

                if func_name == "get_portfolio_state":
                    result = get_portfolio_state()
                elif func_name == "get_market_snapshot":
                    result = get_market_snapshot(args["symbol"])
                else:
                    result = f"Tool not available in PLANNING: {func_name}"

                tool_messages.append({
                    "role": "tool",
                    "name": func_name,
                    "content": result
                })
                state_messages.append({
                    "role": "tool",
                    "name": func_name,
                    "content": result
                })
                log_state_event(state, "tool_result", {"name": func_name, "result": result})
            continue

        break

    # Output pass: force Plan JSON (no tools)
    plan_schema = json.dumps(Plan.model_json_schema(), indent=2)
    plan_system_prompt = PLAN_OUTPUT_SYSTEM_PROMPT.replace("{plan_schema}", plan_schema)
    plan_prompt = """
    **PLANNING OUTPUT REQUIRED.**
    Produce a strict JSON object that matches the Plan schema in the system prompt.
    """
    plan_messages = [
        {"role": "system", "content": plan_system_prompt},
    ] + [m for m in tool_messages if m.get("role") != "system"]
    plan_messages.append({"role": "user", "content": plan_prompt})

    log_state_event(state, "llm_request", {
        "model": "google/gemini-3-flash-preview",
        "response_format": {"type": "json_object"},
        "messages": _serialize_messages(plan_messages)
    })
    response = get_completion(
        plan_messages,
        model="google/gemini-3-flash-preview",
        response_format={"type": "json_object"},
        tools=None
    )
    
    log_state_event(state, "llm_response", {"response": _serialize_llm_response(response)})

    content = response.content if hasattr(response, 'content') else str(response)

    try:
        data = json.loads(content)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        state['plan'] = Plan(**data)
        state_messages.append({"role": "assistant", "content": content})
        log_state_event(state, "plan_parsed", {"plan": state["plan"].model_dump()})
        state['current_node'] = "ANALYZING"
    except Exception as e:
        state['error'] = f"Plan parsing failed: {e}"
        state['retry_count'] += 1
        print(colored(f"[Error] {state['error']}", "red"))
        log_state_event(state, "error", {"message": state["error"]})
        state['current_node'] = "MEMORIZING"

    return state

def node_deciding(state: AgentState) -> AgentState:
    """
    State: DECIDING (Formally formulating the PortfolioDecision)
    """
    if state['verbose']:
        print(colored("\n--- STATE: DECIDING ---", "blue", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "DECIDING"})
        
    plan = state.get("plan")
    quant_report = state.get("quant_report")

    decision_schema = json.dumps(PortfolioDecision.model_json_schema(), indent=2)
    decision_system_prompt = DECISION_OUTPUT_SYSTEM_PROMPT.replace("{decision_schema}", decision_schema)
    prompt = f"""
    **DECISION TIME.**
    Use the plan and quant report to decide. Output a strict JSON object matching this schema:
    {decision_schema}

    **PLAN:**
    {plan.model_dump() if plan else 'None'}

    **QUANT REPORT:**
    {quant_report.model_dump() if quant_report else 'None'}

    **Constraints:**
    - Action: "buy", "sell", "hold" (lowercase)
    - Asset: Symbol string
    """
    
    decision_messages = [
        {"role": "system", "content": decision_system_prompt},
        {"role": "user", "content": state["instruction"]},
        {"role": "user", "content": prompt}
    ]
    
    # Call LLM with JSON mode, NO TOOLS
    log_state_event(state, "llm_request", {
        "model": "google/gemini-3-flash-preview",
        "response_format": {"type": "json_object"},
        "messages": _serialize_messages(decision_messages)
    })
    response = get_completion(decision_messages, model="google/gemini-3-flash-preview", response_format={"type": "json_object"}, tools=None)
    log_state_event(state, "llm_response", {"response": _serialize_llm_response(response)})
    
    content = response.content if hasattr(response, 'content') else str(response)
    
    try:
        data = json.loads(content)
        # Handle list wrapping
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
            
        decision = PortfolioDecision(**data)
        state['decision'] = decision
        
        # APPEND DECISION TO HISTORY
        # Crucial so the Intern knows a decision was made
        state['messages'].append({
            "role": "assistant", 
            "content": content
        })
        log_state_event(state, "decision_parsed", {"decision": decision.model_dump()})
        
        state['current_node'] = "VALIDATING_DECISION"
        
    except Exception as e:
        state['error'] = f"Decision parsing failed: {e}"
        state['retry_count'] += 1
        # Simple retry logic could go here, for now just fail safely
        print(colored(f"[Error] {state['error']}", "red"))
        log_state_event(state, "error", {"message": state["error"]})
        state['current_node'] = "MEMORIZING" # Skip to end
        
    return state

def node_quant(state: AgentState) -> AgentState:
    """
    State: ANALYZING (Quant Execution)
    """
    if state['verbose']:
        print(colored("\n--- STATE: ANALYZING (QUANT) ---", "cyan", attrs=["bold"]))
    log_state_event(state, "state_enter", {
        "node": "ANALYZING",
        "plan": state.get("plan").model_dump() if state.get("plan") else None
    })
        
    plan = state.get("plan")
    question = plan.quant_question if plan else "Analyze market."
    
    events_buffer = []
    quant_report_raw = None
    
    # Run Quant
    def _quant_audit(event_type: str, payload: Dict[str, Any]):
        log_state_event(state, event_type, payload)

    for event in run_quant_agent(question, verbose=state['verbose'], audit_logger=_quant_audit):
        events_buffer.append(event)
        log_state_event(state, "quant_event", {"event": event.model_dump()})
        if event.type == "decision":
            quant_report_raw = event.content # This should be the QuantReport dict/JSON
            
    # Capture Summary
    summary = summarize_quant_cycle(events_buffer)
    log_state_event(state, "quant_summary", {"summary": summary})
    
    # Add summary to Manager's context
    state['messages'].append({
        "role": "tool",
        "name": "consult_quant_researcher",
        "content": json.dumps(summary)
    })
    
    # Store raw report for Validation
    # Accept dict or JSON string
    if isinstance(quant_report_raw, str):
        try:
            quant_report_raw = json.loads(quant_report_raw)
        except Exception:
            pass

    if isinstance(quant_report_raw, dict):
        try:
            state['quant_report'] = QuantReport(**quant_report_raw)
            log_state_event(state, "quant_report_parsed", {"quant_report": state["quant_report"].model_dump()})
            state['current_node'] = "DECIDING"
        except Exception as e:
            state['error'] = f"Quant Output Invalid: {e}"
            log_state_event(state, "error", {"message": state["error"]})
            state['current_node'] = "VALIDATING_QUANT" # Go to retry logic
    else:
        # If it was just a string or empty
        state['error'] = "Quant did not return structured data."
        log_state_event(state, "error", {"message": state["error"]})
        state['current_node'] = "VALIDATING_QUANT"
        
    return state

def node_validate_quant(state: AgentState) -> AgentState:
    """
    State: VALIDATING_QUANT
    Handles Quant errors. In a full graph, this would loop back to Quant with feedback.
    For Phase 2, we just log and return to planning with an error note.
    """
    if state['verbose']:
        print(colored(f"\n--- STATE: VALIDATING QUANT (Error: {state.get('error')}) ---", "red"))
    log_state_event(state, "state_enter", {
        "node": "VALIDATING_QUANT",
        "error": state.get("error")
    })
        
    # Inject error into Manager context so it knows Quant failed
    state['messages'].append({
        "role": "system",
        "content": f"Quant Analysis Failed Validation: {state.get('error')}. Proceed with caution."
    })
    log_state_event(state, "quant_validation_failed", {"error": state.get("error")})
    
    state['current_node'] = "PLANNING"
    return state

def node_validate_decision(state: AgentState) -> AgentState:
    """
    State: VALIDATING_DECISION
    Risk checks.
    """
    if state['verbose']:
        print(colored("\n--- STATE: VALIDATING DECISION ---", "blue", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "VALIDATING_DECISION"})
        
    decision = state['decision']
    if not decision:
        log_state_event(state, "decision_missing", {})
        state['current_node'] = "MEMORIZING"
        return state
        
    # Simple Risk Check: Don't buy if no cash (mock check, real one needs portfolio data)
    # Passed -> Execute
    log_state_event(state, "decision_validated", {"decision": decision.model_dump()})
    state['current_node'] = "EXECUTING"
    return state

def node_execute(state: AgentState) -> AgentState:
    """
    State: EXECUTING
    """
    if state['verbose']:
        print(colored("\n--- STATE: EXECUTING ---", "green", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "EXECUTING"})
        
    d = state['decision']
    if d.action == TradeAction.BUY or d.action == TradeAction.SELL:
        res = execute_order(d.asset, d.action.value, d.quantity)
        log_state_event(state, "execution_result", {"result": res})
        if state['verbose']:
            print(colored(f"[Order] {res}", "green"))
    else:
        log_state_event(state, "execution_result", {"result": "HOLD - No execution."})
        if state['verbose']:
            print(colored("[Order] HOLD - No execution.", "green"))
            
    state['current_node'] = "MEMORIZING"
    return state

def node_memorize(state: AgentState) -> AgentState:
    """
    State: MEMORIZING
    """
    if state['verbose']:
        print(colored("\n--- STATE: MEMORIZING ---", "magenta", attrs=["bold"]))
    log_state_event(state, "state_enter", {"node": "MEMORIZING"})
        
    mem_data = generate_cycle_memory(state['messages'])
    state['memory'] = AgentMemory(**mem_data)
    log_state_event(state, "memory_generated", {"memory": state["memory"].model_dump()})
    
    if state['verbose']:
        print(colored(f"[Memory] {state['memory'].short_term_summary}", "magenta"))
        
    state['current_node'] = "END"
    return state
