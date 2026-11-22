from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, CapabilityEvent, CustomEvent, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from pydantic_ai_viz import AgentBlueprint, CaptureEvents, analyze, blueprint, record
from pydantic_ai_viz._normalize import normalize


@dataclass(kw_only=True)
class Ping(CapabilityEvent, namespace="probe"):
    n: int = 0


@dataclass(kw_only=True)
class Planned(CapabilityEvent, namespace="probe", dispatch="immediate"):
    steps: list[str] = field(default_factory=list[str])


@dataclass(kw_only=True)
class Progress(CustomEvent):
    done: int = 0


@dataclass
class Emitter(AbstractCapability[Any]):
    """Emits a ``probe.ping`` before each model request and gives a ``plan`` tool that emits ``probe.planned``."""

    async def before_model_request(self, ctx: RunContext[Any], request_context: Any) -> Any:
        await ctx.emit(Ping(n=1))
        return request_context

    def get_toolset(self) -> FunctionToolset[Any]:
        async def plan(ctx: RunContext[Any]) -> str:
            await ctx.emit(Planned(steps=["read", "write"]))
            return "planned"

        return FunctionToolset([plan])


@dataclass
class _Context:
    trace_include_content: bool


def _record_one(event: Any, *, include_content: bool = True) -> ReadableSpan:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("run"):
        asyncio.run(CaptureEvents().on_event(_Context(include_content), event=event))
    [span] = exporter.get_finished_spans()
    return span


def test_a_capability_event_becomes_a_span_event() -> None:
    span = _record_one(Ping(n=3, capability_id="emitter"))
    [event] = span.events
    assert event.name == "pydantic_ai_viz.event"
    assert dict(event.attributes or {}) == {
        "kind": "probe.ping",
        "event_kind": "capability",
        "capability_id": "emitter",
        "data": '{"n":3}',
    }
    [wire] = normalize(span, ended=True).span.events
    assert wire.model_dump(exclude={"ts_ms"}) == {
        "kind": "probe.ping",
        "event_kind": "capability",
        "capability_id": "emitter",
        "tool_call_id": None,
        "tool_name": None,
        "data": {"n": 3},
    }
    assert wire.ts_ms == (event.timestamp or 0) / 1_000_000


def test_a_custom_event_is_named_and_its_data_left_out_without_content() -> None:
    span = _record_one(Progress(done=2, tool_call_id="c1", tool_name="t"), include_content=False)
    [event] = span.events
    assert dict(event.attributes or {}) == {
        "kind": "progress",
        "event_kind": "custom",
        "tool_call_id": "c1",
        "tool_name": "t",
    }
    assert normalize(span, ended=True).span.events[0].data is None


def test_other_stream_events_and_failures_are_ignored() -> None:
    assert _record_one("not an event").events == ()

    class Broken:
        @property
        def trace_include_content(self) -> bool:
            raise RuntimeError("boom")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("run"):
        asyncio.run(CaptureEvents().on_event(Broken(), event=Ping()))  # must not raise


def test_an_otel_event_of_another_name_is_not_a_run_event() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("run") as span:
        span.add_event("something.else", {"kind": "x"})
    [ended] = exporter.get_finished_spans()
    assert normalize(ended, ended=True).span.events == []


def _agent() -> Agent[Any, str]:
    agent = Agent(TestModel(), name="probe", capabilities=[Emitter(id="emitter"), CaptureEvents()])

    @agent.tool
    async def report(ctx: RunContext[Any]) -> str:  # pyright: ignore[reportUnusedFunction]
        await ctx.emit(Progress(done=1))
        return "reported"

    return agent


def _ended_events(include_content: bool) -> dict[str, list[dict[str, Any]]]:
    agent = _agent()
    with record(agent, include_content=include_content) as session:
        agent.run_sync("hi")
    found: dict[str, list[dict[str, Any]]] = {}
    for event in session.events:
        if event["type"] == "span.started":
            assert event["span"]["events"] == []
        if event["type"] == "span.ended" and event["span"]["events"]:
            span = event["span"]
            found[f"{span['kind']} {span['name']}"] = [
                {key: value for key, value in e.items() if key != "ts_ms"} for e in span["events"]
            ]
    return found


def test_a_recorded_run_carries_its_events_on_the_spans_they_were_emitted_in() -> None:
    found = _ended_events(include_content=True)
    assert found["tool_call plan"] == [
        {
            "kind": "probe.planned",
            "event_kind": "capability",
            "capability_id": "emitter",
            "tool_call_id": "pyd_ai_tool_call_id__plan",
            "tool_name": "plan",
            "data": {"steps": ["read", "write"]},
        }
    ]
    run = found["agent_run probe"]
    assert [e["kind"] for e in run] == ["probe.ping", "progress", "probe.ping"]
    # Dispatched at its stream position, after the tool returned: on the run, with the tool's call id.
    assert run[1] == {
        "kind": "progress",
        "event_kind": "custom",
        "capability_id": None,
        "tool_call_id": "pyd_ai_tool_call_id__report",
        "tool_name": "report",
        "data": {"done": 1},
    }


def test_without_content_the_events_carry_no_data() -> None:
    found = _ended_events(include_content=False)
    assert {e["kind"] for events in found.values() for e in events} == {"probe.ping", "probe.planned", "progress"}
    assert all(e["data"] is None for events in found.values() for e in events)


def test_capture_events_is_no_part_of_the_blueprint_live_or_from_source(tmp_path: Path) -> None:
    live = blueprint(_agent())
    assert isinstance(live, AgentBlueprint)
    assert [c.name for c in live.capabilities if not c.builtin] == ["Emitter"]
    (tmp_path / "app.py").write_text(
        textwrap.dedent(
            """
            import pydantic_ai_viz as pviz
            from pydantic_ai import Agent
            from pydantic_ai_viz import CaptureEvents

            one = Agent("test", name="one", capabilities=[CaptureEvents()])
            two = Agent("test", name="two", capabilities=[pviz.CaptureEvents()])
            """
        ),
        encoding="utf-8",
    )
    analysis = analyze(tmp_path)
    assert [(b.name, b.capabilities, b.warnings) for b in analysis.agents] == [("one", [], []), ("two", [], [])]
