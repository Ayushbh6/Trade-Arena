import json
import time
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from termcolor import colored
from utils.openrouter import get_completion
from tools.market_data import get_binance_testnet
from agent.core import run_agent, run_quant_agent, count_message_tokens, count_tokens
from agent.schema import AgentEvent, TokenUsage
from database.models import AgentMemory

# Initialize Exchange
exchange = get_binance_testnet()

SYSTEM_PROMPT = """
You are the **Portfolio Manager** of a quantitative crypto trading fund.
Your goal is to manage capital, execute trades, and minimize risk.

### **YOUR TEAM:**
You have a "Quant Researcher" at your disposal. This is a powerful AI with access to a full Python environment, including `pandas`, `numpy`, and `pandas_ta`.
*   **CAPABILITIES:** The Quant can perform deep data analysis, backtest ideas, calculate complex indicators, and execute sophisticated mathematical strategies.
*   **DELEGATION:** **DO NOT** try to calculate technicals yourself. **ALWAYS** delegate complex analysis or strategy verification to the Quant using `consult_quant_researcher`.
*   **STRATEGY:** You can ask for high-level strategies (e.g., "Analyze the correlation between BTC and ETH over the last 24h", "Check for Mean Reversion opportunities", "Evaluate a multi-timeframe trend following strategy"). As long as it can be computed in Python/Pandas, the Quant can do it. The Quant has access to the Binance Testnet via a specialized helper `get_binance_testnet()`.

### **YOUR WORKFLOW:**
1.  **Assess Situation:** Check your portfolio (`get_portfolio_state`) and market price (`get_market_snapshot`).
2.  **Formulate Hypothesis:** E.g., "BTC looks strong, I want to verify if it's a Golden Cross."
3.  **Verify with Quant:** Call `consult_quant_researcher("Check for Golden Cross on BTC 1h...")`.
4.  **Decide:** Read the Quant's report.
    *   If Bullish and you have Cash -> BUY.
    *   If Bearish and you have Position -> SELL.
    *   If Neutral/Unsure -> HOLD.
5.  **Execute:** Use `execute_order` if a trade is needed.

### **RISK MANAGEMENT:**
*   Never invest more than 50% of available cash in one trade.
*   Always verify data over intuition.
*   If the Quant reports an error or "No Trade", do not force a trade.
"""

# --- TOOLS ---

def get_portfolio_state():
    """Returns current USDT balance and open positions."""
    try:
        balance = exchange.fetch_balance()
        usdt_free = balance['USDT']['free']
        
        positions = []
        for asset, amount in balance['total'].items():
            if amount > 0 and asset != 'USDT' and asset != 'USDC':
                positions.append(f"{asset}: {amount}")
                
        return json.dumps({
            "USDT_Free": usdt_free,
            "Positions": positions
        })
    except Exception as e:
        return f"Error fetching portfolio: {e}"

def get_market_snapshot(symbol: str):
    """Returns current price and 24h percentage change."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return json.dumps({
            "Symbol": symbol,
            "Price": ticker['last'],
            "Change_24h_Pct": ticker['percentage']
        })
    except Exception as e:
        return f"Error fetching snapshot for {symbol}: {e}"

def execute_order(symbol: str, side: str, amount: float):
    """
    Executes a MARKET order.
    side: 'buy' or 'sell'
    """
    try:
        # For Testnet Futures, we usually use create_market_order
        # Note: Ensure amount is valid (min quantity rules apply)
        order = exchange.create_order(symbol, 'market', side, amount)
        return json.dumps({
            "Status": "FILLED",
            "Side": side,
            "Amount": amount,
            "Price": order.get('price', 'Market'),
            "OrderID": order['id']
        })
    except Exception as e:
        return f"Error executing order: {e}"

# --- TOOL DEFINITIONS (OpenAI Format) ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_state",
            "description": "Get current USDT balance and open positions.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": "Get current price and 24h change for a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "e.g. BTC/USDT"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consult_quant_researcher",
            "description": "Ask the Quant Researcher to perform deep analysis (SMAs, RSIs, Trends) using Python code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The specific analysis question."}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_order",
            "description": "Execute a market buy or sell order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "e.g. BTC/USDT"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "amount": {"type": "number", "description": "Quantity to trade"}
                },
                "required": ["symbol", "side", "amount"]
            }
        }
    }
]

def run_manager_agent(initial_instruction: str, previous_memory: AgentMemory = None, verbose=True):
    """
    Generator that streams AgentEvent objects for the Manager (and delegates to Quant).
    """
    # Inject Memory into System Prompt
    current_system_prompt = SYSTEM_PROMPT
    if previous_memory:
        memory_str = f"""
### **PREVIOUS CYCLE CONTEXT (IMPORTANT):**
*   **Last Summary:** {previous_memory.short_term_summary}
*   **Active Hypotheses:** {previous_memory.active_hypotheses}
*   **Pending Orders:** {previous_memory.pending_orders}
*   **Planned Next Steps:** {previous_memory.next_steps}

**CONTINUE FROM THIS STATE.** Do not start from scratch.
"""
        current_system_prompt += memory_str

    messages = [
        {"role": "system", "content": current_system_prompt},
        {"role": "user", "content": initial_instruction}
    ]
    
    if verbose:
        print(colored(f"Manager Started: {initial_instruction}\n", "green", attrs=["bold"]))
    yield AgentEvent(type="info", source="manager", content=f"Manager Started: {initial_instruction}")
    
    max_turns = 10
    turn = 0
    
    while turn < max_turns:
        turn += 1
        
        # Calculate prompt tokens
        prompt_tokens = count_message_tokens(messages)

        # 1. Get LLM Response
        response_msg = get_completion(messages, tools=TOOLS, model="google/gemini-3-flash-preview")
        
        # Calculate completion tokens
        completion_text = ""
        if hasattr(response_msg, 'content') and response_msg.content:
            completion_text += response_msg.content
        if hasattr(response_msg, 'tool_calls') and response_msg.tool_calls:
            for tc in response_msg.tool_calls:
                completion_text += str(tc)
        
        completion_tokens = count_tokens(completion_text)
        total_tokens = prompt_tokens + completion_tokens
        
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
        )

        # 2. Check for Tool Calls
        if hasattr(response_msg, 'tool_calls') and response_msg.tool_calls:
            messages.append(response_msg) # Add assistant's thought/tool_call to history
            
            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                
                if verbose:
                    print(colored(f"[Manager] Calling Tool: {func_name} {args}", "yellow") )
                yield AgentEvent(type="tool_call", source="manager", content=f"Calling {func_name} with {args}", metadata={"tool": func_name, "args": args}, usage=usage)
                
                result = None
                if func_name == "get_portfolio_state":
                    result = get_portfolio_state()
                elif func_name == "get_market_snapshot":
                    result = get_market_snapshot(args['symbol'])
                elif func_name == "consult_quant_researcher":
                    # Special handling for Quant Agent to stream its internal events
                    quant_question = args['question']
                    yield AgentEvent(type="info", source="manager", content=f"Consulting Quant: {quant_question}")
                    
                    quant_final_answer = "No answer received from Quant."
                    # Iterate through Quant's generator
                    for quant_event in run_quant_agent(quant_question, verbose=verbose):
                        yield quant_event # Stream quant events upwards
                        if quant_event.type == "decision":
                            quant_final_answer = quant_event.content
                    
                    result = quant_final_answer
                    
                elif func_name == "execute_order":
                    result = execute_order(args['symbol'], args['side'], args['amount'])
                
                if verbose:
                    print(colored(f"[Manager] Tool Result: {str(result)[:100]}...", "yellow") )
                yield AgentEvent(type="tool_result", source="manager", content=str(result), metadata={"tool": func_name})
                
                # Append result to messages
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": str(result)
                })
        
        else:
            # No tool calls, just text response (Final Answer or Question)
            content = response_msg.content if hasattr(response_msg, 'content') else str(response_msg)
            if verbose:
                print(colored(f"\n[Manager] Decision: {content}", "green", attrs=["bold"]))
            yield AgentEvent(type="decision", source="manager", content=content, usage=usage)
            messages.append({"role": "assistant", "content": content})
            break
            
    # --- SUMMARIZATION PHASE ---
    yield AgentEvent(type="info", source="manager", content="Generating Cycle Summary...")
    
    summary_prompt = """
    **CYCLE COMPLETE.**
    Now, summarize your current state into a valid JSON object matching this schema:
    {
        "short_term_summary": "Concise narrative of what you did and saw.",
        "active_hypotheses": ["List of theories you are currently testing"],
        "pending_orders": ["List of any open orders"],
        "next_steps": "What you plan to do in the next cycle."
    }
    """
    
    messages.append({"role": "user", "content": summary_prompt})
    
    # Calculate prompt tokens
    prompt_tokens = count_message_tokens(messages)

    # Force JSON mode
    summary_response_text = get_completion(messages, model="google/gemini-3-flash-preview", response_format={"type": "json_object"})
    
    # Handle case where get_completion returns an object (if tools were somehow involved, though unlikely here)
    content_text = ""
    if hasattr(summary_response_text, 'content'):
        content_text = summary_response_text.content
        summary_response_text = content_text
    else:
        content_text = str(summary_response_text)
        
    completion_tokens = count_tokens(content_text)
    total_tokens = prompt_tokens + completion_tokens
    
    usage = TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens
    )
        
    try:
        memory_data = json.loads(summary_response_text)
        memory = AgentMemory(**memory_data)
        yield AgentEvent(type="memory", source="manager", content=memory.model_dump_json(), usage=usage)
        if verbose:
             print(colored(f"\n[Manager] Memory Generated: {memory.short_term_summary}", "magenta"))
    except Exception as e:
        yield AgentEvent(type="error", source="manager", content=f"Failed to generate memory: {e}", usage=usage)

def run_manager(initial_instruction: str):
    """
    Backward compatibility wrapper for sync calls.
    """
    for event in run_manager_agent(initial_instruction, verbose=True):
        pass

if __name__ == "__main__":
    # Use command line argument if provided, otherwise default
    if len(sys.argv) > 1:
        prompt = sys.argv[1]
    else:
        prompt = "Check BTC status and decide if we should enter a position based on the 1h trend."
        
    run_manager(prompt)