"""Integration-ish test for OpenRouter helper with DeepSeek.

Covers:
- tool calling (fake temperature_search)
- structured output for TradeProposal schema
- multi-turn conversation memory

Run:
  python tests/test_openrouter.py

Requires:
  OPENROUTER_API_KEY in environment/.env
  pip install openrouter python-dotenv
"""

import json
import os
import re
from pathlib import Path
import importlib.util
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).resolve().parent
HELPER_PATH = HERE.parent / "Utils" / "openrouter.py"

spec = importlib.util.spec_from_file_location("openrouter_helper", HELPER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load helper at {HELPER_PATH}")
openrouter_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openrouter_helper)  # type: ignore[arg-type]

chat_completion_raw = openrouter_helper.chat_completion_raw
chat_completion = openrouter_helper.chat_completion
Conversation = openrouter_helper.Conversation


def fake_temperature_search(city: str) -> str:
    # Simple stub; in real app this would call Tavily or a weather API.
    temps = {"paris": "8C", "london": "6C", "new york": "4C"}
    return temps.get(city.lower(), "unknown")


def test_tool_call(model: str) -> None:
    print("== Tool calling test ==")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "temperature_search",
                "description": "Get current temperature for a city (fake tool for testing).",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]

    messages = [
        {
            "role": "user",
            "content": "What is the temperature in Paris right now?",
        }
    ]

    res = chat_completion_raw(
        messages=messages,
        model=model,
        tools=tools,
        tool_choice="auto",
        temperature=0.0,
    )

    # Show intermediate tool-call reasoning
    msg = res.choices[0].message
    print("Assistant raw content:", msg.content)
    tool_calls = getattr(msg, "tool_calls", None) or []
    print("Tool calls from model:")
    for tc in tool_calls:
        print(f"- id={tc.id} name={tc.function.name} args={tc.function.arguments}")


    if not tool_calls:
        print("Model did not call tool; assistant said:")
        print(msg.content)
        return

    # Execute first tool call
    call = tool_calls[0]
    fn = call.function
    args = json.loads(fn.arguments or "{}")
    print("Parsed tool args:", args)
    city = args.get("city", "Paris")
    temp = fake_temperature_search(city)
    print(f"Tool result temperature_search({city}) -> {temp}")

    followup = messages + [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": fn.name, "arguments": fn.arguments},
                }
            ],
            "content": None,
        },
        {"role": "tool", "name": fn.name, "tool_call_id": call.id, "content": temp},
    ]

    final_text = chat_completion(
        messages=followup,
        model=model,
        tools=tools,
        tool_choice="auto",
        temperature=0.0,
    )
    print(final_text)



def test_structured_output(model: str) -> None:
    print("\n== Structured output test (TradeProposal) ==")

    trade_proposal_schema = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "timestamp": {"type": "string"},
            "agent_id": {"type": "string"},
            "role": {"type": "string", "enum": ["macro", "onchain", "technical", "structure"]},
            "action": {"type": "string", "enum": ["buy", "sell", "none"]},
            "symbol": {"type": "string", "enum": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "NONE"]},
            "entry_type": {"type": "string", "enum": ["market", "limit"]},
            "entry_price": {"type": "number", "minimum": 0},
            "stop_price": {"type": "number", "minimum": 0},
            "targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "price": {"type": "number", "minimum": 0},
                        "size_pct": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["price", "size_pct"],
                    "additionalProperties": False,
                },
            },
            "size_notional_usd": {"type": "number", "minimum": 0},
            "risk_pct_of_budget": {"type": "number", "minimum": 0, "maximum": 1},
            "leverage": {"type": "number", "minimum": 1, "maximum": 10},
            "time_horizon_hours": {"type": "number", "minimum": 0, "maximum": 168},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "regime": {"type": "string", "enum": ["trend_up", "trend_down", "range", "high_vol", "risk_on", "risk_off"]},
            "hypothesis": {"type": "string", "minLength": 1, "maxLength": 300},
            "invalidation": {"type": "string", "minLength": 1, "maxLength": 120},
            "rationale": {"type": "array", "items": {"type": "string"}},
            "references": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^(snapshot|news|onchain|trade_proposals|manager_decisions):[A-Za-z0-9_-]+$",
                },
            },
            "no_trade_reason": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": [
            "run_id",
            "timestamp",
            "agent_id",
            "role",
            "action",
            "symbol",
            "entry_type",
            "entry_price",
            "stop_price",
            "targets",
            "size_notional_usd",
            "risk_pct_of_budget",
            "leverage",
            "time_horizon_hours",
            "confidence",
            "regime",
            "hypothesis",
            "invalidation",
            "rationale",
            "references",
            "no_trade_reason",
        ],
        "additionalProperties": False,
    }

    messages = [
        {
            "role": "user",
            "content": (
                "You are the TECHNICAL trader. Produce a TradeProposal JSON that STRICTLY matches the schema. "
                "Assume no clear edge: set action to none, symbol to BTCUSDT, entry_type market, all prices 0, targets empty. "
                "Use role=technical, regime=range, invalidation=none, and references as IDs like snapshot:abc123."
            ),
        }
    ]

    text = chat_completion(
        messages=messages,
        model=model,
        output_schema=trade_proposal_schema,
        schema_name="TradeProposal",
        strict_json=True,
        temperature=0.0,
        max_tokens=400,
    )

    obj = json.loads(text)

    # Local strict adherence checks
    assert obj["role"] in {"macro", "onchain", "technical", "structure"}
    assert obj["symbol"] in {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "NONE"}
    assert obj["regime"] in {"trend_up", "trend_down", "range", "high_vol", "risk_on", "risk_off"}
    assert 0 <= obj["confidence"] <= 1
    assert obj["action"] == "none"
    assert obj["entry_price"] == 0 and obj["stop_price"] == 0
    assert obj["targets"] == []
    pat = re.compile(r"^(snapshot|news|onchain|trade_proposals|manager_decisions):[A-Za-z0-9_-]+$")
    assert all(pat.match(r) for r in obj["references"])

    print("Schema adherence: OK")
    print(json.dumps(obj, indent=2))

def test_multi_turn(model: str) -> None:
    print("\n== Multi-turn conversation test ==")

    convo = Conversation(system="You are a helpful assistant.")

    convo.add_user("Hi, my name is Bob.")
    print(convo.send(model=model, temperature=0.2,))

    convo.add_user("What is 2 + 2?")
    print(convo.send(model=model, temperature=0.0,))

    convo.add_user("Tell me one fun fact about space.")
    print(convo.send(model=model, temperature=0.7,))

    convo.add_user("What is my name?")
    answer = convo.send(model=model, temperature=0.0,)
    print(answer)


def main() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("Set OPENROUTER_API_KEY first (in shell or .env).")

    model = "deepseek/deepseek-v3.2"

    test_tool_call(model)
    #test_structured_output(model)
    #test_multi_turn(model)


if __name__ == "__main__":
    main()
