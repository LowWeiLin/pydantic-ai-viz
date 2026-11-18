"""Token streaming: ``agent.on_event`` -> ``stream.delta`` (spec section 4.4).

Spans never carry tokens, so the text a model is writing can only come from the
agent's event stream. One module-level listener is attached to each registered
agent, at most once, and stays there for the life of the process: Pydantic AI has
no way to remove a listener. It does nothing unless a session with ``stream=True``
is open.

The caveat that makes this opt-in: any event listener switches the agent's model
requests to streaming, and a model that cannot stream then fails.
"""

from __future__ import annotations

import json
import threading
import weakref
from collections.abc import Callable
from typing import Any, Literal, NamedTuple, TypeAlias

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)

PartKind: TypeAlias = Literal["text", "thinking", "tool_call"]


class Delta(NamedTuple):
    """One piece of a response part, as it goes on the wire."""

    part_index: int
    part_kind: PartKind
    tool_name: str | None
    """Set when the event names the tool; later deltas of the same part do not repeat it."""
    text: str


DeltaSink: TypeAlias = Callable[[str, Delta], None]
"""Receives ``(run_id, delta)``. The run id is what ties a delta to an open model request span."""

_lock = threading.Lock()
_sinks: list[DeltaSink] = []
# Agents are not hashable, so a WeakSet cannot hold them; key by identity and let entries die with the agent.
_attached: weakref.WeakValueDictionary[int, Agent[Any, Any]] = weakref.WeakValueDictionary()


def attach(agent: Agent[Any, Any]) -> None:
    """Put the listener on ``agent`` unless it is already there."""
    with _lock:
        if _attached.get(id(agent)) is agent:
            return
        _attached[id(agent)] = agent
    agent.on_event(_on_event)


def add_sink(sink: DeltaSink) -> None:
    with _lock:
        _sinks.append(sink)


def remove_sink(sink: DeltaSink) -> None:
    with _lock:
        if sink in _sinks:
            _sinks.remove(sink)


def _arguments(value: object) -> str:
    """Tool-call arguments as text: models stream JSON fragments, scripted ones may give a whole object."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def delta_of(event: object) -> Delta | None:
    """The delta an agent stream event stands for; ``None`` for every event that is not one."""
    if isinstance(event, PartStartEvent):
        part = event.part
        if isinstance(part, TextPart):
            return Delta(event.index, "text", None, part.content)
        if isinstance(part, ThinkingPart):
            return Delta(event.index, "thinking", None, part.content)
        if isinstance(part, ToolCallPart):
            return Delta(event.index, "tool_call", part.tool_name, _arguments(part.args))
        return None
    if isinstance(event, PartDeltaEvent):
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            return Delta(event.index, "text", None, delta.content_delta)
        if isinstance(delta, ThinkingPartDelta):
            # A thinking delta may carry only a signature.
            return None if delta.content_delta is None else Delta(event.index, "thinking", None, delta.content_delta)
        if isinstance(delta, ToolCallPartDelta):
            return Delta(event.index, "tool_call", delta.tool_name_delta, _arguments(delta.args_delta))
    return None


def _on_event(ctx: RunContext[Any], event: object) -> None:
    """The listener. Runs inside the user's agent run, so it must never raise."""
    if not _sinks:
        return
    try:
        delta = delta_of(event)
        run_id = ctx.run_id
        if delta is None or run_id is None:
            return
        with _lock:
            sinks = tuple(_sinks)
        for sink in sinks:
            sink(run_id, delta)
    except Exception:
        return
