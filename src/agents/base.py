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
from src.data.mongo import jsonify
from src.agents.schemas import (
    DecisionType,
    OrderType,
    Side,
    Timeframe,
    TradeAction,
    TradeProposal,
    export_json_schema,
)
from src.agents.tools import (
    ToolContext,
    build_openrouter_tools,
    build_tool_dispatch,
    build_tool_specs,
 )

try:  # pragma: no cover
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None


@dataclass
class BaseTraderConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 6
    max_tool_calls: int = 6
    tool_choice: str = "auto"
    final_retry_on_invalid_json: bool = True
    allowed_tools: Optional[List[str]] = None
    # Per-tool-call output budget to prevent provider context overflow.
    # This is an approximation (cl100k_base) but works as a safety belt.
    max_tool_output_tokens: int = 8000


class BaseTrader:
    """Common REACT loop for trader agents."""

    def _redact_identity_fields(self, text: str) -> str:
        """Redact model-echoed identity fields from logs/errors for clarity."""
        s = text or ""
        s = re.sub(r'("run_id"\s*:\s*)"[^"]*"', r'\1"<redacted>"', s)
        s = re.sub(r'("cycle_id"\s*:\s*)"[^"]*"', r'\1"<redacted>"', s)
        s = re.sub(r'("timestamp"\s*:\s*)"[^"]*"', r'\1"<redacted>"', s)
        return s

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if tiktoken is None:
            # Fallback heuristic: ~4 chars/token for typical English/JSON.
            return max(1, len(text) // 4)
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def _shrink_json_payload(self, obj: Any, *, max_tokens: int) -> Tuple[Any, bool]:
        """Deterministically shrink a JSON-serializable object until under token budget.

        Strategy:
        - Prefer trimming list lengths (keep most recent tail items).
        - Iteratively halve the max list length until fit.
        - If still too large, replace oversized lists with an omitted marker.
        """

        def _trim_lists(v: Any, *, max_list_len: int) -> Any:
            if isinstance(v, list):
                if len(v) <= max_list_len:
                    return [_trim_lists(x, max_list_len=max_list_len) for x in v]
                # Keep the tail (most recent bars/items) by default.
                trimmed = v[-max_list_len:]
                return [_trim_lists(x, max_list_len=max_list_len) for x in trimmed]
            if isinstance(v, dict):
                return {k: _trim_lists(val, max_list_len=max_list_len) for k, val in v.items()}
            return v

        payload = jsonify(obj)
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        if self._count_tokens(raw) <= max_tokens:
            return payload, False

        truncated = False
        max_list_len = 200
        for _ in range(8):
            truncated_payload = _trim_lists(payload, max_list_len=max_list_len)
            raw2 = json.dumps(truncated_payload, ensure_ascii=False, default=str)
            if self._count_tokens(raw2) <= max_tokens:
                return truncated_payload, True
            truncated = True
            max_list_len = max(5, max_list_len // 2)

        # Last resort: aggressively omit all long lists.
        def _omit_big_lists(v: Any) -> Any:
            if isinstance(v, list) and len(v) > 5:
                return {
                    "_omitted": True,
                    "reason": "payload_too_large",
                    "kept_tail": v[-5:],
                    "original_len": len(v),
                }
            if isinstance(v, list):
                return [_omit_big_lists(x) for x in v]
            if isinstance(v, dict):
                return {k: _omit_big_lists(val) for k, val in v.items()}
            return v

        final_payload = _omit_big_lists(payload)
        return final_payload, True or truncated

    def _sanitize_trade_proposal_dict(self, obj: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Deterministically repair malformed proposals into a safe, valid shape.

        If any trade idea is invalid (e.g. non-numeric leverage, size_usdt<=0),
        drop/normalize it so the proposal can still validate (often as a no-trade).
        """
        repaired = False
        out: Dict[str, Any] = dict(obj or {})

        trades = out.get("trades")
        if not isinstance(trades, list):
            out["trades"] = []
            return out, True

        def _to_float_or_none(x: Any) -> Optional[float]:
            if x is None:
                return None
            try:
                return float(x)
            except Exception:
                return None

        cleaned: List[Dict[str, Any]] = []
        for t in trades:
            if not isinstance(t, dict):
                repaired = True
                continue

            trade = dict(t)
            # "hold" is not an actionable trade in this protocol. No-trade is expressed as trades=[].
            action = trade.get("action")
            if action == "hold":
                repaired = True
                continue

            size = _to_float_or_none(trade.get("size_usdt"))
            if size is None or size <= 0:
                repaired = True
                continue
            trade["size_usdt"] = float(size)

            lev = _to_float_or_none(trade.get("leverage"))
            if lev is None or lev <= 0:
                if trade.get("leverage") is not None:
                    repaired = True
                trade["leverage"] = None
            else:
                trade["leverage"] = float(lev)

            order_type = trade.get("order_type")
            if order_type == OrderType.market.value:
                if trade.get("limit_price") is not None:
                    repaired = True
                    trade["limit_price"] = None
            elif order_type == OrderType.limit.value:
                lp = _to_float_or_none(trade.get("limit_price"))
                if lp is None or lp <= 0:
                    repaired = True
                    continue
                trade["limit_price"] = float(lp)

            sl = _to_float_or_none(trade.get("stop_loss"))
            trade["stop_loss"] = float(sl) if sl is not None and sl > 0 else None
            tp = _to_float_or_none(trade.get("take_profit"))
            trade["take_profit"] = float(tp) if tp is not None and tp > 0 else None

            cleaned.append(trade)

        if repaired:
            out["trades"] = cleaned
            notes = (out.get("notes") or "").strip()
            suffix = "[SYSTEM: invalid trades removed/normalized]"
            if suffix not in notes:
                out["notes"] = (notes + " " + suffix).strip() if notes else suffix

        return out, repaired

    def _tool_signatures_text(self) -> str:
        specs = build_tool_specs(
            self.tools_context,
            allowed_tools=self.config.allowed_tools,
        )
        if not specs:
            return "Tools available: none."

        lines: List[str] = ["Tools available (JSON in/out):"]
        for s in specs:
            params = s.parameters or {}
            props = (params.get("properties") or {}) if isinstance(params, dict) else {}
            required = (params.get("required") or []) if isinstance(params, dict) else []
            req_txt = f"required={required}" if required else "required=[]"
            prop_keys = list(props.keys()) if isinstance(props, dict) else []
            lines.append(f"- {s.name}: {s.description}")
            lines.append(f"  input: object with keys={prop_keys}; {req_txt}")
            lines.append("  output: JSON object (tool-specific payload)")
        return "\n".join(lines)

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
            if isinstance(obj, dict):
                repaired_obj, _ = self._sanitize_trade_proposal_dict(obj)
                return TradeProposal.model_validate(repaired_obj)
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
            "- If you want to HOLD / do nothing: return trades=[] and explain in notes. Do NOT output action='hold'.\n"
            "- Never use size_usdt=0; if no trade, use trades=[].\n"
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

        self._tool_defs = build_openrouter_tools(
            self.tools_context, allowed_tools=self.config.allowed_tools
        )
        self._tool_dispatch = build_tool_dispatch(
            self.tools_context, allowed_tools=self.config.allowed_tools
        )

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
                f"- You can make up to {self.config.max_tool_calls} tool calls total across up to "
                f"{self.config.max_tool_turns} tool turns. Use this budget to gather facts before deciding.\n"
                "- Do not call any tool not in the registry.\n\n"
                + self._tool_signatures_text()
                + "\n\n"
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

                payload, truncated = self._shrink_json_payload(
                    tool_out, max_tokens=self.config.max_tool_output_tokens
                )
                if truncated:
                    payload = {
                        "truncated": True,
                        "note": (
                            "Tool output was truncated to stay within context limits. "
                            "Next time, make more focused tool calls (fewer symbols/timeframes, "
                            "smaller lookback_bars/lookback_minutes, or request a compact detail level)."
                        ),
                        "data": payload,
                    }

                messages.append(
                    {
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            jsonify(payload),
                            ensure_ascii=False,
                            default=str,
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
            "Final TradeProposal output invalid after retries. Last output snippet: "
            f"{self._redact_identity_fields(final_content[:300])}"
        ) from last_err


__all__ = ["BaseTrader", "BaseTraderConfig"]
