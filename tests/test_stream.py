from __future__ import annotations

import json
from typing import Any

import pytest
from inline_snapshot import snapshot
from pydantic_ai import Agent
from pydantic_ai.messages import (
    FinalResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from scenarios import PROMPT, Deps, build_team

from pydantic_ai_viz import _stream, record
from pydantic_ai_viz._models import parse_envelope
from pydantic_ai_viz._stream import Delta, delta_of

pytestmark = pytest.mark.anyio


def deltas_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for envelope in events:
        parse_envelope(json.dumps(envelope))
    return [envelope for envelope in events if envelope["type"] == "stream.delta"]


def spans_by_id(events: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {e["span"]["span_id"]: e for e in events if e["type"] == kind}


def assembled(deltas: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    """Text per ``(span_id, part_index)``, the way the viewer puts a streaming response together."""
    out: dict[tuple[str, int], str] = {}
    for delta in deltas:
        key = (delta["span_id"], delta["part_index"])
        out[key] = out.get(key, "") + delta["delta"]
    return out


async def test_deltas_belong_to_the_open_model_request_of_their_run() -> None:
    team = build_team()
    with record(team.main, stream=True) as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events
    assert events[0]["stream"] is True
    deltas = deltas_of(events)
    started = spans_by_id(events, "span.started")
    ended = spans_by_id(events, "span.ended")
    assert deltas
    for delta in deltas:
        span = started[delta["span_id"]]["span"]
        assert span["kind"] == "model_request"
        assert span["trace_id"] == delta["trace_id"]
        # Between its request's span.started and span.ended, as the wire contract promises.
        assert started[delta["span_id"]]["seq"] < delta["seq"] < ended[delta["span_id"]]["seq"]

    # What the deltas add up to is exactly what the finished request reports.
    text = assembled(deltas)
    checked = 0
    for span_id, envelope in ended.items():
        if envelope["span"]["kind"] != "model_request":
            continue
        (message,) = envelope["span"]["data"]["output_messages"]
        for index, part in enumerate(message["parts"]):
            if part["type"] == "text":
                assert text[span_id, index] == part["content"]
            else:
                assert json.loads(text[span_id, index]) == part["arguments"]
            checked += 1
    assert checked == len(text) == 9  # main: 1 text part and 6 tool calls; researcher: 1 tool call and 1 text part


async def test_first_request_of_the_main_agent_delta_by_delta() -> None:
    team = build_team()
    with record(team.main, stream=True) as session:
        await team.main.run(PROMPT, deps=Deps())
        deltas = deltas_of(session.events)
    first_request = deltas[0]["span_id"]
    assert [
        (d["part_index"], d["part_kind"], d["tool_name"], d["delta"]) for d in deltas if d["span_id"] == first_request
    ] == snapshot(
        [
            (0, "text", None, "Let "),
            (0, "text", None, "me "),
            (0, "text", None, "check."),
            (1, "tool_call", "get_weather", '{"city'),
            (1, "tool_call", "get_weather", '": "Paris"}'),
            (2, "tool_call", "flaky", '{"x": '),
            (2, "tool_call", "flaky", "1}"),
            (3, "tool_call", "delegate_research", '{"topi'),
            (3, "tool_call", "delegate_research", 'c": "Paris"}'),
        ]
    )


async def test_sub_agent_deltas_attach_to_the_sub_agents_request_span() -> None:
    team = build_team()
    with record(team.main, stream=True) as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events
    started = spans_by_id(events, "span.started")
    owners: dict[str, list[str]] = {}
    for delta in deltas_of(events):
        owners.setdefault(started[delta["span_id"]]["span"]["agent_name"], []).append(delta["delta"])
    assert set(owners) == {"main", "researcher"}
    assert "".join(owners["researcher"]) == snapshot('{"topic": "a"}research notes')
    assert "research notes" not in "".join(owners["main"])
    # Two different runs, two different run ids: that is what keeps them apart.
    run_ids = {started[d["span_id"]]["span"]["run_id"] for d in deltas_of(events)}
    assert len(run_ids) == 2


async def test_no_deltas_and_no_forced_streaming_without_stream() -> None:
    team = build_team()
    with record(team.main) as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events
    assert events[0]["stream"] is False
    assert deltas_of(events) == []
    requests = [e["span"] for e in events if e["type"] == "span.ended" and e["span"]["kind"] == "model_request"]
    assert len(requests) == 5
    assert not any("gen_ai.client.operation.time_to_first_chunk" in span["attributes"] for span in requests)


async def test_stream_true_would_break_a_model_that_cannot_stream() -> None:
    """The documented caveat, pinned: this is why ``stream`` defaults to off."""
    from pydantic_ai.messages import ModelResponse

    agent = Agent(FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")])), name="plain")
    with record(agent, stream=True), pytest.raises(AssertionError, match="stream_function"):
        await agent.run("hi")


async def test_listener_is_attached_once_per_agent_across_sessions() -> None:
    agent = Agent(TestModel(custom_output_text="one two"), name="steady")
    with record(agent, stream=True) as first:
        first.register(agent)
        await agent.run("hi")
        expected = [d["delta"] for d in deltas_of(first.events)]
    with record(agent, stream=True) as second:
        await agent.run("hi")
        assert [d["delta"] for d in deltas_of(second.events)] == expected  # not doubled
    assert expected == snapshot(["", "one ", "two"])
    assert len(deltas_of(first.events)) == len(expected)  # the closed session got nothing more


async def test_listener_is_silent_once_no_streaming_session_is_open() -> None:
    agent = Agent(TestModel(custom_output_text="one two"), name="steady")
    with record(agent, stream=True):
        pass
    with record(agent) as session:  # the listener is still on the agent, but nobody wants deltas
        await agent.run("hi")
        assert deltas_of(session.events) == []


async def test_stream_is_off_when_content_capture_is_off() -> None:
    """A token delta is content; with ``include_content=False`` none may reach the log."""
    # A model that cannot stream proves the request was not switched to streaming either.
    from pydantic_ai.messages import ModelResponse

    agent = Agent(FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("a secret")])), name="private")
    with record(agent, stream=True, include_content=False) as session:
        await agent.run("hi")
        events = session.events
    assert session.stream is False
    assert events[0]["stream"] is False
    assert deltas_of(events) == []
    assert "a secret" not in json.dumps(events)


async def test_unregistered_agents_have_no_listener_and_so_no_deltas() -> None:
    team = build_team()
    with record(team.researcher, stream=True) as session:
        await team.main.run(PROMPT, deps=Deps())  # main was never registered
        events = session.events
    started = spans_by_id(events, "span.started")
    assert {started[d["span_id"]]["span"]["agent_name"] for d in deltas_of(events)} == {"researcher"}


async def test_a_failing_sink_never_reaches_the_run() -> None:
    def broken(run_id: str, delta: Delta) -> None:
        raise RuntimeError("viewer bug")

    agent = Agent(TestModel(custom_output_text="fine"), name="steady")
    _stream.attach(agent)
    _stream.add_sink(broken)
    try:
        assert (await agent.run("hi")).output == "fine"
    finally:
        _stream.remove_sink(broken)
        _stream.remove_sink(broken)  # removing twice is fine


def test_delta_of_every_event_kind() -> None:
    assert delta_of(PartStartEvent(index=0, part=TextPart("Let "))) == Delta(0, "text", None, "Let ")
    assert delta_of(PartStartEvent(index=1, part=ThinkingPart("hm"))) == Delta(1, "thinking", None, "hm")
    assert delta_of(PartStartEvent(index=2, part=ToolCallPart("f", '{"a": '))) == Delta(2, "tool_call", "f", '{"a": ')
    assert delta_of(PartStartEvent(index=2, part=ToolCallPart("f", {"a": 1}))) == Delta(2, "tool_call", "f", '{"a": 1}')
    assert delta_of(PartStartEvent(index=2, part=ToolCallPart("f"))) == Delta(2, "tool_call", "f", "")
    assert delta_of(PartDeltaEvent(index=0, delta=TextPartDelta("me "))) == Delta(0, "text", None, "me ")
    assert delta_of(PartDeltaEvent(index=1, delta=ThinkingPartDelta(content_delta="m"))) == Delta(
        1, "thinking", None, "m"
    )
    assert delta_of(PartDeltaEvent(index=1, delta=ThinkingPartDelta(signature_delta="sig"))) is None
    assert delta_of(PartDeltaEvent(index=2, delta=ToolCallPartDelta(args_delta="1}"))) == Delta(
        2, "tool_call", None, "1}"
    )
    assert delta_of(PartDeltaEvent(index=2, delta=ToolCallPartDelta(args_delta={"b": 2}))) == Delta(
        2, "tool_call", None, '{"b": 2}'
    )
    assert delta_of(PartEndEvent(index=0, part=TextPart("Let me "))) is None
    assert delta_of(FinalResultEvent(tool_name=None, tool_call_id=None)) is None
    assert delta_of("not an event") is None


def test_deltas_for_a_run_without_an_open_request_are_dropped() -> None:
    with record(stream=True) as session:
        session._stream_delta("no-such-run", Delta(0, "text", None, "lost"))  # pyright: ignore[reportPrivateUsage]
        assert deltas_of(session.events) == []


async def test_attach_on_creation_listens_to_agents_made_inside_the_block_only() -> None:
    """What `pydantic-ai-viz run --stream` relies on: the first run of an unregistered agent streams."""
    original = Agent.__init__
    with record(stream=True) as session:
        before = Agent(TestModel(), name="before")
        with _stream.attach_on_creation():
            assert Agent.__init__ is not original
            inside = Agent(TestModel(), name="inside")
        assert Agent.__init__ is original
        after = Agent(TestModel(), name="after")
        for agent in (before, inside, after):
            await agent.run("Hello")
        events = session.events
    requests = {
        e["span"]["span_id"]: e["span"]["agent_name"]
        for e in events
        if e["type"] == "span.started" and e["span"]["kind"] == "model_request"
    }
    assert sorted(requests.values()) == ["after", "before", "inside"]
    assert {requests[e["span_id"]] for e in events if e["type"] == "stream.delta"} == {"inside"}


def test_attach_on_creation_restores_the_constructor_after_an_error_and_never_breaks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Agent.__init__

    def broken(agent: Agent[Any, Any]) -> None:
        raise RuntimeError("listener could not be attached")

    monkeypatch.setattr(_stream, "attach", broken)
    with pytest.raises(ZeroDivisionError), _stream.attach_on_creation():  # noqa: PT012
        agent = Agent(TestModel(), name="still-built")
        assert agent.name == "still-built"
        raise ZeroDivisionError
    assert Agent.__init__ is original
