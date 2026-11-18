"""Session: the append-only event log and everything that writes to it.

One log feeds every consumer. The live server tails it, a recording is the log
written out line by line, and an HTML export embeds it. Producers are the span
processor (runs), ``register`` (blueprints) and the token listener (deltas).
"""

from __future__ import annotations

import bisect
import contextlib
import platform
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType, TracebackType
from typing import Any, Self, TypeAlias, cast

from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from pydantic_ai import Agent, InstrumentationSettings
from pydantic_graph import Graph

from . import _compat, _stream
from ._capture import CaptureProcessor
from ._derive import add_delegate, carry_observed, merge_request, observed_blueprint
from ._introspect import AnyAgent, AnyGraph, agent_closure, introspect_agent, introspect_graph
from ._models import WIRE_VERSION, AgentBlueprint, GraphBlueprint, to_wire
from ._normalize import Normalized, WireSpan, clip
from ._version import __version__

Target: TypeAlias = "AnyAgent | AnyGraph | ModuleType"
Envelope: TypeAlias = dict[str, Any]

DEFAULT_MAX_EVENTS = 50_000
DEFAULT_TITLE = "pydantic-ai-viz"

# ---------------------------------------------------------------- event log


def _trace_of(envelope: Envelope) -> str | None:
    kind = envelope["type"]
    if kind in ("span.started", "span.ended"):
        return envelope["span"]["trace_id"]
    if kind == "stream.delta":
        return envelope["trace_id"]
    return None


class EventLog:
    """Thread-safe, append-only list of envelopes with ``seq`` numbering and eviction.

    ``seq`` and ``ts`` are assigned under the lock, so list order, ``seq`` order and
    ``ts`` order always agree.
    """

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self.max_events = max_events
        self._changed = threading.Condition()
        self._events: list[Envelope] = []
        self._next_seq = 0
        self._last_ts = 0.0
        self._open_spans: dict[str, set[str]] = {}
        """Trace id -> span ids started but not ended. Insertion order is age order."""
        self._sizes: dict[str, int] = {}
        """Trace id -> number of retained events."""
        self._subscribers = 0
        self._closed = False

    def append(self, body: Mapping[str, Any]) -> Envelope | None:
        """Add one event (``type`` plus its fields) and return the envelope; ``None`` once closed."""
        with self._changed:
            if self._closed:
                return None
            self._last_ts = max(time.time_ns() / 1_000_000, self._last_ts)
            envelope: Envelope = {"seq": self._next_seq, "ts": self._last_ts, **body}
            self._next_seq += 1
            self._events.append(envelope)
            trace_id = _trace_of(envelope)
            if trace_id is not None:
                open_spans = self._open_spans.setdefault(trace_id, set())
                self._sizes[trace_id] = self._sizes.get(trace_id, 0) + 1
                if envelope["type"] == "span.started":
                    open_spans.add(envelope["span"]["span_id"])
                elif envelope["type"] == "span.ended":
                    open_spans.discard(envelope["span"]["span_id"])
            if len(self._events) > self.max_events:
                self._evict()
            self._changed.notify_all()
            return envelope

    def _evict(self) -> None:
        """Drop the oldest completed traces whole until the log fits again.

        ``session.started`` and blueprints belong to no trace and always stay, and so
        does a trace with a span still open, even if that leaves the log over the limit.
        """
        excess = len(self._events) - self.max_events
        doomed: set[str] = set()
        for trace_id, open_spans in self._open_spans.items():
            if excess <= 0:
                break
            if not open_spans:
                doomed.add(trace_id)
                excess -= self._sizes[trace_id]
        if not doomed:
            return
        self._events = [envelope for envelope in self._events if _trace_of(envelope) not in doomed]
        for trace_id in doomed:
            del self._open_spans[trace_id]
            del self._sizes[trace_id]

    @property
    def last_seq(self) -> int:
        """``seq`` of the newest event ever appended, or ``-1``."""
        with self._changed:
            return self._next_seq - 1

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self, after: int = -1) -> list[Envelope]:
        """Retained envelopes with ``seq > after``, oldest first."""
        with self._changed:
            return self._after(after)

    def _after(self, seq: int) -> list[Envelope]:
        return self._events[bisect.bisect_right(self._events, seq, key=lambda envelope: envelope["seq"]) :]

    def subscribe(self, after: int | None = None) -> Subscription:
        """Follow the log from just after ``seq == after`` (from the start when ``None``).

        An ``after`` beyond the newest ``seq`` means the reader last talked to another
        log (the process restarted), so it is sent everything.
        """
        with self._changed:
            cursor = -1 if after is None or after >= self._next_seq else after
            self._subscribers += 1
            self._changed.notify_all()
        return Subscription(self, cursor)

    @property
    def subscriber_count(self) -> int:
        return self._subscribers

    def wait_for_subscriber(self, timeout: float | None = None) -> bool:
        """Block until someone follows the log; ``False`` on timeout or close."""
        with self._changed:
            self._changed.wait_for(lambda: self._subscribers > 0 or self._closed, timeout)
            return self._subscribers > 0

    def close(self) -> None:
        """Stop accepting events and wake every waiting reader. Reading still works."""
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def _wait(self, cursor: int, timeout: float | None) -> list[Envelope]:
        with self._changed:
            self._changed.wait_for(lambda: self._next_seq - 1 > cursor or self._closed, timeout)
            return self._after(cursor)

    def _unsubscribe(self) -> None:
        with self._changed:
            self._subscribers -= 1


class Subscription:
    """A reader's cursor into an ``EventLog``. Not shared between threads."""

    def __init__(self, log: EventLog, cursor: int) -> None:
        self._log = log
        self._cursor = cursor
        self._open = True

    def get(self, timeout: float | None = None) -> list[Envelope]:
        """Envelopes newer than the last ones returned, waiting up to ``timeout`` seconds for the first.

        An empty list means the timeout passed or the log was closed (see ``closed``).
        """
        events = self._log._wait(self._cursor, timeout)  # pyright: ignore[reportPrivateUsage]
        if events:
            self._cursor = events[-1]["seq"]
        return events

    @property
    def closed(self) -> bool:
        """True once the log is closed; ``get`` no longer blocks then."""
        return self._log.closed

    def close(self) -> None:
        if self._open:
            self._open = False
            self._log._unsubscribe()  # pyright: ignore[reportPrivateUsage]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ------------------------------------------------------------------ session

_sessions_lock = threading.Lock()
_open_sessions: list[Session] = []


def active_session() -> Session | None:
    """The most recently opened session that is still open."""
    with _sessions_lock:
        return _open_sessions[-1] if _open_sessions else None


@dataclass(slots=True)
class _RegisteredAgent:
    agent: AnyAgent
    id: str
    label: str | None


@dataclass(slots=True)
class _RegisteredGraph:
    graph: AnyGraph
    id: str


@dataclass(slots=True)
class _OpenRequest:
    """A model request in flight: where the token deltas of its run belong."""

    trace_id: str
    span_id: str
    tool_names: dict[int, str]
    """Part index -> tool name, from the event that opened the part."""


class Session:
    """A capture session: registered blueprints plus every run observed while it is open.

    Create one with `record()` (capture only) or `serve()` (capture plus the local
    viewer). A session is a context manager; leaving the block calls `close()`.

    Attributes:
        url: Address of the local viewer, or `None` when there is no server.
        title: Shown in the viewer's top bar and used for export file names.
        include_content: Whether prompts, tool arguments and results are captured.
        stream: Whether token streaming is captured.
    """

    def __init__(
        self,
        *,
        title: str | None = None,
        include_content: bool = True,
        stream: bool = False,
        instrument: bool = True,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        """Open a session. Prefer `record()` or `serve()`, which also register targets.

        Args:
            title: Session title; defaults to `"pydantic-ai-viz"`.
            include_content: Capture prompts, tool arguments and results.
            stream: Capture token deltas (switches model requests to streaming).
            instrument: Call `Agent.instrument_all` with this session's tracer provider.
            max_events: Size of the event log before whole completed runs are dropped.

        Raises:
            RuntimeError: If `instrument` is true and another instrumenting session is open.
        """
        self.url: str | None = None
        self.title = title or DEFAULT_TITLE
        self.include_content = include_content
        self.stream = stream
        self.session_id = uuid.uuid4().hex

        self._lock = threading.RLock()
        self._log = EventLog(max_events)
        self._processor = CaptureProcessor(self._span_started, self._span_ended)
        # A private provider: nothing global is set, and an existing OTel setup is left alone.
        self._tracer_provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
        self._tracer_provider.add_span_processor(self._processor)
        self._instrumentation: InstrumentationSettings | None = None
        self._previous_instrumentation: Any = False
        self._closed = False

        self._agents: dict[int, _RegisteredAgent] = {}
        self._graphs: dict[int, _RegisteredGraph] = {}
        self._agent_blueprints: dict[str, AgentBlueprint] = {}
        self._agent_count = 0
        self._graph_count = 0
        self._open_tool_calls: dict[str, tuple[str | None, str]] = {}
        """Span id of a running tool call -> (owning agent name, tool name)."""
        self._merged_requests: set[str] = set()
        """Agent names whose first model request has been folded into their blueprint."""
        self._open_requests: dict[str, _OpenRequest] = {}
        """Run id -> its model request in flight. A run makes one request at a time."""

        with _sessions_lock:
            if instrument:
                if any(other._instrumentation is not None for other in _open_sessions):
                    raise RuntimeError("a pydantic-ai-viz session is already active; close it first")
                self._instrumentation = InstrumentationSettings(
                    tracer_provider=self._tracer_provider, version=5, include_content=include_content
                )
                previous = _compat.instrumentation_default()
                self._previous_instrumentation = False if previous is None else previous
                Agent.instrument_all(self._instrumentation)
            _open_sessions.append(self)
        if stream:
            _stream.add_sink(self._stream_delta)

        self._log.append(
            {
                "type": "session.started",
                "pviz": WIRE_VERSION,
                "session_id": self.session_id,
                "title": self.title,
                "include_content": include_content,
                "stream": stream,
                "versions": {
                    "pydantic_ai_viz": __version__,
                    "pydantic_ai": _compat.pydantic_ai_version(),
                    "python": platform.python_version(),
                },
            }
        )

    # ------------------------------------------------------------ reading

    @property
    def events(self) -> list[dict[str, Any]]:
        """A snapshot of the event log: JSON-ready envelopes, oldest first.

        The list is new on every access; the envelopes in it are the log's own and
        must be treated as read-only.
        """
        return self._log.snapshot()

    @property
    def span_processor(self) -> SpanProcessor:
        """The processor that feeds this session.

        Only needed with `instrument=False`: add it to your own tracer provider, for
        example `logfire.configure(additional_span_processors=[session.span_processor])`.
        """
        return self._processor

    @property
    def closed(self) -> bool:
        """True once `close()` has run."""
        return self._closed

    # -------------------------------------------------------- registration

    def register(self, *targets: Target) -> None:
        """Add blueprints for agents, graphs, or every agent and graph in a module.

        Agents reachable through the static delegation heuristic are registered too.
        Registering the same object again does nothing.

        Raises:
            TypeError: If a target is not an `Agent`, a `Graph` or a module.
        """
        with self._lock:
            for target in targets:
                if isinstance(target, Agent):
                    self._register_agent(target, {})
                elif isinstance(target, Graph):
                    self._register_graph(target, None)
                elif isinstance(target, ModuleType):  # pyright: ignore[reportUnnecessaryIsInstance]
                    self._register_module(target)
                else:
                    raise TypeError(
                        f"register() expects a pydantic_ai.Agent, a pydantic_graph.Graph or a module, "
                        f"got {type(target).__name__}"
                    )

    def _register_module(self, module: ModuleType) -> None:
        members = list(vars(module).items())
        # Variable names are the fallback labels, also for agents first reached as a delegate.
        labels = {id(cast("object", value)): name for name, value in reversed(members) if isinstance(value, Agent)}
        for name, value in members:
            if isinstance(value, Agent):
                self._register_agent(cast("AnyAgent", value), labels)
            elif isinstance(value, Graph):
                self._register_graph(cast("AnyGraph", value), name)

    def _register_agent(self, root: AnyAgent, labels: Mapping[int, str]) -> None:
        if id(root) in self._agents:
            return
        added: list[_RegisteredAgent] = []
        for agent in agent_closure(root):
            if id(agent) not in self._agents:
                # An agent already seen running keeps the id of its observed blueprint,
                # so the static blueprint replaces that one in the viewer.
                observed = self._blueprint_named(agent.name, source="observed")
                entry = _RegisteredAgent(
                    agent, self._new_agent_id() if observed is None else observed.id, labels.get(id(agent))
                )
                self._agents[id(agent)] = entry
                added.append(entry)
        # Ids are allocated for the whole closure first, so delegation edges can point forward.
        for entry in added:
            blueprint = self._introspect(entry)
            previous = self._agent_blueprints.get(entry.id)
            self._emit_agent(blueprint if previous is None else carry_observed(previous, blueprint))
            if self.stream:
                _stream.attach(entry.agent)

    def _new_agent_id(self) -> str:
        self._agent_count += 1
        return f"a{self._agent_count}"

    def _blueprint_named(self, name: str | None, *, source: str | None = None) -> AgentBlueprint | None:
        if name is None:
            return None
        for blueprint in self._agent_blueprints.values():
            if blueprint.name == name and source in (None, blueprint.source):
                return blueprint
        return None

    def _introspect(self, entry: _RegisteredAgent) -> AgentBlueprint:
        def delegate_id(agent: AnyAgent) -> str | None:
            known = self._agents.get(id(agent))
            return None if known is None else known.id

        blueprint = introspect_agent(entry.agent, id=entry.id, label=entry.label, delegate_id=delegate_id)
        name = blueprint.name
        holder = self._blueprint_named(name)
        if name is not None and holder is not None and holder.id != blueprint.id:
            # Runs are matched to blueprints by agent name, so a name can belong to one blueprint only.
            warning = f"another agent named {name!r} is already registered; runs named {name!r} are shown on that one"
            blueprint = blueprint.model_copy(update={"name": None, "warnings": [*blueprint.warnings, warning]})
        return blueprint

    def _register_graph(self, graph: AnyGraph, label: str | None) -> str:
        known = self._graphs.get(id(graph))
        if known is not None:
            return known.id
        self._graph_count += 1
        entry = _RegisteredGraph(graph, f"g{self._graph_count}")
        self._graphs[id(graph)] = entry
        self._emit_graph(introspect_graph(graph, id=entry.id, label=label))
        return entry.id

    def _emit_agent(self, blueprint: AgentBlueprint) -> None:
        self._agent_blueprints[blueprint.id] = blueprint
        self._log.append({"type": "blueprint.agent", "agent": to_wire(blueprint)})

    def _emit_graph(self, blueprint: GraphBlueprint) -> None:
        self._log.append({"type": "blueprint.graph", "graph": to_wire(blueprint)})

    # --------------------------------------------------------------- spans

    def _span_started(self, normalized: Normalized) -> None:
        span = normalized.span
        with self._lock:
            # Blueprint changes go out before the span that caused them, so the
            # viewer can draw a run on its blueprint from the first event.
            if span.kind == "agent_run" and span.agent_name is not None:
                blueprint = self._blueprint_for_run(span.agent_name, normalized.agent_description, span.data.model_name)
                self._observe_delegation(span, blueprint)
            elif span.kind == "tool_call":
                self._open_tool_calls[span.span_id] = (span.agent_name, span.data.tool_name)
            elif span.kind == "model_request" and span.run_id is not None:
                self._open_requests[span.run_id] = _OpenRequest(span.trace_id, span.span_id, {})
            self._log.append({"type": "span.started", "span": to_wire(span)})

    def _span_ended(self, normalized: Normalized) -> None:
        span = normalized.span
        with self._lock:
            self._open_tool_calls.pop(span.span_id, None)
            run_id = span.run_id
            if run_id is not None and getattr(self._open_requests.get(run_id), "span_id", None) == span.span_id:
                del self._open_requests[run_id]
            parameters = normalized.request_parameters
            name = span.agent_name
            if (
                span.kind == "model_request"
                and name is not None
                and parameters is not None
                and name not in self._merged_requests
            ):
                # Rule 2, once per agent name: the request says which tools and output mode the model was given.
                self._merged_requests.add(name)
                blueprint = self._blueprint_for_run(name, None, span.data.model_name)
                merged = merge_request(
                    blueprint, parameters, instructions=span.data.instructions, provider=span.data.provider
                )
                if merged is not blueprint:
                    self._emit_agent(merged)
            self._log.append({"type": "span.ended", "span": to_wire(span)})

    def _blueprint_for_run(self, name: str, description: str | None, model_name: str | None) -> AgentBlueprint:
        """The blueprint that runs of the agent called ``name`` belong to (rule 1)."""
        blueprint = self._blueprint_named(name)
        if blueprint is not None:
            return blueprint
        for entry in self._agents.values():
            if entry.agent.name == name:
                # A registered agent whose blueprint does not carry this name yet. The usual case is an
                # agent created without a name: Pydantic AI infers one when it first runs.
                blueprint = self._introspect(entry)
                break
        else:
            blueprint = observed_blueprint(self._new_agent_id(), name, description=description, model_name=model_name)
        self._emit_agent(blueprint)
        return blueprint

    def _observe_delegation(self, span: WireSpan, blueprint: AgentBlueprint) -> None:
        """Rule 3: an agent run directly under a tool call means that tool delegates to that agent."""
        if span.parent_id is None:
            return
        owner_name, tool_name = self._open_tool_calls.get(span.parent_id, (None, ""))
        owner = self._blueprint_named(owner_name)
        if owner is not None:
            updated = add_delegate(owner, tool_name, blueprint.id)
            if updated is not owner:
                self._emit_agent(updated)

    def _stream_delta(self, run_id: str, delta: _stream.Delta) -> None:
        """Log a token delta against the model request its run has open; drop it if there is none."""
        with self._lock:
            request = self._open_requests.get(run_id)
            if request is None:
                return
            tool_name = delta.tool_name
            if delta.part_kind == "tool_call":
                if tool_name:
                    request.tool_names[delta.part_index] = tool_name
                else:
                    tool_name = request.tool_names.get(delta.part_index)
            # Lookup and append share the lock with the span callbacks, so a delta
            # always lands between its request's span.started and span.ended.
            self._log.append(
                {
                    "type": "stream.delta",
                    "trace_id": request.trace_id,
                    "span_id": request.span_id,
                    "part_index": delta.part_index,
                    "part_kind": delta.part_kind,
                    "tool_name": tool_name,
                    "delta": clip(delta.text),
                }
            )

    # ----------------------------------------------------------- lifecycle

    def close(self) -> None:
        """End the session: stop capturing and undo `Agent.instrument_all`. Safe to call twice.

        The events stay readable. Runs still in flight finish normally; what they emit
        after this point is not recorded.
        """
        with _sessions_lock:
            if self._closed:
                return
            self._closed = True
            if self in _open_sessions:
                _open_sessions.remove(self)
            if self._instrumentation is not None and _compat.instrumentation_default() in (None, self._instrumentation):
                # Put back what was there before us, unless someone has replaced our settings since.
                # If that fails, instrumentation stays on, which is harmless: the log is closed below.
                with contextlib.suppress(Exception):
                    Agent.instrument_all(self._previous_instrumentation)
        _stream.remove_sink(self._stream_delta)
        self._log.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()


def _default_title(targets: tuple[Target, ...]) -> str | None:
    for target in targets:
        name: Any = getattr(target, "__name__" if isinstance(target, ModuleType) else "name", None)
        if isinstance(name, str) and name:
            return name
    return None


def record(
    *targets: Target,
    include_content: bool = True,
    stream: bool = False,
    instrument: bool = True,
    title: str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> Session:
    """Capture agent runs in this process, without starting a server.

    Every agent run that happens while the session is open is recorded, whether or
    not the agent was passed here; passing it adds its full static blueprint.

    ```python
    import pydantic_ai_viz as pviz

    with pviz.record(agent) as session:
        agent.run_sync("Hello")
    print(len(session.events))
    ```

    Args:
        *targets: Agents, graphs, or modules whose module-level agents and graphs
            are all registered.
        include_content: Capture prompts, tool arguments and results. With `False`
            the structure of each run is kept and the content is `null`.
        stream: Capture token deltas. This switches model requests of the registered
            agents to streaming, so a model that cannot stream will fail.
        instrument: Call `Agent.instrument_all` with this session's own tracer
            provider. With `False` nothing global is touched; add
            `session.span_processor` to your own provider instead.
        title: Session title. Defaults to the name of the first named target.
        max_events: Size of the event log before whole completed runs are dropped.

    Returns:
        The open session.

    Raises:
        RuntimeError: If `instrument` is true and another instrumenting session is open.
        TypeError: If a target is not an agent, a graph or a module.
    """
    session = Session(
        title=title or _default_title(targets),
        include_content=include_content,
        stream=stream,
        instrument=instrument,
        max_events=max_events,
    )
    try:
        session.register(*targets)
    except BaseException:
        session.close()
        raise
    return session
