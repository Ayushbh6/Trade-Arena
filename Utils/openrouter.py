"""OpenRouter SDK helper.

Wraps the official OpenRouter Python SDK (openrouter>=beta) with:
- env-based configuration
- sync/async chat helpers
- safe streaming generators that keep the client open
- tool calling passthrough
- structured output (json_schema/json_object) support
- a lightweight Conversation helper for multi-turn flows

Strategy-neutral: this module only handles model I/O and safety scaffolding.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Dict, Generator, Iterable, List, Optional, Union

from openrouter import OpenRouter

Message = Dict[str, Any]
ToolDef = Dict[str, Any]


class OpenRouterConfigError(RuntimeError):
    pass


def _get_api_key(api_key: Optional[str] = None) -> str:
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise OpenRouterConfigError(
            "OPENROUTER_API_KEY is not set. Provide api_key=... or set it in .env"
        )
    return key


@contextmanager
def openrouter_client(
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> Generator[OpenRouter, None, None]:
    """Yield a configured OpenRouter client."""
    key = _get_api_key(api_key)
    kwargs: Dict[str, Any] = {"api_key": key}
    if timeout_s is None:
        try:
            timeout_s = float(os.getenv("OPENROUTER_TIMEOUT_S", "").strip() or 0) or None
        except Exception:
            timeout_s = None
    if timeout_s is not None:
        # OpenRouter SDK expects milliseconds.
        kwargs["timeout_ms"] = int(timeout_s * 1000)
    # Note: OpenRouter SDK (v0.1.x) does not accept `max_retries` on the client.
    # Retries (if desired) are handled in our wrapper send() loop instead.

    with OpenRouter(**kwargs) as client:
        yield client


@asynccontextmanager
async def openrouter_client_async(
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> AsyncGenerator[OpenRouter, None]:
    """Async variant of openrouter_client."""
    key = _get_api_key(api_key)
    kwargs: Dict[str, Any] = {"api_key": key}
    if timeout_s is None:
        try:
            timeout_s = float(os.getenv("OPENROUTER_TIMEOUT_S", "").strip() or 0) or None
        except Exception:
            timeout_s = None
    if timeout_s is not None:
        # OpenRouter SDK expects milliseconds.
        kwargs["timeout_ms"] = int(timeout_s * 1000)
    # Note: OpenRouter SDK (v0.1.x) does not accept `max_retries` on the client.
    # Retries (if desired) are handled in our wrapper send() loop instead.

    async with OpenRouter(**kwargs) as client:
        yield client


def _chat_event_stream(
    messages: List[Message],
    model: str,
    api_key: Optional[str],
    params: Dict[str, Any],
) -> Generator[Any, None, None]:
    """Internal generator that keeps client/response open while streaming."""
    with openrouter_client(api_key=api_key) as client:
        res = client.chat.send(model=model, messages=messages, stream=True, **params)
        with res as event_stream:
            for event in event_stream:
                yield event


def _build_response_format(
    *,
    response_format: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "structured_output",
    strict_json: bool = True,
) -> Optional[Dict[str, Any]]:
    if response_format is not None:
        return response_format
    if output_schema is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": output_schema,
            "strict": strict_json,
        },
    }


def chat_completion_raw(
    messages: List[Message],
    model: str,
    *,
    api_key: Optional[str] = None,
    stream: bool = False,
    tools: Optional[List[ToolDef]] = None,
    tool_choice: Optional[Any] = None,
    response_format: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "structured_output",
    strict_json: bool = True,
    **params: Any,
) -> Union[Any, Iterable[Any]]:
    """Low-level chat completion returning the SDK response (or stream iterator).

    Supports tools and structured outputs.

    If output_schema is provided, we first try json_schema; if the model rejects it,
    we retry once with json_object + an added system instruction.
    """
    rf = _build_response_format(
        response_format=response_format,
        output_schema=output_schema,
        schema_name=schema_name,
        strict_json=strict_json,
    )

    send_params = dict(params)
    if tools is not None:
        send_params["tools"] = tools
    if tool_choice is not None:
        send_params["tool_choice"] = tool_choice
    if rf is not None:
        send_params["response_format"] = rf

    if stream:
        return _chat_event_stream(messages, model, api_key, send_params)

    def _send(msgs: List[Message], rf_override: Optional[Dict[str, Any]] = None):
        sp = dict(send_params)
        if rf_override is not None:
            sp["response_format"] = rf_override
        # Wrapper-level retries to smooth over transient provider errors.
        retries = 0
        try:
            retries = int(os.getenv("OPENROUTER_MAX_RETRIES", "").strip() or 0)
        except Exception:
            retries = 0
        attempts = max(1, 1 + max(0, retries))

        last_err: Optional[Exception] = None
        for _ in range(attempts):
            try:
                with openrouter_client(api_key=api_key) as client:
                    return client.chat.send(model=model, messages=msgs, stream=False, **sp)
            except Exception as e:  # pragma: no cover
                last_err = e
                continue
        assert last_err is not None
        raise last_err

    try:
        return _send(messages)
    except Exception:
        if output_schema is None:
            raise
        # Fallback: ask for plain JSON object + system schema hint
        schema_hint = json.dumps(output_schema, ensure_ascii=False)
        fallback_msgs = [
            {
                "role": "system",
                "content": (
                    "Return ONLY valid JSON matching this schema. Do not add extra keys. "
                    f"Schema: {schema_hint}"
                ),
            }
        ] + messages
        return _send(fallback_msgs, rf_override={"type": "json_object"})


def chat_completion(
    messages: List[Message],
    model: str,
    *,
    api_key: Optional[str] = None,
    stream: bool = False,
    tools: Optional[List[ToolDef]] = None,
    tool_choice: Optional[Any] = None,
    response_format: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "structured_output",
    strict_json: bool = True,
    **params: Any,
) -> Union[str, Iterable[Any]]:
    """High-level chat completion.

    If stream=False (default), returns assistant text.
    If stream=True, returns an iterator of SDK streaming events.

    Tools and structured-output args are forwarded to chat_completion_raw.
    """
    if stream:
        return chat_completion_raw(
            messages,
            model,
            api_key=api_key,
            stream=True,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            output_schema=output_schema,
            schema_name=schema_name,
            strict_json=strict_json,
            **params,
        )

    res = chat_completion_raw(
        messages,
        model,
        api_key=api_key,
        stream=False,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        output_schema=output_schema,
        schema_name=schema_name,
        strict_json=strict_json,
        **params,
    )
    try:
        return res.choices[0].message.content  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Unexpected OpenRouter response shape: {res}") from e


def chat_completion_stream_text(
    messages: List[Message],
    model: str,
    *,
    api_key: Optional[str] = None,
    tools: Optional[List[ToolDef]] = None,
    tool_choice: Optional[Any] = None,
    response_format: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "structured_output",
    strict_json: bool = True,
    **params: Any,
) -> Generator[str, None, None]:
    """Convenience generator that yields only text deltas."""
    send_params = dict(params)
    if tools is not None:
        send_params["tools"] = tools
    if tool_choice is not None:
        send_params["tool_choice"] = tool_choice
    rf = _build_response_format(
        response_format=response_format,
        output_schema=output_schema,
        schema_name=schema_name,
        strict_json=strict_json,
    )
    if rf is not None:
        send_params["response_format"] = rf

    for event in _chat_event_stream(messages, model, api_key, send_params):
        delta = None
        if getattr(event, "choices", None):
            delta = event.choices[0].delta.content
        if delta:
            yield delta


async def chat_completion_async(
    messages: List[Message],
    model: str,
    *,
    api_key: Optional[str] = None,
    stream: bool = False,
    tools: Optional[List[ToolDef]] = None,
    tool_choice: Optional[Any] = None,
    response_format: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "structured_output",
    strict_json: bool = True,
    **params: Any,
) -> Union[str, AsyncGenerator[Any, None]]:
    """Async chat completion.

    If stream=False, returns assistant text.
    If stream=True, raises NotImplementedError (SDK lacks async streaming).
    """
    if stream:
        raise NotImplementedError(
            "Async streaming is not exposed in SDK yet; use chat_completion_stream_text."
        )

    rf = _build_response_format(
        response_format=response_format,
        output_schema=output_schema,
        schema_name=schema_name,
        strict_json=strict_json,
    )
    send_params = dict(params)
    if tools is not None:
        send_params["tools"] = tools
    if tool_choice is not None:
        send_params["tool_choice"] = tool_choice
    if rf is not None:
        send_params["response_format"] = rf

    async with openrouter_client_async(api_key=api_key) as client:
        res = await client.chat.send_async(model=model, messages=messages, **send_params)
        try:
            return res.choices[0].message.content  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Unexpected OpenRouter response shape: {res}") from e


class Conversation:
    """Lightweight multi-turn conversation helper."""

    def __init__(self, system: Optional[str] = None):
        self.messages: List[Message] = []
        if system:
            self.messages.append({"role": "system", "content": system})

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_name: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "name": tool_name, "content": content}
        )

    def send_raw(self, model: str, **kwargs: Any) -> Any:
        res = chat_completion_raw(self.messages, model, **kwargs)
        # Persist assistant message if present
        try:
            msg = res.choices[0].message  # type: ignore[attr-defined]
            self.messages.append(
                {"role": msg.role, "content": msg.content, "tool_calls": getattr(msg, "tool_calls", None)}
            )
        except Exception:
            pass
        return res

    def send(self, model: str, **kwargs: Any) -> str:
        text = chat_completion(self.messages, model, **kwargs)
        if isinstance(text, str):
            self.add_assistant(text)
            return text
        raise RuntimeError("send() cannot be used with stream=True")


__all__ = [
    "OpenRouterConfigError",
    "openrouter_client",
    "openrouter_client_async",
    "chat_completion_raw",
    "chat_completion",
    "chat_completion_stream_text",
    "chat_completion_async",
    "Conversation",
]
