import sys
import os
import io
import traceback
import tiktoken
from contextlib import redirect_stdout

# Add project root to sys.path to ensure local tools are importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.schema import AgentOutput, AgentEvent, TokenUsage
from utils.openrouter import get_completion
from termcolor import colored

def count_tokens(text: str, model: str = "gpt-4") -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))

def count_message_tokens(messages: list, model: str = "gpt-4") -> int:
    """Return the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    
    num_tokens = 0
    for message in messages:
        num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
        
        # Handle both dicts and ChatCompletionMessage objects
        if isinstance(message, dict):
            items = message.items()
        else:
            # Convert object to dict if possible, or use __dict__
            try:
                # For LiteLLM/OpenAI objects
                items = message.model_dump().items()
            except:
                try:
                    items = message.__dict__.items()
                except:
                    items = []

        for key, value in items:
            if value is None: continue
            num_tokens += len(encoding.encode(str(value)))
            if key == "name":  # if there's a name, the role is omitted
                num_tokens += -1  # role is always required and always 1 token
    num_tokens += 2  # every reply is primed with <im_start>assistant
    return num_tokens

SYSTEM_PROMPT = """
You are a **Senior Quantitative Researcher** and **Python Expert** working at a high-frequency trading firm.
Your goal is to answer the user's financial questions by performing rigorous, data-driven analysis using a Python environment.

### **CORE DIRECTIVES:**
1.  **NO GUESSING:** Never answer based on your internal knowledge alone. Always fetch real-time data to verify.
2.  **ITERATIVE PROCESS:** Do not try to write one giant script. Break your problem into small, verifiable steps.
    *   Step 1: Fetch Data.
    *   Step 2: Verify Data (print head/tail).
    *   Step 3: Calculate Indicators.
    *   Step 4: Execute Trade / Formulate Conclusion.
3.  **TRADING DISCIPLINE:** You do not have to trade every time. If indicators are neutral or conflicting, your recommendation should be **"HOLD"** or **"NO TRADE"**. Quality over quantity.
4.  **CODING STANDARDS:**
    *   Use `pandas`, `numpy`, `ccxt`, `pandas_ta`.
    *   Handle errors gracefully. If data is empty, print why.
    *   **ALWAYS print() your results.** If you don't print, you won't see the data.
    *   **PERSISTENCE:** Variables and imports persist between code blocks in the same session.

### **AVAILABLE TOOLS & LIBRARIES:**
*   **Binance Testnet (CRITICAL):** To access the exchange, you **MUST** use the provided helper. 
    *   **Correct Import:** `from tools.market_data import get_binance_testnet`
    *   **Usage:** `exchange = get_binance_testnet()`
    *   **Note:** Do NOT try to initialize `ccxt.binance()` with your own keys or `test=True` manually. Use the helper.
*   **Data Analysis:** `pandas` (as `pd`), `numpy` (as `np`).
*   **Technical Analysis:** `pandas_ta` (as `ta`). Use it like `df.ta.rsi()` or `ta.sma(df['close'], length=20)`.

### **RESPONSE FORMAT (JSON ONLY):**
You must respond with a valid JSON object matching this schema:
{
  "thought": "Your step-by-step plan for THIS specific turn. Numbered list.",
  "action": "code" | "final_answer",
  "code": "The python code to run (if action is 'code')",
  "final_answer": "Your final conclusion (if action is 'final_answer')"
}
"""

def execute_python_code(code: str):
    """Executes code and captures stdout."""
    f = io.StringIO()
    try:
        with redirect_stdout(f):
            # We provide a shared global dict so the agent can maintain state between code calls if needed
            # For now, we reuse globals() to persist imports and variables across turns
            exec(code, globals())
        return f.getvalue(), None
    except Exception:
        return f.getvalue(), traceback.format_exc()

def run_quant_agent(user_prompt: str, model="x-ai/grok-code-fast-1", verbose=True):
    """
    Generator that streams AgentEvent objects.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    max_iterations = 15
    iteration = 0
    
    if verbose:
        print(colored(f"Agent Goal: {user_prompt}\n", "blue"))

    while iteration < max_iterations:
        iteration += 1
        if verbose:
            print(colored(f"--- Turn {iteration} ---", "yellow")),
        
        # Calculate prompt tokens
        prompt_tokens = count_message_tokens(messages)

        # Get structured output from LLM
        response_text = get_completion(
            messages=messages,
            model=model,
            response_format={"type": "json_object"}
        )
        
        # Calculate completion tokens
        completion_tokens = count_tokens(response_text) if response_text else 0
        total_tokens = prompt_tokens + completion_tokens
        
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
        )

        if not response_text:
            yield AgentEvent(type="error", source="quant", content="No response from LLM.", usage=usage)
            return
        
        try:
            output = AgentOutput.model_validate_json(response_text)
        except Exception as e:
            error_msg = f"SYSTEM ERROR: Invalid JSON format. {str(e)}. Please retry with valid JSON."
            yield AgentEvent(type="error", source="quant", content=f"JSON Error: {str(e)}", usage=usage)
            
            # Feed the error back to the LLM so it can fix it
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": error_msg})
            continue

        # Emit Thought
        if verbose:
            print(colored(f"Thought: {output.thought}", "white", attrs=["bold"]))
        yield AgentEvent(type="thought", source="quant", content=output.thought, usage=usage)

        if output.action == "final_answer":
            if verbose:
                print(colored("\nFinal Answer:", "green", attrs=["bold"]))
                print(output.final_answer)
            yield AgentEvent(type="decision", source="quant", content=output.final_answer, usage=usage)
            return
        
        if output.action == "code":
            if verbose:
                print(colored("Code:", "cyan")),
                print(output.code)
            yield AgentEvent(type="code", source="quant", content=output.code, usage=usage)
            
            stdout, stderr = execute_python_code(output.code)
            
            observation = ""
            if stdout:
                observation += f"STDOUT:\n{stdout}\n"
            if stderr:
                observation += f"STDERR:\n{stderr}\n"
            
            if not observation:
                observation = "Code executed successfully with no output (did you forget to print?)."
            
            if verbose:
                print(colored("Observation:", "magenta")),
                print(observation.strip())
            
            yield AgentEvent(type="observation", source="quant", content=observation)
            
            # Feed back the observation to the LLM
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": f"OBSERVATION:\n{observation}"})
            
    yield AgentEvent(type="error", source="quant", content="Max iterations reached.")

def run_agent(user_prompt: str, model="x-ai/grok-code-fast-1", verbose=True):
    """
    Backward compatibility wrapper for sync calls (used by Manager & Main).
    Collects all streamed events and returns the Final Answer.
    """
    final_answer = None
    for event in run_quant_agent(user_prompt, model, verbose):
        if event.type == "decision":
            final_answer = event.content
        elif event.type == "error":
            return event.content
            
    return final_answer or "No final answer received."
