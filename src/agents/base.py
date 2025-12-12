"""Base REACT-style trader agent.

Uses our OpenRouter wrapper and in-house tool registry.
No external agent frameworks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from Utils.openrouter import chat_completion_raw
from src.agents.schemas import (
    DecisionType,
    OrderType,
    Side,
    Timeframe,
    TradeAction,
    TradeProposal,
    export_json_schema,
)
from src.agents.tools import ToolContext, build_openrouter_tools, build_tool_dispatch
from src.data.mongo import jsonify


@dataclass
class BaseTraderConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 6
    max_tool_calls: int = 6
    tool_choice: str = "auto"
    final_retry_on_invalid_json: bool = True


class BaseTrader:
    """Common REACT loop for trader agents."""

    def _strip_code_fences(self, text: str) -> str:
        s = text.strip()
        # Remove ```json ... ``` or ``` ... ``` wrappers if present.
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", s)
            s = re.sub(r"\n?```$", "", s)
        return s.strip()

    def _parse_trade_proposal(self, content: str) -> TradeProposal:
        """Parse model output into TradeProposal with best-effort JSON decoding."""
        raw = content or ""
        s = self._strip_code_fences(raw)

        # Fast path: strict JSON
        try:
            return TradeProposal.model_validate_json(s)
        except Exception:
            pass

        # Fallback: tolerate unescaped control characters in strings.
        # This still enforces schema via Pydantic model validation.
        try:
            obj = json.loads(s, strict=False)
            return TradeProposal.model_validate(obj)
        except Exception as e:
            snippet = s[:500].replace("\n", "\\n")
            raise RuntimeError(
                f"Failed to parse TradeProposal JSON. Snippet: {snippet}"
            ) from e

    def _schema_summary(self) -> str:
        side_vals = [s.value for s in Side]
        action_vals = [a.value for a in TradeAction]
        order_vals = [o.value for o in OrderType]
        tf_vals = [t.value for t in Timeframe]
        decision_vals = [d.value for d in DecisionType]

        return (
            "TradeProposal JSON schema summary (STRICT):\n"
            "Top-level object keys:\n"
            "- agent_id: string (your id)\n"
            "- run_id: string|null\n"
            "- cycle_id: string|null\n"
            "- timestamp: RFC3339/ISO datetime string (UTC)\n"
            "- trades: array of TradeIdea objects (can be empty if no-trade)\n"
            "- notes: string|null\n\n"
            "TradeIdea object keys (no extras allowed):\n"
            "- symbol: string like BTCUSDT\n"
            f"- side: one of {side_vals}\n"
            f"- action: one of {action_vals}\n"
            "- size_usdt: number > 0\n"
            "- leverage: number > 0 | null\n"
            f"- order_type: one of {order_vals}\n"
            "- limit_price: number > 0 | null (REQUIRED if order_type=limit; OMIT if market)\n"
            "- stop_loss: number > 0 | null\n"
            "- take_profit: number > 0 | null\n"
            f"- time_horizon: one of {tf_vals} | null\n"
            "- confidence: number between 0 and 1\n"
            "- rationale: string (non-empty)\n"
            "- invalidation: string|null\n"
            "- tags: array of strings (optional)\n\n"
            f"ManagerDecision (for awareness): decision values {decision_vals}.\n"
            "Rules:\n"
            "- Do NOT add any keys beyond those listed.\n"
            "- Use null for unknown optional fields.\n"
        )

    def _build_finalization_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sanitize/condense transcript for schema-only finalization.

        Some providers/models behave unpredictably if we send prior tool-role messages
        into a later call where tools are not provided. To keep the finalization call
        reliable, we:
        - keep the original system prompt
        - keep the original user prompt content
        - append tool outputs as plain text (authoritative transcript)
        - omit tool-role messages and any tool_call metadata
        """
        if not messages:
            raise ValueError("messages cannot be empty")

        system_msg = {"role": "system", "content": messages[0].get("content", "")}

        user_content = ""
        if len(messages) > 1 and messages[1].get("role") == "user":
            user_content = messages[1].get("content") or ""

        tool_lines: List[str] = []
        for m in messages:
            if m.get("role") != "tool":
                continue
            name = m.get("name") or "tool"
            content = m.get("content") or ""
            tool_lines.append(f"- {name}: {content}")

        assistant_notes: List[str] = []
        for m in messages[2:]:
            if m.get("role") != "assistant":
                continue
            if m.get("tool_calls"):
                continue
            c = (m.get("content") or "").strip()
            if c:
                assistant_notes.append(c)

        final_parts: List[str] = [user_content]
        if tool_lines:
            final_parts.append("\nTool results (authoritative):")
            final_parts.append("\n".join(tool_lines))
        if assistant_notes:
            final_parts.append("\nPrior assistant notes (may be incomplete):")
            final_parts.append("\n\n".join(assistant_notes))

        final_system = {
            "role": "system",
            "content": (
                "FINALIZATION STEP:\n"
                "- Tools are NOT available in this step.\n"
                "- Return ONLY the TradeProposal JSON object.\n"
                "- Do NOT return an array; the top-level value MUST be a JSON object.\n"
            ),
        }
        final_user = {"role": "user", "content": "\n".join([p for p in final_parts if p])}
        return [system_msg, final_system, final_user]

    def __init__(
        self,
        *,
        agent_id: str,
        role_prompt: str,
        config: BaseTraderConfig,
        tools_context: Optional[ToolContext] = None,
    ):
        self.agent_id = agent_id
        self.role_prompt = role_prompt.strip()
        self.config = config
        self.tools_context = tools_context or ToolContext()

        self._tool_defs = build_openrouter_tools(self.tools_context)
        self._tool_dispatch = build_tool_dispatch(self.tools_context)

        # Trace/debug info from last decide() call
        self.last_messages: List[Dict[str, Any]] = []
        self.last_tool_calls: List[Dict[str, Any]] = []

    def build_messages(
        self,
        *,
        market_brief: Dict[str, Any],
        firm_state: Optional[Dict[str, Any]] = None,
        position_summary: Optional[Dict[str, Any]] = None,
        memory_snippet: Optional[str] = None,
        extra_instructions: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Build system + user messages for the trader."""
        system = {
            "role": "system",
            "content": (
                f"You are trader agent '{self.agent_id}', operating as a top-tier institutional "
                "crypto derivatives PM/trader (Goldman/Citadel standard).\n\n"
                f"Mandate (non-negotiable):\n{self.role_prompt}\n\n"
                "Operating principles:\n"
                "- Be rigorous, probabilistic, and risk-first. Prefer no-trade to low-edge trades.\n"
                "- Separate observations → hypotheses → trades. State clear invalidation conditions.\n"
                "- Never anchor on a single signal; look for confluence across regimes, trend, vol, "
                "flows (funding/OI), and catalysts.\n"
                "- Maintain strategy-neutral language; do not invent rules beyond your mandate.\n\n"
                "REACT process (follow in order):\n"
                "1) Parse the Market Brief and firm/position context.\n"
                "2) Identify current trend/vol regime per symbol and broader breadth/correlation context.\n"
                "3) Form 1–3 candidate theses with explicit catalysts and invalidations.\n"
                "4) Use tools to resolve uncertainty or fetch missing facts (prices, indicators, news, memory, positions).\n"
                "5) After each tool result, update your view; discard weak theses.\n"
                "6) If a trade has edge, size it conservatively within budget; define entry type, "
                "risk (stop), reward (targets/TP), leverage, horizon, and confidence.\n"
                "7) If no clear edge, output no-trade with an explanatory note.\n\n"
                "Tool policy:\n"
                "- Tools are authoritative. Do not fabricate data that a tool could provide.\n"
                "- You may call multiple tools sequentially. If instructed to call specific tools, comply.\n"
                "- Do not call any tool not in the registry.\n\n"
            "Output contract:\n"
            "- Final answer MUST be ONLY valid JSON matching TradeProposal schema.\n"
            "- No extra keys, no prose outside JSON.\n"
            "- All numbers must be realistic and grounded in tool/context; if unsure, abstain.\n"
            "- You MUST include a short reasoning summary in top-level 'notes' for EVERY response.\n"
            "- If you choose no-trade (empty trades), 'notes' MUST explain why no trade.\n"
            "\n"
            + self._schema_summary()
            ),
        }

        user_parts: List[str] = []
        user_parts.append("Market Brief (strategy-neutral):")
        user_parts.append(
            json.dumps(jsonify(market_brief), ensure_ascii=False, default=str)
        )
        if firm_state is not None:
            user_parts.append("\nFirm state:")
            user_parts.append(
                json.dumps(jsonify(firm_state), ensure_ascii=False, default=str)
            )
        if position_summary is not None:
            user_parts.append("\nYour positions:")
            user_parts.append(
                json.dumps(jsonify(position_summary), ensure_ascii=False, default=str)
            )
        if memory_snippet:
            user_parts.append("\nRelevant memory:")
            user_parts.append(memory_snippet)
        if extra_instructions:
            user_parts.append("\nExtra instructions:")
            user_parts.append(extra_instructions)

        user = {"role": "user", "content": "\n".join(user_parts)}
        return [system, user]

    def _extract_tool_calls(self, msg: Any) -> List[Any]:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            return list(tool_calls)
        # Some models may return tool calls in message dict.
        if isinstance(msg, dict) and msg.get("tool_calls"):
            return list(msg["tool_calls"])
        return []

    def _tool_call_to_dict(self, tc: Any) -> Tuple[str, str, Dict[str, Any]]:
        # SDK objects have .id, .function.name, .function.arguments
        try:
            tc_id = tc.id
            fn_name = tc.function.name
            fn_args = tc.function.arguments
        except Exception:
            tc_id = tc.get("id")
            fn = tc.get("function", {})
            fn_name = fn.get("name")
            fn_args = fn.get("arguments")
        args_obj: Dict[str, Any] = {}
        if isinstance(fn_args, str) and fn_args.strip():
            try:
                args_obj = json.loads(fn_args)
            except Exception:
                args_obj = {}
        return str(tc_id), str(fn_name), args_obj

    async def decide(
        self,
        *,
        market_brief: Dict[str, Any],
        firm_state: Optional[Dict[str, Any]] = None,
        position_summary: Optional[Dict[str, Any]] = None,
        memory_snippet: Optional[str] = None,
        extra_instructions: Optional[str] = None,
    ) -> TradeProposal:
        """Run REACT loop and return validated TradeProposal."""
        messages = self.build_messages(
            market_brief=market_brief,
            firm_state=firm_state,
            position_summary=position_summary,
            memory_snippet=memory_snippet,
            extra_instructions=extra_instructions,
        )

        self.last_messages = list(messages)
        self.last_tool_calls = []

        schema = export_json_schema(TradeProposal)

        # Phase A (tool turns): allow the model to call tools freely.
        # Do NOT enforce structured output here; many models struggle to combine
        # tool calling and strict json_schema in the same turn.
        tool_calls_used = 0
        for _turn in range(self.config.max_tool_turns):
            if tool_calls_used >= self.config.max_tool_calls:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Tool call budget reached ({self.config.max_tool_calls}). "
                            "Stop calling tools and proceed to final TradeProposal JSON."
                        ),
                    }
                )
                self.last_messages = list(messages)
                break

            res = chat_completion_raw(
                messages=messages,
                model=self.config.model,
                tools=self._tool_defs,
                tool_choice=self.config.tool_choice,
                temperature=self.config.temperature,
            )

            msg = res.choices[0].message
            tool_calls = self._extract_tool_calls(msg)
            if not tool_calls:
                content = getattr(msg, "content", None) or msg.get("content")  # type: ignore[union-attr]
                # Keep any assistant analysis in the transcript, then finalize in Phase B.
                if content:
                    messages.append({"role": "assistant", "content": content})
                    self.last_messages = list(messages)
                break

            remaining = max(0, self.config.max_tool_calls - tool_calls_used)
            if remaining <= 0:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Tool call budget reached ({self.config.max_tool_calls}). "
                            "Stop calling tools and proceed to final TradeProposal JSON."
                        ),
                    }
                )
                self.last_messages = list(messages)
                break

            if len(tool_calls) > remaining:
                tool_calls = tool_calls[:remaining]

            # Append assistant tool-call message
            assistant_tool_msg = {
                "role": "assistant",
                "tool_calls": [],
                "content": getattr(msg, "content", None),
            }

            for tc in tool_calls:
                tc_id, fn_name, args = self._tool_call_to_dict(tc)
                assistant_tool_msg["tool_calls"].append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": json.dumps(args)},
                    }
                )
                self.last_tool_calls.append({"id": tc_id, "name": fn_name, "args": args})
                tool_calls_used += 1

            messages.append(assistant_tool_msg)
            self.last_messages = list(messages)

            # Execute tools sequentially, feed results back.
            for tc in assistant_tool_msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                args = json.loads(tc["function"].get("arguments") or "{}")
                tool_fn = self._tool_dispatch.get(fn_name)
                if tool_fn is None:
                    tool_out = {"error": f"unknown tool: {fn_name}"}
                else:
                    try:
                        tool_out = await tool_fn(**args)  # type: ignore[misc]
                    except Exception as e:
                        tool_out = {"error": str(e)}

                messages.append(
                    {
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            jsonify(tool_out), ensure_ascii=False, default=str
                        ),
                    }
                )
                self.last_messages = list(messages)

            if tool_calls_used >= self.config.max_tool_calls:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Tool call budget reached ({self.config.max_tool_calls}). "
                            "Stop calling tools and proceed to final TradeProposal JSON."
                        ),
                    }
                )
                self.last_messages = list(messages)
                break

        final_messages = self._build_finalization_messages(messages)

        # If we get here, tool loop exceeded max turns. Force a final answer.
        # Phase B (finalization): enforce strict TradeProposal schema (no tools).
        def _finalize() -> str:
            final_res = chat_completion_raw(
                messages=final_messages,
                model=self.config.model,
                output_schema=schema,
                schema_name="TradeProposal",
                strict_json=True,
                temperature=0.0,
            )
            final_msg = final_res.choices[0].message
            return getattr(final_msg, "content", None) or final_msg.get("content")  # type: ignore[union-attr]

        def _finalize_json_object_with_schema_hint(err_text: str) -> str:
            schema_hint = json.dumps(schema, ensure_ascii=False)
            hint_msgs = final_messages + [
                {
                    "role": "system",
                    "content": (
                        "FINAL RESPONSE RETRY (JSON MODE):\n"
                        "Return ONLY a valid JSON object matching this schema. No markdown, no prose.\n"
                        "The top-level value MUST be a JSON object (start with '{', end with '}'), not an array.\n"
                        f"Schema: {schema_hint}\n"
                        f"Previous error: {err_text}"
                    ),
                }
            ]
            res = chat_completion_raw(
                messages=hint_msgs,
                model=self.config.model,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            msg = res.choices[0].message
            return getattr(msg, "content", None) or msg.get("content")  # type: ignore[union-attr]

        def _enforce_reasoning(p: TradeProposal) -> None:
            notes = (p.notes or "").strip() if p.notes is not None else ""
            if not notes:
                raise ValueError("Missing required top-level notes reasoning.")
            if len(notes) < 10:
                raise ValueError("Top-level notes reasoning too short.")

        last_err: Optional[Exception] = None
        final_content = ""
        for attempt in range(2 if self.config.final_retry_on_invalid_json else 1):
            try:
                if attempt == 0:
                    final_content = _finalize()
                else:
                    final_content = _finalize_json_object_with_schema_hint(str(last_err or "invalid output"))

                self.last_messages = list(final_messages) + [
                    {"role": "assistant", "content": final_content}
                ]
                proposal = self._parse_trade_proposal(final_content)
                _enforce_reasoning(proposal)
                return proposal
            except Exception as e:
                last_err = e
                if attempt == 0 and self.config.final_retry_on_invalid_json:
                    # Keep the tool transcript stable; retry uses the json_object path with a schema hint.
                    continue
                break

        raise RuntimeError(
            f"Final TradeProposal output invalid after retries. Last output snippet: {final_content[:300]}"
        ) from last_err


__all__ = ["BaseTrader", "BaseTraderConfig"]
