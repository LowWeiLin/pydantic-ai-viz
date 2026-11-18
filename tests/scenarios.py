"""Offline agents shared by the capture tests.

Agents are built fresh by every call: a token listener (``agent.on_event``) stays
on an agent for the life of the process and switches its model requests to
streaming, so module-level agents would leak state from one test into the next.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, InstrumentationSettings, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

PROMPT = "Weather in Paris?"


@dataclass
class Deps:
    user: str = "ann"


class Answer(BaseModel):
    """Final structured answer."""

    city: str
    temp_c: float


@dataclass
class Team:
    main: Agent[Deps, Answer]
    researcher: Agent[None, str]


_TEXT = ("Let ", "me ", "check.")
_TURNS: tuple[tuple[tuple[str, str, str], ...], ...] = (
    (
        ("get_weather", '{"city": "Paris"}', "c1"),
        ("flaky", '{"x": 1}', "c2"),
        ("delegate_research", '{"topic": "Paris"}', "c3"),
    ),
    (("flaky", '{"x": 2}', "c4"), ("add", '{"a": 1, "b": 2}', "c5")),
    (("final_result", '{"city": "Paris", "temp_c": 21.5}', "c6"),),
)
"""Tool calls per model turn as ``(tool, args JSON, call id)``; turn 0 also says ``_TEXT`` first."""


def _turn(messages: list[ModelMessage]) -> int:
    return sum(isinstance(message, ModelResponse) for message in messages)


def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    turn = _turn(messages)
    parts: list[Any] = [TextPart("".join(_TEXT))] if turn == 0 else []
    # Objects here, raw JSON strings when streamed: the normaliser must treat both alike.
    parts.extend(ToolCallPart(name, json.loads(args), call_id) for name, args, call_id in _TURNS[turn])
    return ModelResponse(parts=parts)


async def _respond_streaming(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
    """The same script as ``_respond``, in chunks: words, then each call's arguments in two pieces."""
    turn = _turn(messages)
    if turn == 0:
        for word in _TEXT:
            yield word
    for index, (name, args, call_id) in enumerate(_TURNS[turn]):
        yield {index: DeltaToolCall(name=name, json_args=args[:6], tool_call_id=call_id)}
        yield {index: DeltaToolCall(json_args=args[6:])}


def build_team(instrument: InstrumentationSettings | None = None, *, main_name: str | None = "main") -> Team:
    """A ``main`` agent that retries a tool, delegates to ``researcher`` and returns an ``Answer``.

    One run makes 3 model requests and 5 tool calls on ``main`` (three in parallel, one
    of them raising ``ModelRetry``), plus 2 requests and 1 tool call on ``researcher``.
    """
    researcher: Agent[None, str] = Agent(
        TestModel(custom_output_text="research notes"),
        name="researcher",
        description="Looks things up",
        instructions="You research.",
    )

    @researcher.tool_plain
    def lookup(topic: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Look up a topic."""
        return f"facts about {topic}"

    extra: FunctionToolset[Deps] = FunctionToolset(id="extra_ts")

    @extra.tool_plain
    def add(a: int, b: int) -> int:  # pyright: ignore[reportUnusedFunction]
        """Add two ints."""
        return a + b

    main: Agent[Deps, Answer] = Agent(
        FunctionModel(_respond, stream_function=_respond_streaming, model_name="scripted-fn"),
        name=main_name,
        description="Main demo agent",
        deps_type=Deps,
        output_type=Answer,
        instructions="Static instructions.",
        system_prompt="Static system prompt.",
        toolsets=[extra],
        retries=3,
        model_settings={"temperature": 0.2},
    )
    if instrument is not None:
        # Per-agent settings, so tests of the normaliser alone touch nothing global.
        main.instrument = researcher.instrument = instrument

    @main.instructions
    def dyn_instructions(ctx: RunContext[Deps]) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"User is {ctx.deps.user}."

    @main.tool
    def get_weather(ctx: RunContext[Deps], city: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Get the weather.

        Args:
            city: The city name.
        """
        return {"city": city, "temp_c": 21.5}

    @main.tool_plain(retries=2)
    def flaky(x: int) -> str:  # pyright: ignore[reportUnusedFunction]
        """Fails the first time."""
        if x == 1:
            raise ModelRetry("x must be 2")
        return "ok"

    @main.tool
    async def delegate_research(ctx: RunContext[Deps], topic: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Delegate to the researcher agent."""
        result = await researcher.run(f"research {topic}", usage=ctx.usage)
        return result.output

    return Team(main=main, researcher=researcher)


async def drain(ctx: RunContext[Any], events: Any) -> None:
    """An ``event_stream_handler`` that only forces every model request to stream."""
    async for _ in events:
        pass


_HEX_ID = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIME_KEYS = frozenset({"ts", "start_ms", "end_ms", "gen_ai.client.operation.time_to_first_chunk"})


def scrub(value: Any, ids: dict[str, str] | None = None) -> Any:
    """Replace everything that differs between two identical runs: ids, times and stack traces.

    Ids are numbered in order of first appearance, so parent links stay comparable.
    """
    seen: dict[str, str] = {} if ids is None else ids
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _TIME_KEYS:
                out[key] = None if item is None else 0.0
            elif key == "stacktrace":
                out[key] = None if item is None else "<stacktrace>"
            else:
                out[key] = scrub(item, seen)
        return out
    if isinstance(value, list):
        return [scrub(item, seen) for item in value]
    if isinstance(value, str) and (_HEX_ID.match(value) or _UUID.match(value)):
        return seen.setdefault(value, f"<id {len(seen) + 1}>")
    return value
