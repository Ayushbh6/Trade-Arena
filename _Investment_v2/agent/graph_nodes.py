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
from agent.manager import get_portfolio_state, get_market_snapshot, execute_order, SYSTEM_PROMPT, TOOLS
from database.connection import Database

PLANNING_TOOLS = [
    tool for tool in TOOLS
    if tool.get("function", {}).get("name") in {"get_portfolio_state", "get_market_snapshot"}
]

PLANNING_TOOL_SYSTEM_PROMPT = """
[PLANNING_TOOL_PROMPT]
You are in the PLANNING (tooling) state.
Use ONLY the available tools to gather portfolio/market context.
Do not decide trades here; your role is to scan markets and build context for planning.
"""

PLAN_OUTPUT_SYSTEM_PROMPT = """
[PLAN_OUTPUT_PROMPT]
You are in the PLANNING (output) state.
Produce ONLY a single JSON object that matches the Plan schema.

ENUMS / REQUIRED CHOICES:
- Use only valid symbols for assets (e.g., "BTC/USDT").
- Ensure quant_question is explicit and actionable.

Plan JSON Schema:
{plan_schema}

Examples:
Example 1:
{{"objective":"Assess ETH/USDT trend on 1h","assets":["ETH/USDT"],"quant_question":"Analyze ETH/USDT 1h trend and compute RSI and MACD. Return signal and indicators.","timeframes":["1h"],"constraints":{{"max_risk_pct":50}},"expected_outputs":["RSI","MACD"]}}

Example 2:
{{"objective":"Check BTC/USDT momentum on 4h","assets":["BTC/USDT"],"quant_question":"Evaluate BTC/USDT 4h momentum using RSI and SMA. Return signal and indicators.","timeframes":["4h"],"constraints":{{}},"expected_outputs":["RSI","SMA_50"]}}
"""

DECISION_OUTPUT_SYSTEM_PROMPT = """
[DECISION_OUTPUT_PROMPT]
You are in the DECIDING state.
Produce ONLY a single JSON object that matches the PortfolioDecision schema.
Use the provided Plan + QuantReport.

ENUMS / REQUIRED CHOICES:
- action must be one of: "buy", "sell", "hold" (lowercase).

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

def _ensure_system_prompt(messages: List[Any], tag: str, content: str) -> None:
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        text = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if role == "system" and tag in (text or ""):
            return
    messages.append({"role": "system", "content": content})

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

    messages = state['messages']
    _ensure_system_prompt(messages, "[PLANNING_TOOL_PROMPT]", PLANNING_TOOL_SYSTEM_PROMPT)
    
    # Add Market Context if it's the start
    if len(messages) <= 2:
        context_msg = f"""
        ### MARKET UPDATE:
        Portfolio: {json.dumps(state['market_data']['portfolio'])}
        Prices: {json.dumps(state['market_data']['prices'])}
        Note: {state['market_data'].get('note', '')}
        
        **INSTRUCTION:**
        If the user asks for analysis or trends, you **MUST** call `consult_quant_researcher`. 
        Do NOT try to guess technical indicators yourself.
        Do NOT just say "HOLD" without analysis.
        """
        messages.append({"role": "user", "content": context_msg})
    
    # DEBUG: Print messages to see what LLM sees
    if state['verbose']:
        print(colored("--- MANAGER INPUT MESSAGES ---", "yellow"))
        # print(json.dumps(messages, indent=2)) # Too verbose, just length
        print(f"Message Count: {len(messages)}")
        print(f"Last Message: {messages[-1]['content'][:200]}...")

    # Tooling pass: allow only portfolio/market tools (no quant, no execution)
    max_tool_turns = 5
    for _ in range(max_tool_turns):
        log_state_event(state, "llm_request", {
            "model": "google/gemini-3-flash-preview",
            "tools": PLANNING_TOOLS,
            "messages": _serialize_messages(messages)
        })
        response = get_completion(messages, tools=PLANNING_TOOLS, model="google/gemini-3-flash-preview")
        log_state_event(state, "llm_response", {"response": _serialize_llm_response(response)})

        if hasattr(response, 'tool_calls') and response.tool_calls:
            messages.append(response)

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

                messages.append({
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
    _ensure_system_prompt(messages, "[PLAN_OUTPUT_PROMPT]", plan_system_prompt)
    plan_prompt = """
    **PLANNING OUTPUT REQUIRED.**
    Produce a strict JSON object that matches the Plan schema in the system prompt.
    """
    messages.append({"role": "user", "content": plan_prompt})

    log_state_event(state, "llm_request", {
        "model": "google/gemini-3-flash-preview",
        "response_format": {"type": "json_object"},
        "messages": _serialize_messages(messages)
    })
    response = get_completion(
        messages,
        model="google/gemini-3-flash-preview",
        response_format={"type": "json_object"},
        tools=None
    )
    
    log_state_event(state, "llm_response", {"response": _serialize_llm_response(response)})

    content = response.content if hasattr(response, 'content') else str(response)
    
    if state['verbose']:
        print(colored(f"  Plan Output: {content}", "cyan"))

    try:
        data = json.loads(content)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        state['plan'] = Plan(**data)
        state['messages'].append({"role": "assistant", "content": content})
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
        
    messages = state['messages']
    
    plan = state.get("plan")
    quant_report = state.get("quant_report")

    decision_schema = json.dumps(PortfolioDecision.model_json_schema(), indent=2)
    decision_system_prompt = DECISION_OUTPUT_SYSTEM_PROMPT.replace("{decision_schema}", decision_schema)
    _ensure_system_prompt(messages, "[DECISION_OUTPUT_PROMPT]", decision_system_prompt)
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
    
    messages.append({"role": "user", "content": prompt})
    
    # Call LLM with JSON mode, NO TOOLS
    log_state_event(state, "llm_request", {
        "model": "google/gemini-3-flash-preview",
        "response_format": {"type": "json_object"},
        "messages": _serialize_messages(messages)
    })
    response = get_completion(messages, model="google/gemini-3-flash-preview", response_format={"type": "json_object"}, tools=None)
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
