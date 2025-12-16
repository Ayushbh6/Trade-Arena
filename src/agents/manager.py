"""Manager / CIO agent (Plan/Act style).

Aggregates trader proposals + compliance reports and outputs a
schema-valid ManagerDecision. Manager must veto hard violations and may resize
soft violations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from Utils.openrouter import chat_completion_raw
from src.agents.schemas import DecisionItem, DecisionType, ManagerDecision, export_json_schema
from src.agents.tools import ToolContext, build_openrouter_tools, build_tool_dispatch
from src.data.mongo import jsonify
from src.risk.schemas import ComplianceReport


MANAGER_ROLE_PROMPT = """
# Role: Manager / CIO (Crypto Perps Desk)

You are the **Manager/CIO** supervising multiple trader agents trading USDT-margined perpetual futures.

Your job is to maximize **firm-level risk-adjusted returns**, while enforcing **hard safety invariants**.

---

## Inputs You Receive

- One `TradeProposal` per trader for the current cycle
- A deterministic `ComplianceReport` per proposal (risk engine output)
- Firm state + risk limits + agent budgets
- Current `trust_scores` per agent (allocator signal; higher = more trusted)
- Current positions summaries (if provided)

---

## Non-Negotiable Governance Rules

1. **Hard violations MUST be vetoed.** You may not override them.
2. **Soft violations MAY be resized.** Use resize suggestions when available.
3. Prefer **capital preservation** over forced trades. If edge is unclear, `defer`.
4. Avoid internal conflicts: do not approve opposing trades on the same symbol unless explicitly justified.

---

## Plan/Act Checklist (Follow Internally)

1) Scan compliance reports and veto any trade with hard violations.
2) For remaining trades, apply soft resize suggestions (size multiplier/cap/leverage).
3) Sanity check portfolio/firm exposure and avoid overconcentration.
4) Output the final `ManagerDecision` JSON.

---

## Output Contract (STRICT)

- Final response MUST be ONLY a `ManagerDecision` JSON object matching schema.
- No markdown, no extra text, no code fences.
- You MUST include a short reasoning summary in top-level `notes` (even if no decisions).
- `trust_deltas` is OPTIONAL and informational only (weekly allocator may consider it).
"""


@dataclass
class ManagerConfig:
    model: str
    temperature: float = 0.0
    max_tool_turns: int = 4
    max_tool_calls: int = 4
    tool_choice: str = "auto"
    final_retry_on_invalid_json: bool = True


class ManagerAgent:
    def __init__(
        self,
        *,
        manager_id: str = "manager",
        config: ManagerConfig,
        tools_context: Optional[ToolContext] = None,
    ):
        self.manager_id = manager_id
        self.config = config
        self.tools_context = tools_context or ToolContext()

        self._tool_defs = build_openrouter_tools(self.tools_context)
        self._tool_dispatch = build_tool_dispatch(self.tools_context)

        self.last_messages: List[Dict[str, Any]] = []
        self.last_tool_calls: List[Dict[str, Any]] = []

    def _strip_code_fences(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", s)
            s = re.sub(r"\n?```$", "", s)
        return s.strip()

    def _parse_manager_decision(self, content: str) -> ManagerDecision:
        s = self._strip_code_fences(content)
        try:
            return ManagerDecision.model_validate_json(s)
        except Exception:
            pass
        try:
            obj = json.loads(s, strict=False)
            return ManagerDecision.model_validate(obj)
        except Exception as e:
            snippet = s[:500].replace("\n", "\\n")
            raise RuntimeError(
                f"Failed to parse ManagerDecision JSON. Snippet: {snippet}"
            ) from e

    def _extract_tool_calls(self, msg: Any) -> List[Any]:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            return list(tool_calls)
        if isinstance(msg, dict) and msg.get("tool_calls"):
            return list(msg["tool_calls"])
        return []

    def _tool_call_to_dict(self, tc: Any) -> Tuple[str, str, Dict[str, Any]]:
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

    def _build_finalization_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
                "- Return ONLY the ManagerDecision JSON object.\n"
                "- Do NOT return an array; the top-level value MUST be a JSON object.\n"
            ),
        }
        final_user = {"role": "user", "content": "\n".join([p for p in final_parts if p])}
        return [system_msg, final_system, final_user]

    def _schema_summary(self) -> str:
        decision_vals = [d.value for d in DecisionType]
        return (
            "ManagerDecision JSON schema summary (STRICT):\n"
            "- manager_id: string\n"
            "- run_id: string|null\n"
            "- cycle_id: string|null\n"
            "- timestamp: RFC3339/ISO datetime string (UTC)\n"
            "- decisions: array of DecisionItem objects\n"
            "- notes: string|null (REQUIRED in practice; must explain decisions)\n\n"
            "- trust_deltas: array of TrustDelta objects (optional; may be empty)\n\n"
            "DecisionItem keys:\n"
            "- agent_id: string|null\n"
            "- trade_index: int|null\n"
            "- symbol: string\n"
            f"- decision: one of {decision_vals}\n"
            "- approved_size_usdt: number|null\n"
            "- approved_leverage: number|null\n"
            "- notes: string|null\n"
            "\nTrustDelta keys:\n"
            "- agent_id: string\n"
            "- delta: number in [-1, 1]\n"
            "- reason: string\n"
            "Rules:\n"
            "- Do NOT add any keys beyond those listed.\n"
        )

    async def _audit_tool_call(
        self,
        *,
        run_id: Optional[str],
        cycle_id: Optional[str],
        tool_name: str,
        args: Dict[str, Any],
        result: Any,
        error: Optional[str],
    ) -> None:
        if not run_id:
            return
        mongo = getattr(self.tools_context, "mongo", None)
        if mongo is None:
            return

        # Best-effort size guard for Mongo (16MB max doc).
        try:
            raw = json.dumps(jsonify(result), ensure_ascii=False, default=str)
            if len(raw) > 200_000:
                stored: Any = {
                    "_omitted": True,
                    "reason": "result_too_large_for_audit_event",
                    "char_len": len(raw),
                    "head": raw[:50_000],
                    "tail": raw[-50_000:],
                }
            else:
                stored = jsonify(result)
        except Exception:
            stored = {"_omitted": True, "reason": "json_dump_failed"}

        try:
            await mongo.log_audit_event(
                "tool_call",
                {
                    "cycle_id": cycle_id,
                    "tool": {"name": tool_name, "args": jsonify(args)},
                    "result": {"error": error, "data": stored},
                },
                run_id=run_id,
                agent_id=self.manager_id,
            )
        except Exception:
            return

    async def decide(
        self,
        *,
        proposals: List[Dict[str, Any]],
        compliance_reports: List[Dict[str, Any]],
        trust_scores: Optional[Dict[str, float]] = None,
        firm_state: Optional[Dict[str, Any]] = None,
        positions_by_agent: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        extra_instructions: Optional[str] = None,
    ) -> ManagerDecision:
        schema = export_json_schema(ManagerDecision)

        system = {
            "role": "system",
            "content": (
                f"You are manager agent '{self.manager_id}'.\n\n"
                f"{MANAGER_ROLE_PROMPT}\n\n"
                + self._schema_summary()
            ),
        }

        user_parts: List[str] = []
        if run_id:
            user_parts.append(f"run_id: {run_id}")
        if cycle_id:
            user_parts.append(f"cycle_id: {cycle_id}")

        user_parts.append("\nTrader proposals (authoritative):")
        user_parts.append(json.dumps(jsonify(proposals), ensure_ascii=False, default=str))

        user_parts.append("\nCompliance reports (authoritative):")
        user_parts.append(
            json.dumps(jsonify(compliance_reports), ensure_ascii=False, default=str)
        )

        if trust_scores is not None:
            user_parts.append("\nTrust scores (allocator signal; higher = more trusted):")
            user_parts.append(json.dumps(jsonify(trust_scores), ensure_ascii=False, default=str))

        if firm_state is not None:
            user_parts.append("\nFirm state:")
            user_parts.append(json.dumps(jsonify(firm_state), ensure_ascii=False, default=str))

        if positions_by_agent is not None:
            user_parts.append("\nPositions by agent:")
            user_parts.append(
                json.dumps(jsonify(positions_by_agent), ensure_ascii=False, default=str)
            )

        if extra_instructions:
            user_parts.append("\nExtra instructions:")
            user_parts.append(extra_instructions)

        messages: List[Dict[str, Any]] = [system, {"role": "user", "content": "\n".join(user_parts)}]
        self.last_messages = list(messages)
        self.last_tool_calls = []

        tool_calls_used = 0
        for _turn in range(self.config.max_tool_turns):
            if tool_calls_used >= self.config.max_tool_calls:
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
                if content:
                    messages.append({"role": "assistant", "content": content})
                break

            remaining = max(0, self.config.max_tool_calls - tool_calls_used)
            if remaining <= 0:
                break
            if len(tool_calls) > remaining:
                tool_calls = tool_calls[:remaining]

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

            for tc in assistant_tool_msg["tool_calls"]:
                fn = tc.get("function", {})
                fn_name = fn.get("name")
                args = {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}

                tool_fn = self._tool_dispatch.get(fn_name)
                error: Optional[str] = None
                if tool_fn is None:
                    error = f"unknown tool: {fn_name}"
                    tool_out = {"error": error}
                else:
                    try:
                        tool_out = await tool_fn(**args)  # type: ignore[misc]
                    except Exception as e:
                        error = str(e)
                        tool_out = {"error": error}

                await self._audit_tool_call(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    tool_name=str(fn_name),
                    args=args if isinstance(args, dict) else {},
                    result=tool_out,
                    error=error,
                )

                messages.append(
                    {
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": tc["id"],
                        "content": json.dumps(jsonify(tool_out), ensure_ascii=False, default=str),
                    }
                )

            self.last_messages = list(messages)

        final_messages = self._build_finalization_messages(messages)

        def _finalize_json_schema() -> str:
            res = chat_completion_raw(
                messages=final_messages,
                model=self.config.model,
                output_schema=schema,
                schema_name="ManagerDecision",
                strict_json=True,
                temperature=0.0,
            )
            msg = res.choices[0].message
            return getattr(msg, "content", None) or msg.get("content")  # type: ignore[union-attr]

        def _finalize_json_object(err_text: str) -> str:
            schema_hint = json.dumps(schema, ensure_ascii=False)
            retry_msgs = final_messages + [
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
                messages=retry_msgs,
                model=self.config.model,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            msg = res.choices[0].message
            return getattr(msg, "content", None) or msg.get("content")  # type: ignore[union-attr]

        def _enforce_notes(md: ManagerDecision) -> None:
            notes = (md.notes or "").strip()
            if not notes or len(notes) < 10:
                raise ValueError("Missing required manager notes reasoning.")

        last_err: Optional[Exception] = None
        final_content = ""
        for attempt in range(2 if self.config.final_retry_on_invalid_json else 1):
            try:
                if attempt == 0:
                    final_content = _finalize_json_schema()
                else:
                    final_content = _finalize_json_object(str(last_err or "invalid output"))

                self.last_messages = list(final_messages) + [
                    {"role": "assistant", "content": final_content}
                ]
                decision = self._parse_manager_decision(final_content)
                _enforce_notes(decision)

                # Ensure manager_id/run_id/cycle_id filled if provided.
                if decision.manager_id != self.manager_id:
                    decision.manager_id = self.manager_id  # type: ignore[misc]
                if run_id and decision.run_id is None:
                    decision.run_id = run_id  # type: ignore[misc]
                if cycle_id and decision.cycle_id is None:
                    decision.cycle_id = cycle_id  # type: ignore[misc]
                return decision
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(
            f"Final ManagerDecision output invalid after retries. Last output snippet: {final_content[:300]}"
        ) from last_err


__all__ = ["ManagerAgent", "ManagerConfig", "MANAGER_ROLE_PROMPT"]
