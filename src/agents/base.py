"""Base REACT-style trader agent.

Uses our OpenRouter wrapper and in-house tool registry.
No external agent frameworks.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from Utils.openrouter import chat_completion_raw
from src.agents.memory.context_manager import (
    ConversationTurn,
    TurnRole,
    enforce_max_prompt_tokens,
    render_instant_transcript,
    render_ledger_for_prompt,
    trim_instant_turns_to_budget,
)
from src.agents.memory.reground import rebuild_ledger_facts_from_mongo
from src.agents.memory.state_store import ContextStateStore
from src.agents.memory.summarizer import SummarizerConfig, summarize_narrative
from src.agents.memory.ledger_updates import apply_ledger_updates
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
    # Phase 7: persistent context (raw QnA + narrative + non-negotiable ledger).
    # Default off to preserve current behavior until orchestrator enables it.
    enable_phase7_context: bool = False
    enable_phase7_compression: bool = False
    phase7_summarizer_model: Optional[str] = None
    # Budget safety belt for the *incoming* market brief / firm state / positions that
    # are embedded into the user prompt. Tools have their own output caps.
    max_market_brief_tokens: int = 15000
    max_firm_state_tokens: int = 4000
    max_position_summary_tokens: int = 6000
    # Per-tool-call output budget to prevent provider context overflow.
    # This is an approximation (cl100k_base) but works as a safety belt.
    max_tool_output_tokens: int = 8000


class BaseTrader:
    """Common REACT loop for trader agents."""

    async def _audit_tool_call(
        self,
        *,
        run_id: Optional[str],
        cycle_id: Optional[str],
        tool_name: str,
        args: Dict[str, Any],
        raw_result: Any,
        prompt_payload: Any,
        truncated_for_prompt: bool,
        error: Optional[str],
    ) -> None:
        """Persist tool call inputs/outputs into Mongo audit_log (system-of-record)."""
        if not run_id:
            return
        if self.tools_context is None or self.tools_context.mongo is None:
            return
        mongo = self.tools_context.mongo

        # Mongo has a 16MB document limit. Store a bounded payload for safety.
        stored_result, stored_truncated = self._shrink_json_payload(
            jsonify(raw_result), max_tokens=50000
        )
        stored_prompt_payload, stored_prompt_truncated = self._shrink_json_payload(
            jsonify(prompt_payload), max_tokens=15000
        )

        payload: Dict[str, Any] = {
            "cycle_id": cycle_id,
            "tool": {"name": tool_name, "args": jsonify(args)},
            "result": {
                "error": error,
                "stored_truncated": bool(stored_truncated),
                "data": stored_result,
            },
            "prompt_payload": {
                "truncated_for_prompt": bool(truncated_for_prompt),
                "stored_truncated": bool(stored_prompt_truncated),
                "data": stored_prompt_payload,
            },
        }
        try:
            await mongo.log_audit_event(
                "tool_call",
                payload,
                run_id=run_id,
                agent_id=self.agent_id,
            )
        except Exception:
            # Best-effort: never fail the agent loop due to audit logging.
            return

    async def _load_phase7_blocks(
        self,
        *,
        run_id: Optional[str],
        cycle_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not self.config.enable_phase7_context:
            return None
        if not run_id:
            return None
        if self.tools_context is None or self.tools_context.mongo is None:
            return None

        store = ContextStateStore(mongo=self.tools_context.mongo)
        state = await store.load_or_create(run_id=run_id, agent_id=self.agent_id)

        # Safe MVP default: deterministically re-ground facts every cycle.
        facts = await rebuild_ledger_facts_from_mongo(
            mongo=self.tools_context.mongo,
            run_id=run_id,
            agent_id=self.agent_id,
            max_outcomes=10,
        )
        state.ledger.facts = facts

        kept, _dropped = trim_instant_turns_to_budget(
            turns=state.instant_turns,
            max_tokens=state.budget.max_instant_tokens,
        )
        state.instant_turns = kept
        if _dropped:
            dropped_text = render_instant_transcript(_dropped)
            if dropped_text:
                prefix = "\n\n--- Dropped instant turns (older) ---\n"
                state.narrative_summary = (state.narrative_summary or "") + prefix + dropped_text

        # Persist immediately so Phase 7 state exists even if the cycle later errors/hangs
        # during LLM/tool calls. Facts were just re-grounded from Mongo above.
        await store.save(state)

        async def _persist_after(*, user_turn: str, assistant_turn: str) -> None:
            if user_turn.strip():
                state.instant_turns.append(
                    ConversationTurn(
                        role=TurnRole.user,
                        content=user_turn.strip(),
                        cycle_id=cycle_id,
                        timestamp=datetime.utcnow(),
                    )
                )
            if assistant_turn.strip():
                state.instant_turns.append(
                    ConversationTurn(
                        role=TurnRole.assistant,
                        content=assistant_turn.strip(),
                        cycle_id=cycle_id,
                        timestamp=datetime.utcnow(),
                    )
                )
            kept2, _dropped2 = trim_instant_turns_to_budget(
                turns=state.instant_turns,
                max_tokens=state.budget.max_instant_tokens,
            )
            state.instant_turns = kept2

            appended_old = ""
            if _dropped2:
                appended_old = render_instant_transcript(_dropped2)
                if appended_old:
                    prefix = "\n\n--- Dropped instant turns (older) ---\n"
                    state.narrative_summary = (state.narrative_summary or "") + prefix + appended_old

            if self.config.enable_phase7_compression:
                narrative = state.narrative_summary or ""
                if narrative and self._count_tokens(narrative) > 20000:
                    model = (
                        self.config.phase7_summarizer_model
                        or os.getenv("LLM_MODEL_SUMMARIZER")
                        or os.getenv("LLM_MODEL_SUMMARISER")
                        or os.getenv("LLM_MODEL_MANAGER_FAST")
                        or os.getenv("LLM_MODEL_MANAGER")
                        or self.config.model
                    )
                    cfg = SummarizerConfig(model=model, temperature=0.0)
                    result = summarize_narrative(
                        config=cfg,
                        agent_id=self.agent_id,
                        run_id=run_id,
                        existing_narrative_summary=state.narrative_summary or "",
                        appended_old_transcript=appended_old,
                        current_watchlist=[w.model_dump(mode="json") for w in state.ledger.watchlist],
                        current_lessons_last_5=[l.model_dump(mode="json") for l in state.ledger.lessons_last_5],
                    )
                    state.narrative_summary = result.new_narrative_summary
                    if getattr(result, "ledger_updates", None):
                        apply_ledger_updates(ledger=state.ledger, updates=result.ledger_updates)
                    # Back-compat: allow full soft-state replacement if provided.
                    if getattr(result, "watchlist", None) is not None:
                        state.ledger.watchlist = result.watchlist or []
                    if getattr(result, "lessons_last_5", None) is not None:
                        state.ledger.lessons_last_5 = result.lessons_last_5 or []
            await store.save(state)

        return {
            "state": state,
            "ledger_json": render_ledger_for_prompt(state.ledger),
            "narrative_summary": state.narrative_summary or "",
            "instant_transcript": render_instant_transcript(state.instant_turns),
            "persist_after": _persist_after,
        }

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
        phase7_blocks: Optional[Dict[str, Any]] = None,
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

        if phase7_blocks is not None:
            user_parts.append("\n=== Grounded Ledger (non-negotiable; facts from Mongo) ===")
            user_parts.append(phase7_blocks.get("ledger_json") or "")
            user_parts.append("\n=== Narrative Summary (compressed older history) ===")
            user_parts.append(phase7_blocks.get("narrative_summary") or "")
            user_parts.append("\n=== Instant Memory (raw recent QnA transcript) ===")
            user_parts.append(phase7_blocks.get("instant_transcript") or "")

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

    def _tool_usage_summary(self) -> str:
        """Compact summary of tools used in the last decide() call."""
        calls = list(self.last_tool_calls or [])
        if not calls:
            return "tool_calls=0 tools=[]"
        names: List[str] = []
        for c in calls:
            if isinstance(c, dict) and c.get("name"):
                names.append(str(c["name"]))
        # Keep order but de-dup.
        seen: set[str] = set()
        unique: List[str] = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            unique.append(n)
        # Keep it short for prompt safety.
        unique = unique[:12]
        return f"tool_calls={len(calls)} tools={unique}"

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
        run_id = market_brief.get("run_id") if isinstance(market_brief, dict) else None
        cycle_id = market_brief.get("cycle_id") if isinstance(market_brief, dict) else None
        phase7 = await self._load_phase7_blocks(run_id=run_id, cycle_id=cycle_id)

        # Deterministically shrink large inbound payloads before prompt assembly.
        shrunk_market_brief = market_brief
        try:
            mb_payload, mb_trunc = self._shrink_json_payload(
                market_brief, max_tokens=self.config.max_market_brief_tokens
            )
            if mb_trunc and isinstance(mb_payload, dict):
                mb_payload.setdefault("meta", {})
                if isinstance(mb_payload["meta"], dict):
                    mb_payload["meta"]["truncated_for_prompt"] = True
                shrunk_market_brief = mb_payload
        except Exception:
            shrunk_market_brief = market_brief

        shrunk_firm_state = firm_state
        if firm_state is not None:
            try:
                fs_payload, fs_trunc = self._shrink_json_payload(
                    firm_state, max_tokens=self.config.max_firm_state_tokens
                )
                shrunk_firm_state = fs_payload if fs_trunc else firm_state
            except Exception:
                shrunk_firm_state = firm_state

        shrunk_position_summary = position_summary
        if position_summary is not None:
            try:
                ps_payload, ps_trunc = self._shrink_json_payload(
                    position_summary, max_tokens=self.config.max_position_summary_tokens
                )
                shrunk_position_summary = ps_payload if ps_trunc else position_summary
            except Exception:
                shrunk_position_summary = position_summary

        messages = self.build_messages(
            market_brief=shrunk_market_brief,
            firm_state=shrunk_firm_state,
            position_summary=shrunk_position_summary,
            memory_snippet=memory_snippet,
            extra_instructions=extra_instructions,
            phase7_blocks=phase7,
        )

        # Phase 7: enforce overall prompt budget (max_prompt_tokens≈75k) across
        # pinned/current + ledger + narrative + instant.
        if phase7 is not None and phase7.get("state") is not None:
            state = phase7["state"]
            max_prompt = int(getattr(state.budget, "max_prompt_tokens", 75000))
            system_text = (messages[0].get("content") or "") if messages else ""
            user_text = (messages[1].get("content") or "") if len(messages) > 1 else ""

            # Split user text into base portion and Phase 7 blocks so we can trim deterministically.
            marker = "\n=== Grounded Ledger (non-negotiable; facts from Mongo) ===\n"
            base_user_text = user_text.split(marker, 1)[0] if marker in user_text else user_text

            try:
                ledger_view, narrative, kept_turns = enforce_max_prompt_tokens(
                    system_text=system_text,
                    base_user_text=base_user_text,
                    ledger_json=phase7.get("ledger_json") or "",
                    narrative_summary=phase7.get("narrative_summary") or "",
                    instant_turns=list(getattr(state, "instant_turns", []) or []),
                    max_prompt_tokens=max_prompt,
                )
                state.narrative_summary = narrative
                state.instant_turns = kept_turns
                phase7["ledger_json"] = ledger_view or render_ledger_for_prompt(state.ledger)
                phase7["narrative_summary"] = state.narrative_summary or ""
                phase7["instant_transcript"] = render_instant_transcript(state.instant_turns)
                messages = self.build_messages(
                    market_brief=shrunk_market_brief,
                    firm_state=shrunk_firm_state,
                    position_summary=shrunk_position_summary,
                    memory_snippet=memory_snippet,
                    extra_instructions=extra_instructions,
                    phase7_blocks=phase7,
                )
            except Exception:
                # Hard fallback: drop Phase 7 blocks from the prompt rather than overflow provider limits.
                messages = self.build_messages(
                    market_brief=shrunk_market_brief,
                    firm_state=shrunk_firm_state,
                    position_summary=shrunk_position_summary,
                    memory_snippet=memory_snippet,
                    extra_instructions=extra_instructions,
                    phase7_blocks=None,
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

                await self._audit_tool_call(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    tool_name=fn_name,
                    args=args if isinstance(args, dict) else {},
                    raw_result=tool_out,
                    prompt_payload=payload,
                    truncated_for_prompt=truncated,
                    error=error,
                )

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
                if phase7 is not None and phase7.get("persist_after") is not None:
                    # Persist a compact tool usage summary *before* the final JSON to avoid
                    # polluting memory with full tool transcripts while retaining traceability.
                    tool_summary = self._tool_usage_summary()
                    user_turn = (
                        f"cycle_id={proposal.cycle_id} "
                        f"{tool_summary} "
                        f"neutral_summary={(market_brief.get('neutral_summary') or '') if isinstance(market_brief, dict) else ''}"
                    )
                    await phase7["persist_after"](  # type: ignore[misc]
                        user_turn=user_turn,
                        assistant_turn=final_content,
                    )
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
