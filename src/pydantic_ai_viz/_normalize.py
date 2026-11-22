"""OpenTelemetry span -> wire span (spec section 3.4).

This is the only module that knows the span attribute names Pydantic AI emits.
The viewer never interprets raw attributes; whatever is not understood here is
passed through in ``attributes`` (or the whole span degrades to ``kind: "other"``),
so a new Pydantic AI release can lose detail but cannot break a session.

Written against instrumentation version 5. Versions 3, 4 and 6 use the same keys;
version 6 only changes the role of tool-result messages, which is handled below.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from ._models import (
    MAX_STRING_CHARS,
    AgentRunData,
    AgentRunSpan,
    Deferral,
    DeferredCall,
    GraphNodeData,
    GraphNodeSpan,
    GraphRunData,
    GraphRunSpan,
    Message,
    ModelRequestData,
    ModelRequestSpan,
    OtherPart,
    OtherSpan,
    RetryInfo,
    RunEvent,
    SpanError,
    TextPart,
    ThinkingPart,
    ToolCallData,
    ToolCallPart,
    ToolCallSpan,
    ToolResultPart,
    Usage,
)

WireSpan: TypeAlias = AgentRunSpan | ModelRequestSpan | ToolCallSpan | GraphRunSpan | GraphNodeSpan | OtherSpan
WirePart: TypeAlias = TextPart | ThinkingPart | ToolCallPart | ToolResultPart | OtherPart
Role: TypeAlias = Literal["system", "user", "assistant", "tool"]
Status: TypeAlias = Literal["running", "ok", "error"]

# Attributes written by our own tracer for graph runs (``_graph.py``).
TRACER_NAME = "pydantic_ai_viz"
ATTR_KIND = "pydantic_ai_viz.kind"
ATTR_GRAPH_ID = "pydantic_ai_viz.graph.id"
ATTR_GRAPH_NAME = "pydantic_ai_viz.graph.name"
ATTR_GRAPH_NODE_ID = "pydantic_ai_viz.graph.node_id"
ATTR_GRAPH_INPUTS = "pydantic_ai_viz.graph.inputs"
ATTR_GRAPH_OUTPUT = "pydantic_ai_viz.graph.output"
EVENT_NAME = "pydantic_ai_viz.event"
"""Name of the span events ``CaptureEvents`` adds, one per capability or custom event."""

# Attributes written by Pydantic AI.
ATTR_OPERATION = "gen_ai.operation.name"
ATTR_AGENT_NAME = "gen_ai.agent.name"
ATTR_AGENT_NAME_LEGACY = "agent_name"
ATTR_AGENT_DESCRIPTION = "gen_ai.agent.description"
ATTR_RUN_ID = "gen_ai.agent.call.id"
ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
ATTR_INSTRUCTIONS = "gen_ai.system_instructions"
ATTR_REQUEST_PARAMETERS = "model_request_parameters"
REQUEST_PREFIX = "gen_ai.request."

_NEVER_ON_THE_WIRE = frozenset({ATTR_REQUEST_PARAMETERS, "gen_ai.tool.definitions", "logfire.json_schema"})
"""Bulky and redundant: tool definitions reach the viewer through the blueprint instead."""

_ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})


@dataclass(frozen=True, slots=True)
class Normalized:
    """One span in wire form, plus what the derivation rules need and the wire does not carry."""

    span: WireSpan
    agent_description: str | None
    """``gen_ai.agent.description`` of an agent run, for observed blueprints."""
    request_parameters: dict[str, Any] | None
    """Parsed ``model_request_parameters`` of an ended model request."""


# ------------------------------------------------------------------- values


def clip(value: Any) -> Any:
    """Make ``value`` JSON-safe and cut over-long strings (wire rule 5)."""
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return f"{value[:MAX_STRING_CHARS]}... [truncated, {len(value)} chars total]"
        return value
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): clip(item) for key, item in cast("Mapping[Any, Any]", value).items()}
    if isinstance(value, list | tuple):
        return [clip(item) for item in cast("list[Any]", value)]
    return str(value)


def parse_jsonish(value: Any) -> Any:
    """Decode a JSON string; a string that is not JSON is kept as it is."""
    if isinstance(value, str):
        try:
            return clip(json.loads(value))
        except ValueError:
            return clip(value)
    return clip(value)


def _text(value: Any) -> str | None:
    return None if value is None else cast("str", clip(str(value)))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _Attributes:
    """Span attributes that remember which keys were turned into wire fields."""

    def __init__(self, raw: Mapping[str, Any] | None) -> None:
        self._raw: dict[str, Any] = dict(raw or {})
        self._taken: set[str] = set()

    def peek(self, key: str) -> Any:
        return self._raw.get(key)

    def take(self, key: str) -> Any:
        self._taken.add(key)
        return self._raw.get(key)

    def take_prefix(self, prefix: str) -> dict[str, Any]:
        found = {key[len(prefix) :]: value for key, value in self._raw.items() if key.startswith(prefix)}
        self._taken.update(prefix + suffix for suffix in found)
        return found

    def rest(self) -> dict[str, Any]:
        return {
            key: clip(value)
            for key, value in self._raw.items()
            if key not in self._taken and key not in _NEVER_ON_THE_WIRE
        }


# ----------------------------------------------------------------- messages


def _part(raw: Any) -> WirePart:
    if not isinstance(raw, dict):
        return OtherPart(type="other", raw=clip(raw))
    part = cast("dict[str, Any]", raw)
    kind = part.get("type")
    content = part.get("content")
    name = part.get("name")
    call_id = _text(part.get("id"))
    if kind == "text" and (content is None or isinstance(content, str)):
        return TextPart(type="text", content=_text(content))
    if kind in ("thinking", "reasoning") and (content is None or isinstance(content, str)):
        return ThinkingPart(type="thinking", content=_text(content))
    if kind == "tool_call" and isinstance(name, str):
        # Streamed requests report the arguments as the raw JSON string the model produced.
        return ToolCallPart(type="tool_call", id=call_id, name=name, arguments=parse_jsonish(part.get("arguments")))
    if kind == "tool_call_response" and isinstance(name, str):
        return ToolResultPart(type="tool_result", id=call_id, name=name, result=clip(part.get("result")))
    return OtherPart(type="other", raw=clip(part))


def _message(raw: Any) -> Message:
    if not isinstance(raw, dict):
        return Message(role="user", parts=[OtherPart(type="other", raw=clip(raw))])
    message = cast("dict[str, Any]", raw)
    raw_parts = message.get("parts")
    parts = [_part(item) for item in cast("list[Any]", raw_parts)] if isinstance(raw_parts, list) else []
    role = message.get("role")
    if parts and all(isinstance(part, ToolResultPart) for part in parts):
        # Instrumentation v5 sends tool results as "user", v6 as "tool".
        resolved: Role = "tool"
    elif isinstance(role, str) and role in _ROLES:
        resolved = cast("Role", role)
    else:
        resolved = "user"
    return Message(role=resolved, parts=parts)


def messages(raw: Any) -> list[Message] | None:
    """Normalise a ``gen_ai.*.messages`` / ``pydantic_ai.all_messages`` attribute."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list):
        return None
    return [_message(item) for item in cast("list[Any]", raw)]


def instructions(raw: Any) -> str | None:
    """Resolved instructions: the ``content`` of each entry, joined by a blank line."""
    if raw is None:
        return None
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return _text(raw)
    if not isinstance(parsed, list):
        return _text(raw)
    texts: list[str] = []
    for item in cast("list[Any]", parsed):
        content: Any = cast("dict[str, Any]", item).get("content") if isinstance(item, dict) else item
        if isinstance(content, str):
            texts.append(content)
    return _text("\n\n".join(texts)) if texts else None


def _usage(attributes: _Attributes, prefix: str) -> Usage | None:
    input_tokens = _int(attributes.take(f"{prefix}input_tokens"))
    output_tokens = _int(attributes.take(f"{prefix}output_tokens"))
    if input_tokens is None and output_tokens is None:
        return None
    return Usage(input_tokens=input_tokens or 0, output_tokens=output_tokens or 0)


def _request_parameters(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else None


# -------------------------------------------------------------------- spans


def _hex(value: int, width: int) -> str:
    return format(value, f"0{width}x")


def _milliseconds(nanoseconds: int | None) -> float | None:
    return None if nanoseconds is None else nanoseconds / 1_000_000


def _strip(name: str, prefix: str) -> str:
    return name[len(prefix) :] if name.startswith(prefix) else name


def _error(span: ReadableSpan, status: Status) -> SpanError | None:
    for event in reversed(span.events):
        if event.name == "exception":
            details: Mapping[str, Any] = event.attributes or {}
            kind = str(details.get("exception.type") or "Error")
            attributes = span.attributes or {}
            # A retry the model is asked for is expected: its message is shown, not where it was raised.
            retried = kind.endswith("ToolRetryError") or any(
                key in attributes for key in ("pydantic_ai.tool.retry.cause", "pydantic_ai.output.retry.cause")
            )
            return SpanError(
                type=kind,
                message=_text(details.get("exception.message")),
                stacktrace=None if retried else _text(details.get("exception.stacktrace")),
            )
    if status == "error":
        return SpanError(type="Error", message=_text(span.status.description), stacktrace=None)
    return None


def _events(span: ReadableSpan) -> list[RunEvent]:
    found: list[RunEvent] = []
    for event in span.events:
        details: Mapping[str, Any] = event.attributes or {}
        kind = details.get("kind")
        if event.name != EVENT_NAME or not isinstance(kind, str):
            continue
        found.append(
            RunEvent(
                kind=cast("str", _text(kind)),
                event_kind="custom" if details.get("event_kind") == "custom" else "capability",
                capability_id=_text(details.get("capability_id")),
                tool_call_id=_text(details.get("tool_call_id")),
                tool_name=_text(details.get("tool_name")),
                ts_ms=_milliseconds(event.timestamp) or 0.0,
                data=parse_jsonish(details.get("data")),
            )
        )
    return found


def _retry(attributes: _Attributes) -> RetryInfo | None:
    cause = attributes.take("pydantic_ai.tool.retry.cause")
    attempt = _int(attributes.take("pydantic_ai.tool.retry.attempt"))
    max_retries = _int(attributes.take("pydantic_ai.tool.retry.max_retries"))
    if not isinstance(cause, str):
        return None
    return RetryInfo(cause=cause, attempt=attempt or 0, max_retries=max_retries or 0)


_DEFERRALS: dict[str, Deferral] = {"ApprovalRequired": "approval", "CallDeferred": "external"}
"""``pydantic_ai.tool.deferral.name`` -> what the call is left waiting for."""


def _deferral(attributes: _Attributes) -> Deferral | None:
    name = attributes.take("pydantic_ai.tool.deferral.name")
    return _DEFERRALS.get(name) if isinstance(name, str) else None


def _requested(raw: list[Any], kind: Deferral) -> list[DeferredCall]:
    """The ``ToolCallPart``s of one list of a ``DeferredToolRequests``; nothing if any item is not one."""
    parts = [cast("dict[str, Any]", item) for item in raw if isinstance(item, dict)]
    if len(parts) != len(raw) or any(part.get("part_kind") != "tool-call" for part in parts):
        return []
    return [
        DeferredCall(call_id=_text(part.get("tool_call_id")), tool_name=str(part.get("tool_name")), kind=kind)
        for part in parts
    ]


def deferred(raw_output: Any, history: list[Message] | None) -> list[DeferredCall]:
    """The tool calls an ended run is waiting on: those of a ``DeferredToolRequests`` output.

    Only the names and call ids are kept: arguments and metadata are content. Without content
    capture there is no output to read, so the calls are those of the last model response that
    no tool result answers. Those are taken to wait for approval; the session corrects the
    kind of the calls it knows run outside the agent.
    """
    output: Any = raw_output
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return []
    if isinstance(output, dict):
        request = cast("dict[str, Any]", output)
        calls, approvals = request.get("calls"), request.get("approvals")
        if not isinstance(calls, list) or not isinstance(approvals, list):
            return []
        return [
            *_requested(cast("list[Any]", calls), "external"),
            *_requested(cast("list[Any]", approvals), "approval"),
        ]
    if output is not None or not history:
        return []
    last = max((index for index, message in enumerate(history) if message.role == "assistant"), default=None)
    if last is None:
        return []
    answered = {
        part.id for message in history[last + 1 :] for part in message.parts if isinstance(part, ToolResultPart)
    }
    return [
        DeferredCall(call_id=part.id, tool_name=part.name, kind="approval")
        for part in history[last].parts
        if isinstance(part, ToolCallPart) and part.id not in answered
    ]


def normalize(span: ReadableSpan, *, ended: bool) -> Normalized:
    """Turn an OTel span into its wire form.

    Args:
        span: The span as seen by a ``SpanProcessor``.
        ended: ``False`` from ``on_start`` (the result has status ``"running"``, no end
            time, no leftover ``attributes`` and only the data known at start); ``True``
            from ``on_end``.
    """
    attributes = _Attributes(span.attributes)
    context = span.get_span_context()
    parent = span.parent
    if not ended:
        status: Status = "running"
    else:
        status = "error" if span.status.status_code is StatusCode.ERROR else "ok"

    agent_name = attributes.take(ATTR_AGENT_NAME)
    legacy_agent_name = attributes.take(ATTR_AGENT_NAME_LEGACY)
    if not isinstance(agent_name, str):
        agent_name = legacy_agent_name if isinstance(legacy_agent_name, str) else None
    run_id = attributes.take(ATTR_RUN_ID)

    operation = attributes.peek(ATTR_OPERATION)
    own_kind = attributes.take(ATTR_KIND)
    graph_id = attributes.take(ATTR_GRAPH_ID)
    description: str | None = None
    request_parameters: dict[str, Any] | None = None

    def base(name: str) -> dict[str, Any]:
        return {
            "trace_id": _hex(context.trace_id if context else 0, 32),
            "span_id": _hex(context.span_id if context else 0, 16),
            "parent_id": None if parent is None else _hex(parent.span_id, 16),
            "name": name,
            "start_ms": _milliseconds(span.start_time) or 0.0,
            "end_ms": _milliseconds(span.end_time) if ended else None,
            "status": status,
            "agent_name": agent_name,
            "run_id": run_id if isinstance(run_id, str) else None,
            "error": _error(span, status) if ended else None,
            "events": _events(span) if ended else [],
            # Taken last, so every key read above has been consumed.
            "attributes": attributes.rest() if ended else {},
        }

    wire: WireSpan
    if own_kind == "graph_run" and isinstance(graph_id, str):
        graph_name = attributes.take(ATTR_GRAPH_NAME)
        name = graph_name if isinstance(graph_name, str) else _strip(span.name, "run graph ")
        data = GraphRunData(
            graph_id=graph_id,
            graph_name=name,
            inputs=parse_jsonish(attributes.take(ATTR_GRAPH_INPUTS)),
            output=parse_jsonish(attributes.take(ATTR_GRAPH_OUTPUT)),
        )
        wire = GraphRunSpan(kind="graph_run", data=data, **base(name))
    elif own_kind == "graph_node" and isinstance(graph_id, str):
        node_id = attributes.take(ATTR_GRAPH_NODE_ID)
        name = node_id if isinstance(node_id, str) else _strip(span.name, "run node ")
        attributes.take(ATTR_GRAPH_NAME)
        node = GraphNodeData(graph_id=graph_id, node_id=name, inputs=parse_jsonish(attributes.take(ATTR_GRAPH_INPUTS)))
        wire = GraphNodeSpan(kind="graph_node", data=node, **base(name))
    elif operation == "invoke_agent":
        raw_description = attributes.peek(ATTR_AGENT_DESCRIPTION)
        description = raw_description if isinstance(raw_description, str) else None
        raw_output = attributes.take("final_result")
        history = messages(attributes.take("pydantic_ai.all_messages"))
        run = AgentRunData(
            model_name=_text(attributes.take("model_name")),
            conversation_id=_text(attributes.take(ATTR_CONVERSATION_ID)),
            instructions=instructions(attributes.take(ATTR_INSTRUCTIONS)),
            output=parse_jsonish(raw_output),
            usage=_usage(attributes, "gen_ai.aggregated_usage."),
            messages=history,
            deferred=deferred(raw_output, history) if status == "ok" else [],
        )
        wire = AgentRunSpan(kind="agent_run", data=run, **base(agent_name or _strip(span.name, "invoke_agent ")))
    elif operation == "chat":
        name = _strip(span.name, "chat ")
        settings = attributes.take_prefix(REQUEST_PREFIX)
        model_name = settings.pop("model", None)
        provider = attributes.take("gen_ai.provider.name")
        system = attributes.take("gen_ai.system")
        request_parameters = _request_parameters(attributes.peek(ATTR_REQUEST_PARAMETERS))
        request = ModelRequestData(
            model_name=model_name if isinstance(model_name, str) else name,
            provider=_text(provider if provider is not None else system),
            response_model=_text(attributes.take("gen_ai.response.model")),
            settings={key: clip(value) for key, value in settings.items()},
            instructions=instructions(attributes.take(ATTR_INSTRUCTIONS)),
            input_messages=messages(attributes.take("gen_ai.input.messages")),
            output_messages=messages(attributes.take("gen_ai.output.messages")),
            usage=_usage(attributes, "gen_ai.usage."),
        )
        wire = ModelRequestSpan(kind="model_request", data=request, **base(name))
    elif operation == "execute_tool":
        tool_name = attributes.take("gen_ai.tool.name")
        name = tool_name if isinstance(tool_name, str) else _strip(span.name, "execute_tool ")
        call = ToolCallData(
            tool_name=name,
            call_id=_text(attributes.take("gen_ai.tool.call.id")),
            arguments=parse_jsonish(attributes.take("gen_ai.tool.call.arguments")),
            result=parse_jsonish(attributes.take("gen_ai.tool.call.result")),
            retry=_retry(attributes),
            deferral=_deferral(attributes),
        )
        wire = ToolCallSpan(kind="tool_call", data=call, **base(name))
    else:
        wire = OtherSpan(kind="other", data=None, **base(span.name))
    return Normalized(span=wire, agent_description=description, request_parameters=request_parameters)
