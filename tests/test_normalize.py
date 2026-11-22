from __future__ import annotations

import json
from typing import Any, Literal

import pytest
from inline_snapshot import snapshot
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.trace import Status, StatusCode
from pydantic_ai import Agent, InstrumentationSettings
from pydantic_ai.models.test import TestModel
from scenarios import PROMPT, Deps, build_team, drain, scrub

from pydantic_ai_viz._models import MAX_STRING_CHARS, SPAN_ADAPTER, to_wire
from pydantic_ai_viz._normalize import Normalized, clip, instructions, messages, normalize, parse_jsonish


class Collector(SpanProcessor):
    """Normalises every span at start and at end, the way the capture processor does."""

    def __init__(self) -> None:
        self.started: list[Normalized] = []
        self.ended: dict[str, Normalized] = {}
        self.raw: list[ReadableSpan] = []

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        self.started.append(normalize(span, ended=False))

    def on_end(self, span: ReadableSpan) -> None:
        self.raw.append(span)
        result = normalize(span, ended=True)
        self.ended[result.span.span_id] = result

    def spans(self) -> list[dict[str, Any]]:
        """Ended spans as wire dicts, in start order (the end order of parallel tools is not stable)."""
        out: list[dict[str, Any]] = []
        for start in self.started:
            wire = to_wire(self.ended[start.span.span_id].span)
            assert to_wire(SPAN_ADAPTER.validate_python(wire)) == wire  # closed wire model, every field present
            out.append(wire)
        return out


async def capture(*, version: Literal[5, 6] = 5, stream: bool = False, include_content: bool = True) -> Collector:
    collector = Collector()
    provider = TracerProvider()
    provider.add_span_processor(collector)
    settings = InstrumentationSettings(tracer_provider=provider, version=version, include_content=include_content)
    team = build_team(settings)
    result = await team.main.run(PROMPT, deps=Deps(), event_stream_handler=drain if stream else None)
    assert result.output.city == "Paris"
    return collector


def tree(spans: list[dict[str, Any]]) -> list[str]:
    """``kind name status < parent name`` per span."""
    names = {span["span_id"]: span["name"] for span in spans}
    return [f"{s['kind']} {s['name']} {s['status']} < {names.get(s['parent_id'])}" for s in spans]


def first_by_name(spans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{span['kind']} {span['name']}": span for span in reversed(spans)}


pytestmark = pytest.mark.anyio


async def test_span_tree_of_a_real_run() -> None:
    collector = await capture()
    assert tree(collector.spans()) == snapshot(
        [
            "agent_run main ok < None",
            "model_request scripted-fn ok < main",
            "tool_call get_weather ok < main",
            "tool_call flaky error < main",
            "tool_call delegate_research ok < main",
            "agent_run researcher ok < delegate_research",
            "model_request test ok < researcher",
            "tool_call lookup ok < researcher",
            "model_request test ok < researcher",
            "model_request scripted-fn ok < main",
            "tool_call flaky ok < main",
            "tool_call add ok < main",
            "model_request scripted-fn ok < main",
        ]
    )


async def test_golden_spans_v5() -> None:
    collector = await capture()
    spans = first_by_name(scrub(collector.spans()))
    assert spans["agent_run main"] == snapshot(
        {
            "trace_id": "<id 1>",
            "span_id": "<id 2>",
            "parent_id": None,
            "name": "main",
            "start_ms": 0.0,
            "end_ms": 0.0,
            "status": "ok",
            "agent_name": "main",
            "run_id": "<id 3>",
            "error": None,
            "attributes": {
                "gen_ai.operation.name": "invoke_agent",
                "logfire.msg": "main run",
                "gen_ai.agent.description": "Main demo agent",
            },
            "kind": "agent_run",
            "data": {
                "model_name": "scripted-fn",
                "conversation_id": "<id 4>",
                "instructions": """\
Static instructions.

User is ann.\
""",
                "output": {"city": "Paris", "temp_c": 21.5},
                "usage": {"input_tokens": 203, "output_tokens": 81},
                "messages": [
                    {"role": "system", "parts": [{"type": "text", "content": "Static system prompt."}]},
                    {"role": "user", "parts": [{"type": "text", "content": "Weather in Paris?"}]},
                    {
                        "role": "assistant",
                        "parts": [
                            {"type": "text", "content": "Let me check."},
                            {"type": "tool_call", "id": "c1", "name": "get_weather", "arguments": {"city": "Paris"}},
                            {"type": "tool_call", "id": "c2", "name": "flaky", "arguments": {"x": 1}},
                            {
                                "type": "tool_call",
                                "id": "c3",
                                "name": "delegate_research",
                                "arguments": {"topic": "Paris"},
                            },
                        ],
                    },
                    {
                        "role": "tool",
                        "parts": [
                            {
                                "type": "tool_result",
                                "id": "c1",
                                "name": "get_weather",
                                "result": {"city": "Paris", "temp_c": 21.5},
                            },
                            {"type": "tool_result", "id": "c2", "name": "flaky", "result": "x must be 2"},
                            {
                                "type": "tool_result",
                                "id": "c3",
                                "name": "delegate_research",
                                "result": "research notes",
                            },
                        ],
                    },
                    {
                        "role": "assistant",
                        "parts": [
                            {"type": "tool_call", "id": "c4", "name": "flaky", "arguments": {"x": 2}},
                            {"type": "tool_call", "id": "c5", "name": "add", "arguments": {"a": 1, "b": 2}},
                        ],
                    },
                    {
                        "role": "tool",
                        "parts": [
                            {"type": "tool_result", "id": "c4", "name": "flaky", "result": "ok"},
                            {"type": "tool_result", "id": "c5", "name": "add", "result": 3},
                        ],
                    },
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "id": "c6",
                                "name": "final_result",
                                "arguments": {"city": "Paris", "temp_c": 21.5},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "parts": [
                            {
                                "type": "tool_result",
                                "id": "c6",
                                "name": "final_result",
                                "result": "Final result processed.",
                            }
                        ],
                    },
                ],
            },
        }
    )
    assert spans["model_request scripted-fn"] == snapshot(
        {
            "trace_id": "<id 1>",
            "span_id": "<id 5>",
            "parent_id": "<id 2>",
            "name": "scripted-fn",
            "start_ms": 0.0,
            "end_ms": 0.0,
            "status": "ok",
            "agent_name": "main",
            "run_id": "<id 3>",
            "error": None,
            "attributes": {"gen_ai.operation.name": "chat", "gen_ai.conversation.id": "<id 4>"},
            "kind": "model_request",
            "data": {
                "model_name": "scripted-fn",
                "provider": "function",
                "response_model": "scripted-fn",
                "settings": {"temperature": 0.2},
                "instructions": """\
Static instructions.

User is ann.\
""",
                "input_messages": [
                    {"role": "system", "parts": [{"type": "text", "content": "Static system prompt."}]},
                    {"role": "user", "parts": [{"type": "text", "content": "Weather in Paris?"}]},
                ],
                "output_messages": [
                    {
                        "role": "assistant",
                        "parts": [
                            {"type": "text", "content": "Let me check."},
                            {"type": "tool_call", "id": "c1", "name": "get_weather", "arguments": {"city": "Paris"}},
                            {"type": "tool_call", "id": "c2", "name": "flaky", "arguments": {"x": 1}},
                            {
                                "type": "tool_call",
                                "id": "c3",
                                "name": "delegate_research",
                                "arguments": {"topic": "Paris"},
                            },
                        ],
                    }
                ],
                "usage": {"input_tokens": 57, "output_tokens": 18},
            },
        }
    )
    assert spans["tool_call get_weather"] == snapshot(
        {
            "trace_id": "<id 1>",
            "span_id": "<id 6>",
            "parent_id": "<id 2>",
            "name": "get_weather",
            "start_ms": 0.0,
            "end_ms": 0.0,
            "status": "ok",
            "agent_name": "main",
            "run_id": "<id 3>",
            "error": None,
            "attributes": {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.conversation.id": "<id 4>",
                "logfire.msg": "running tool: get_weather",
            },
            "kind": "tool_call",
            "data": {
                "tool_name": "get_weather",
                "call_id": "c1",
                "arguments": {"city": "Paris"},
                "result": {"city": "Paris", "temp_c": 21.5},
                "retry": None,
            },
        }
    )
    assert spans["tool_call flaky"] == snapshot(
        {
            "trace_id": "<id 1>",
            "span_id": "<id 7>",
            "parent_id": "<id 2>",
            "name": "flaky",
            "start_ms": 0.0,
            "end_ms": 0.0,
            "status": "error",
            "agent_name": "main",
            "run_id": "<id 3>",
            "error": {
                "type": "pydantic_ai.exceptions.ToolRetryError",
                "message": "x must be 2",
                "stacktrace": None,
            },
            "attributes": {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.conversation.id": "<id 4>",
                "logfire.msg": "running tool: flaky",
            },
            "kind": "tool_call",
            "data": {
                "tool_name": "flaky",
                "call_id": "c2",
                "arguments": {"x": 1},
                "result": "x must be 2",
                "retry": {"cause": "model_retry", "attempt": 1, "max_retries": 2},
            },
        }
    )
    assert spans["agent_run researcher"] == snapshot(
        {
            "trace_id": "<id 1>",
            "span_id": "<id 9>",
            "parent_id": "<id 8>",
            "name": "researcher",
            "start_ms": 0.0,
            "end_ms": 0.0,
            "status": "ok",
            "agent_name": "researcher",
            "run_id": "<id 10>",
            "error": None,
            "attributes": {
                "gen_ai.operation.name": "invoke_agent",
                "logfire.msg": "researcher run",
                "gen_ai.agent.description": "Looks things up",
            },
            "kind": "agent_run",
            "data": {
                "model_name": "test",
                "conversation_id": "<id 11>",
                "instructions": "You research.",
                "output": "research notes",
                "usage": {"input_tokens": 107, "output_tokens": 12},
                "messages": [
                    {"role": "user", "parts": [{"type": "text", "content": "research Paris"}]},
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "id": "pyd_ai_tool_call_id__lookup",
                                "name": "lookup",
                                "arguments": {"topic": "a"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "parts": [
                            {
                                "type": "tool_result",
                                "id": "pyd_ai_tool_call_id__lookup",
                                "name": "lookup",
                                "result": "facts about a",
                            }
                        ],
                    },
                    {"role": "assistant", "parts": [{"type": "text", "content": "research notes"}]},
                ],
            },
        }
    )


async def test_span_started_carries_only_what_is_known_at_start() -> None:
    collector = await capture()
    started = scrub([to_wire(item.span) for item in collector.started])
    assert started[:3] == snapshot(
        [
            {
                "trace_id": "<id 1>",
                "span_id": "<id 2>",
                "parent_id": None,
                "name": "main",
                "start_ms": 0.0,
                "end_ms": None,
                "status": "running",
                "agent_name": "main",
                "run_id": "<id 3>",
                "error": None,
                "attributes": {},
                "kind": "agent_run",
                "data": {
                    "model_name": "scripted-fn",
                    "conversation_id": "<id 4>",
                    "instructions": None,
                    "output": None,
                    "usage": None,
                    "messages": None,
                },
            },
            {
                "trace_id": "<id 1>",
                "span_id": "<id 5>",
                "parent_id": "<id 2>",
                "name": "scripted-fn",
                "start_ms": 0.0,
                "end_ms": None,
                "status": "running",
                "agent_name": "main",
                "run_id": "<id 3>",
                "error": None,
                "attributes": {},
                "kind": "model_request",
                "data": {
                    "model_name": "scripted-fn",
                    "provider": None,
                    "response_model": None,
                    "settings": {},
                    "instructions": None,
                    "input_messages": None,
                    "output_messages": None,
                    "usage": None,
                },
            },
            {
                "trace_id": "<id 1>",
                "span_id": "<id 6>",
                "parent_id": "<id 2>",
                "name": "get_weather",
                "start_ms": 0.0,
                "end_ms": None,
                "status": "running",
                "agent_name": "main",
                "run_id": "<id 3>",
                "error": None,
                "attributes": {},
                "kind": "tool_call",
                "data": {
                    "tool_name": "get_weather",
                    "call_id": "c1",
                    "arguments": {"city": "Paris"},
                    "result": None,
                    "retry": None,
                },
            },
        ]
    )
    for span in started:
        assert span["status"] == "running"
        assert span["end_ms"] is None
        assert span["error"] is None
        assert span["attributes"] == {}


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_v5_and_v6_normalise_to_the_same_spans(stream: bool) -> None:
    v5 = await capture(version=5, stream=stream)
    v6 = await capture(version=6, stream=stream)
    assert scrub(v5.spans()) == scrub(v6.spans())

    # The premise: the two versions do differ in the raw attribute...
    def raw_roles(collector: Collector) -> set[str]:
        (run,) = (span for span in collector.raw if span.name == "invoke_agent main")
        assert run.attributes is not None
        return {message["role"] for message in json.loads(str(run.attributes["pydantic_ai.all_messages"]))}

    assert raw_roles(v5) == {"system", "user", "assistant"}
    assert raw_roles(v6) == {"system", "user", "assistant", "tool"}
    # ...and the wire says "tool" for both.
    assert [message["role"] for message in v5.spans()[0]["data"]["messages"]] == snapshot(
        ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"]
    )


def _without_stream_only_detail(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop what a streamed request reports differently: its estimated usage and a timing attribute."""
    for span in spans:
        span["attributes"].pop("gen_ai.client.operation.time_to_first_chunk", None)
        if span["kind"] in ("agent_run", "model_request"):
            assert span["data"]["usage"]["input_tokens"] > 0
            span["data"]["usage"] = None
    return spans


@pytest.mark.parametrize("version", [5, 6])
async def test_streamed_and_plain_runs_normalise_to_the_same_spans(version: Literal[5, 6]) -> None:
    plain = await capture(version=version, stream=False)
    streamed = await capture(version=version, stream=True)
    assert _without_stream_only_detail(scrub(streamed.spans())) == _without_stream_only_detail(scrub(plain.spans()))


async def test_tool_arguments_parse_from_a_string_and_from_an_object() -> None:
    plain = await capture(stream=False)
    streamed = await capture(stream=True)

    def raw_output(collector: Collector) -> str:
        request = next(span for span in collector.raw if span.name == "chat scripted-fn")
        assert request.attributes is not None
        return str(request.attributes["gen_ai.output.messages"])

    # The premise: a plain run reports an object, a streamed run the raw JSON string.
    assert '"arguments":{"city":"Paris"}' in raw_output(plain)
    assert '"arguments":"{\\"city\\": \\"Paris\\"}"' in raw_output(streamed)
    for collector in (plain, streamed):
        request = collector.spans()[1]
        calls = [part for part in request["data"]["output_messages"][0]["parts"] if part["type"] == "tool_call"]
        assert [call["arguments"] for call in calls] == [{"city": "Paris"}, {"x": 1}, {"topic": "Paris"}]
        assert collector.spans()[2]["data"]["arguments"] == {"city": "Paris"}


async def test_retry_span_has_retry_and_error() -> None:
    collector = await capture()
    first, second = (span for span in collector.spans() if span["name"] == "flaky")
    assert first["status"] == "error"
    assert first["data"]["retry"] == {"cause": "model_retry", "attempt": 1, "max_retries": 2}
    assert first["data"]["result"] == "x must be 2"
    assert first["error"]["type"] == "pydantic_ai.exceptions.ToolRetryError"
    assert first["error"]["message"] == "x must be 2"
    assert first["error"]["stacktrace"] is None  # an expected retry: its message is what the model was told
    assert second["status"] == "ok"
    assert second["data"]["retry"] is None
    assert second["error"] is None
    assert second["data"]["result"] == "ok"


async def test_include_content_false_keeps_structure_and_nulls_content() -> None:
    full = await capture()
    redacted = await capture(include_content=False)
    assert tree(redacted.spans()) == tree(full.spans())
    spans = first_by_name(scrub(redacted.spans()))
    assert spans["agent_run main"]["data"] == snapshot(
        {
            "model_name": "scripted-fn",
            "conversation_id": "<id 4>",
            "instructions": None,
            "output": None,
            "usage": {"input_tokens": 203, "output_tokens": 81},
            "messages": [
                {"role": "system", "parts": [{"type": "text", "content": None}]},
                {"role": "user", "parts": [{"type": "text", "content": None}]},
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "content": None},
                        {"type": "tool_call", "id": "c1", "name": "get_weather", "arguments": None},
                        {"type": "tool_call", "id": "c2", "name": "flaky", "arguments": None},
                        {"type": "tool_call", "id": "c3", "name": "delegate_research", "arguments": None},
                    ],
                },
                {
                    "role": "tool",
                    "parts": [
                        {"type": "tool_result", "id": "c1", "name": "get_weather", "result": None},
                        {"type": "tool_result", "id": "c2", "name": "flaky", "result": None},
                        {"type": "tool_result", "id": "c3", "name": "delegate_research", "result": None},
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "tool_call", "id": "c4", "name": "flaky", "arguments": None},
                        {"type": "tool_call", "id": "c5", "name": "add", "arguments": None},
                    ],
                },
                {
                    "role": "tool",
                    "parts": [
                        {"type": "tool_result", "id": "c4", "name": "flaky", "result": None},
                        {"type": "tool_result", "id": "c5", "name": "add", "result": None},
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [{"type": "tool_call", "id": "c6", "name": "final_result", "arguments": None}],
                },
                {
                    "role": "tool",
                    "parts": [{"type": "tool_result", "id": "c6", "name": "final_result", "result": None}],
                },
            ],
        }
    )
    assert spans["model_request scripted-fn"]["data"] == snapshot(
        {
            "model_name": "scripted-fn",
            "provider": "function",
            "response_model": "scripted-fn",
            "settings": {"temperature": 0.2},
            "instructions": None,
            "input_messages": [
                {"role": "system", "parts": [{"type": "text", "content": None}]},
                {"role": "user", "parts": [{"type": "text", "content": None}]},
            ],
            "output_messages": [
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "content": None},
                        {"type": "tool_call", "id": "c1", "name": "get_weather", "arguments": None},
                        {"type": "tool_call", "id": "c2", "name": "flaky", "arguments": None},
                        {"type": "tool_call", "id": "c3", "name": "delegate_research", "arguments": None},
                    ],
                }
            ],
            "usage": {"input_tokens": 57, "output_tokens": 18},
        }
    )
    assert spans["tool_call flaky"]["data"] == snapshot(
        {
            "tool_name": "flaky",
            "call_id": "c2",
            "arguments": None,
            "result": None,
            "retry": {"cause": "model_retry", "attempt": 1, "max_retries": 2},
        }
    )
    assert spans["tool_call flaky"]["error"] == snapshot(
        {"type": "pydantic_ai.exceptions.ToolRetryError", "message": None, "stacktrace": None}
    )


async def test_failed_run_has_error_status_and_exception_details() -> None:
    collector = Collector()
    provider = TracerProvider()
    provider.add_span_processor(collector)
    agent = Agent(TestModel(), name="bad")
    agent.instrument = InstrumentationSettings(tracer_provider=provider, version=5)

    @agent.tool_plain
    def boom(x: int) -> str:  # pyright: ignore[reportUnusedFunction]
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        await agent.run("go")
    spans = collector.spans()
    assert tree(spans) == snapshot(
        ["agent_run bad error < None", "model_request test ok < bad", "tool_call boom error < bad"]
    )
    run, _, tool = spans
    assert tool["data"]["retry"] is None  # a real error, not a retry
    assert tool["error"]["type"] == "ValueError"
    assert tool["error"]["stacktrace"].startswith("Traceback")  # a real error keeps where it was raised
    assert run["error"]["type"] == "ValueError"
    assert run["error"]["message"] == "kaboom"
    assert run["data"]["output"] is None


# ------------------------------------------------- synthetic spans and values


def _span(name: str, attributes: dict[str, Any], *, status: Status | None = None) -> ReadableSpan:
    span = TracerProvider().get_tracer("test").start_span(name, attributes=attributes)
    if status is not None:
        span.set_status(status)
    span.end()
    assert isinstance(span, ReadableSpan)
    return span


def test_unknown_span_degrades_to_other_with_raw_attributes() -> None:
    span = _span("db query", {"db.system": "sqlite", "rows": 3, "tags": ("a", "b"), "agent_name": "legacy"})
    wire = to_wire(normalize(span, ended=True).span)
    assert wire["kind"] == "other"
    assert wire["data"] is None
    assert wire["name"] == "db query"
    assert wire["parent_id"] is None
    assert wire["agent_name"] == "legacy"  # the pre-gen_ai key is the fallback
    assert wire["attributes"] == {"db.system": "sqlite", "rows": 3, "tags": ["a", "b"]}
    assert wire["end_ms"] >= wire["start_ms"] > 1_600_000_000_000  # epoch milliseconds


def _failed(attributes: dict[str, Any], exception_type: str) -> dict[str, Any]:
    """A tool span that ended with an exception event, as Pydantic AI records one."""
    span = TracerProvider().get_tracer("test").start_span("running tool", attributes=attributes)
    span.add_event(
        "exception",
        {
            "exception.type": exception_type,
            "exception.message": "x must be 2",
            "exception.stacktrace": "Traceback (most recent call last): ...",
        },
    )
    span.set_status(Status(StatusCode.ERROR))
    span.end()
    assert isinstance(span, ReadableSpan)
    return to_wire(normalize(span, ended=True).span)


@pytest.mark.parametrize(
    ("exception_type", "attributes"),
    [
        ("pydantic_ai.exceptions.ToolRetryError", {}),
        ("RuntimeError", {"pydantic_ai.tool.retry.cause": "model_retry"}),
        ("RuntimeError", {"pydantic_ai.output.retry.cause": "model_retry"}),
    ],
)
def test_an_expected_retry_has_no_stacktrace(exception_type: str, attributes: dict[str, Any]) -> None:
    wire = _failed(attributes, exception_type)
    assert wire["error"] == {"type": exception_type, "message": "x must be 2", "stacktrace": None}


def test_a_real_error_keeps_its_stacktrace() -> None:
    wire = _failed({}, "RuntimeError")
    assert wire["error"]["stacktrace"].startswith("Traceback")


def test_error_status_without_exception_event_uses_the_status_description() -> None:
    span = _span("job", {}, status=Status(StatusCode.ERROR, "gave up"))
    wire = to_wire(normalize(span, ended=True).span)
    assert wire["status"] == "error"
    assert wire["error"] == {"type": "Error", "message": "gave up", "stacktrace": None}


def test_bulky_definitions_stay_off_the_wire_but_parameters_reach_derivation() -> None:
    span = _span(
        "chat gpt-x",
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "openai",
            "gen_ai.request.max_tokens": 100,
            "model_request_parameters": '{"function_tools": [{"name": "t"}]}',
            "gen_ai.tool.definitions": "[]",
            "logfire.json_schema": "{}",
        },
    )
    result = normalize(span, ended=True)
    wire = to_wire(result.span)
    assert result.request_parameters == {"function_tools": [{"name": "t"}]}
    assert wire["attributes"] == {"gen_ai.operation.name": "chat"}
    assert wire["data"] == snapshot(
        {
            "model_name": "gpt-x",
            "provider": "openai",
            "response_model": None,
            "settings": {"max_tokens": 100},
            "instructions": None,
            "input_messages": None,
            "output_messages": None,
            "usage": None,
        }
    )


def test_tool_span_without_gen_ai_tool_name_falls_back_to_the_span_name() -> None:
    span = _span("execute_tool lookup", {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.call.result": "3"})
    wire = to_wire(normalize(span, ended=True).span)
    assert wire["name"] == "lookup"
    assert wire["data"] == {"tool_name": "lookup", "call_id": None, "arguments": None, "result": 3, "retry": None}


def test_graph_span_without_a_graph_id_is_other() -> None:
    span = _span("run node write", {"pydantic_ai_viz.kind": "graph_node"})
    assert normalize(span, ended=True).span.kind == "other"


def test_messages_normalisation_rules() -> None:
    raw = [
        {"role": "system", "parts": [{"type": "text", "content": "Be brief."}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "hmm"},
                {"type": "thinking", "content": "more"},
                {"type": "tool_call", "id": "c1", "name": "f", "arguments": '{"a": 1}'},
                {"type": "tool_call", "id": "c2", "name": "g", "arguments": "not json"},
                {"type": "tool_call", "id": "c3", "name": "h"},
                {"type": "image", "uri": "data:..."},
            ],
        },
        {"role": "user", "parts": [{"type": "tool_call_response", "id": "c1", "name": "f", "result": {"ok": True}}]},
        {"role": "tool", "parts": [{"type": "tool_call_response", "name": "final_result", "result": "done"}]},
        {"role": "user", "parts": [{"type": "text"}, {"type": "tool_call_response", "id": "c2", "name": "g"}]},
        {"role": "developer", "parts": []},
        "garbage",
    ]
    parsed = messages(raw)
    assert parsed is not None
    assert [to_wire(message) for message in parsed] == snapshot(
        [
            {"role": "system", "parts": [{"type": "text", "content": "Be brief."}]},
            {
                "role": "assistant",
                "parts": [
                    {"type": "thinking", "content": "hmm"},
                    {"type": "thinking", "content": "more"},
                    {"type": "tool_call", "id": "c1", "name": "f", "arguments": {"a": 1}},
                    {"type": "tool_call", "id": "c2", "name": "g", "arguments": "not json"},
                    {"type": "tool_call", "id": "c3", "name": "h", "arguments": None},
                    {"type": "other", "raw": {"type": "image", "uri": "data:..."}},
                ],
            },
            {"role": "tool", "parts": [{"type": "tool_result", "id": "c1", "name": "f", "result": {"ok": True}}]},
            {"role": "tool", "parts": [{"type": "tool_result", "id": None, "name": "final_result", "result": "done"}]},
            {
                "role": "user",
                "parts": [
                    {"type": "text", "content": None},
                    {"type": "tool_result", "id": "c2", "name": "g", "result": None},
                ],
            },
            {"role": "user", "parts": []},
            {"role": "user", "parts": [{"type": "other", "raw": "garbage"}]},
        ]
    )


def test_messages_accept_a_json_string_and_reject_anything_else() -> None:
    parsed = messages('[{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]')
    assert parsed is not None
    assert [to_wire(message) for message in parsed] == [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]
    assert messages(None) is None
    assert messages("not json") is None
    assert messages('{"role": "user"}') is None


def test_instructions_join_contents_with_a_blank_line() -> None:
    assert instructions('[{"type":"text","content":"A"},{"type":"text","content":"B"}]') == "A\n\nB"
    assert instructions("[]") is None
    assert instructions(None) is None
    assert instructions("plain text") == "plain text"


def test_jsonish_strings_parse_or_stay_raw() -> None:
    assert parse_jsonish('{"city":"Paris"}') == {"city": "Paris"}
    assert parse_jsonish("3") == 3
    assert parse_jsonish("research notes") == "research notes"
    assert parse_jsonish(None) is None
    assert parse_jsonish("NaN") == "nan"  # not a JSON number, so it must not reach the wire as one


def test_long_strings_are_cut_everywhere() -> None:
    long = "x" * (MAX_STRING_CHARS + 10)
    cut = clip(long)
    assert cut == "x" * MAX_STRING_CHARS + f"... [truncated, {MAX_STRING_CHARS + 10} chars total]"
    assert clip("x" * MAX_STRING_CHARS) == "x" * MAX_STRING_CHARS
    assert clip({"a": [long, 1, None, True, 1.5]}) == {"a": [cut, 1, None, True, 1.5]}
    span = _span(
        "execute_tool big", {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.call.result": long, "x": long}
    )
    wire = to_wire(normalize(span, ended=True).span)
    assert wire["data"]["result"] == cut
    assert wire["attributes"]["x"] == cut
