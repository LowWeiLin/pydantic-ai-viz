"""``CaptureEvents``: the opt-in capability that puts capability and custom events on the run's spans.

Pydantic AI does not record ``CapabilityEvent``s or ``CustomEvent``s in its telemetry, and the viewer
cannot add a listener to an agent on its own. A capability's ``on_event`` receives them while a span of
the run is the current one (the agent run, or the tool an ``immediate`` event is emitted in), so this one adds each
as an OpenTelemetry span event that ``_normalize`` reads back. Like ``_capture``, it never raises.
"""

from __future__ import annotations

import contextlib
import dataclasses
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from pydantic_ai.capabilities import AbstractCapability
from pydantic_core import to_json

from . import _compat
from ._normalize import EVENT_NAME

_STAMPED = frozenset({"kind", "name", "event_kind", "capability_id", "tool_call_id", "tool_name"})
"""Fields of both event classes that are attributes of their own; the others are the event's data."""


@dataclass
class CaptureEvents(AbstractCapability[Any]):
    """Show the agent's capability and custom events on its runs in the viewer.

    Add it to the agent: `Agent(..., capabilities=[CaptureEvents()])`. Each `CapabilityEvent` and
    `CustomEvent` of a run is recorded on the span that is current when it is dispatched: the run's,
    or a tool's for an `immediate` event emitted while the tool runs. Their fields are content: they
    are only recorded when the run's instrumentation includes content.
    It changes nothing else about the agent and is left out of its blueprint.
    """

    async def on_event(self, ctx: Any, *, event: Any) -> None:
        with contextlib.suppress(Exception):  # the run must not notice the viewer
            _record(ctx, event)


def _record(ctx: Any, event: Any) -> None:
    if not isinstance(event, _compat.EVENT_CLASSES):
        return
    span = trace.get_current_span()
    if not span.is_recording():
        return
    custom = event.event_kind == "custom"
    attributes: dict[str, Any] = {
        "kind": (event.name or type(event).__name__) if custom else event.kind,
        "event_kind": "custom" if custom else "capability",
    }
    for key in ("capability_id", "tool_call_id", "tool_name"):
        value = getattr(event, key, None)
        if isinstance(value, str):
            attributes[key] = value
    if getattr(ctx, "trace_include_content", False):
        data = {f.name: getattr(event, f.name) for f in dataclasses.fields(event) if f.name not in _STAMPED}
        attributes["data"] = to_json(data, fallback=repr).decode()
    span.add_event(EVENT_NAME, attributes)
