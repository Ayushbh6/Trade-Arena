import json
from typing import List, Dict, Any
from agent.schema import AgentEvent
from utils.openrouter import get_completion

SUMMARY_SYSTEM_PROMPT = """
You are a **Technical Communicator** for a Quantitative Hedge Fund.
Your job is to read the raw execution logs of a "Quant Researcher" (an AI agent) and compress them into a concise, structured summary for the "Portfolio Manager".

### **INPUT:**
You will receive a list of "Events" from the Quant's execution cycle. These events include:
- `thought`: The Quant's internal reasoning.
- `code`: Valid Python code executed.
- `observation`: The output of that code (often DataFrames, plots, inputs).
- `error`: Any runtime errors.

### **OUTPUT:**
You must produce a **SINGLE JSON OBJECT** containing:
1.  `key_data_points`: A summary of the most important data found (e.g., "BTC RSI=75", "Correlation=0.9").
2.  `actions_taken`: A bulleted list of what the Quant actually calculated or checked.
3.  `quant_conclusion`: The final "signal" and "reasoning" produced by the Quant.
4.  `anomalies`: Any errors, missing data, or strange behavior observed.

**CONSTRAINTS:**
- **Be Concise:** The Manager pays for every token. Strip out code boilerplate. Focus on the *results*.
- **No Hallucination:** Only summarize what is actually in the logs.
-**Provide only the JSON output and nothing else**
"""

def summarize_quant_cycle(events: List[AgentEvent], model: str = "google/gemini-2.5-flash-lite") -> Dict[str, Any]:
    """
    Compresses a list of Quant events into a structured summary to save tokens.
    """
    if not events:
        return {"summary": "No events to summarize."}

    # formatted_logs = []
    # for e in events:
    #     content_preview = str(e.content)[:500] + "..." if len(str(e.content)) > 500 else str(e.content)
    #     formatted_logs.append(f"[{e.type.upper()}] {content_preview}")
    
    # Avoid truncating too much, but be mindful. 
    # For now, let's just dump them stringified.
    logs_str = "\n".join([f"[{e.type.upper()}] {e.content}" for e in events])
    
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": f"### RAW LOGS:\n{logs_str}"}
    ]
    
    try:
        response = get_completion(messages, model=model, response_format={"type": "json_object"})
        
        content = ""
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
            
            
        return json.loads(content)
        
    except Exception as e:
        return {
            "error": f"Summarization failed: {e}",
            "fallback_summary": logs_str[:1000] # Fallback to partial raw logs
        }

MEMORY_SYSTEM_PROMPT = """
You are the **Memory Manager** for an Investment Agent.
Your job is to read the conversation history of a trading cycle and update the "Short-Term Memory" state.

### **INPUT:**
You will receive the full conversation history (System instructions, User Prompt, Tool Calls, and Final Decision).

### **OUTPUT:**
You must produce a **SINGLE JSON OBJECT** matching this schema:
{
    "short_term_summary": "Concise narrative of what happened in this cycle. Focus on decisions made and new data discovered.",
    "active_hypotheses": ["List of theories currently being tested (e.g. 'BTC Golden Cross verified')"],
    "pending_orders": ["List of any orders placed but not confirmed filled (or 'None')"],
    "next_steps": "What should the agent check next time?"
}

**CONSTRAINTS:**
- Be highly concise.
- Focus on the *Delta* (what changed).
"""

def generate_cycle_memory(history: List[Dict[str, Any]], model: str = "google/gemini-2.5-flash-lite") -> Dict[str, Any]:
    """
    Compresses the cycle history into an AgentMemory object using a cheap model.
    """
    # Convert history to string for the lightweight model (simplification)
    # Ideally we pass messages directly, but cleaning them helps the lite model focus.
    clean_history = ""
    for msg in history:
        # Handle both dict and Pydantic objects (ChatCompletionMessage)
        if hasattr(msg, 'role'):
            role = msg.role
        else:
            role = msg.get('role', 'unknown')
            
        if hasattr(msg, 'content'):
            content = msg.content
        else:
            content = msg.get('content', '')
            
        if role == 'tool':
            # Truncate tool outputs for memory generation
            content_str = str(content)
            content = f"[Tool Result] {content_str[:200]}..." 
        
        clean_history += f"[{str(role).upper()}]: {content}\n"
        
    messages = [
        {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
        {"role": "user", "content": f"### CONVERSATION HISTORY:\n{clean_history}"}
    ]
    
    try:
        response = get_completion(messages, model=model, response_format={"type": "json_object"})
        
        content = ""
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
            
        return json.loads(content)
    except Exception as e:
        return {
            "short_term_summary": f"Memory generation failed: {e}",
            "active_hypotheses": [],
            "pending_orders": [],
            "next_steps": "Retry analysis."
        }
