from agent.graph_state import AgentState
from agent.graph_nodes import (
    node_scan, 
    node_plan, 
    node_deciding, 
    node_quant, 
    node_validate_quant, 
    node_validate_decision, 
    node_execute, 
    node_memorize
)

def run_agent_graph(instruction: str, verbose: bool = True):
    """
    Main entry point for the Graph-based Agent.
    """
    import uuid

    # 1. Initialize State
    state: AgentState = {
        "instruction": instruction,
        "messages": [
            {"role": "user", "content": instruction}
        ],
        "market_data": None,
        "plan": None,
        "quant_report": None,
        "decision": None,
        "memory": None,
        "current_node": "SCANNING",
        "error": None,
        "retry_count": 0,
        "verbose": verbose,
        "run_id": str(uuid.uuid4()),
        "session_id": None,
        "cycle_id": None
    }
    
    # 2. State Machine Loop
    MAX_STEPS = 20
    step = 0
    
    # Routing Table
    NODE_MAP = {
        "SCANNING": node_scan,
        "PLANNING": node_plan,
        "DECIDING": node_deciding,
        "ANALYZING": node_quant,
        "VALIDATING_QUANT": node_validate_quant,
        "VALIDATING_DECISION": node_validate_decision,
        "EXECUTING": node_execute,
        "MEMORIZING": node_memorize
    }
    
    while state['current_node'] != "END" and step < MAX_STEPS:
        current_node_name = state['current_node']
        step += 1
        
        handler = NODE_MAP.get(current_node_name)
        if not handler:
            print(f"Error: Unknown node {current_node_name}")
            break
            
        # Execute Node logic
        state = handler(state)
        
    return state

if __name__ == "__main__":
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Check BTC status."
    run_agent_graph(prompt)
