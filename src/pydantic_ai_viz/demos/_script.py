"""Scripted offline models for the demos.

A script is a list of turns. `scripted_model` turns it into a `FunctionModel` that has both a
plain and a streaming implementation, so the same script works whether or not token streaming
is switched on. Nothing here is random and nothing touches the network.

Which turn is played depends only on the message history: turn `n` answers the request that
already contains `n` model responses. A turn can also be a function of the history, which is
how tool results, retries and the user prompt steer what the "model" says next.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

OUTPUT = "<output>"
"""Tool name that stands for the agent's output tool, whatever it is called."""

CHUNK_MS = 40.0
"""Base delay per streamed chunk (one word of text, or one piece of tool-call arguments)."""

_speed: ContextVar[float] = ContextVar("pydantic_ai_viz_demo_speed", default=0.0)


@contextmanager
def playback(speed: float) -> Generator[None]:
    """Run the enclosed scenario with delays divided by `speed`; 0 means no delays at all.

    Outside this context the speed is 0, so agents imported and run directly never sleep.
    """
    token = _speed.set(speed)
    try:
        yield
    finally:
        _speed.reset(token)


def _seconds(base_ms: float) -> float:
    speed = _speed.get()
    return base_ms / 1000.0 / speed if speed > 0 else 0.0


async def pause(base_ms: float) -> None:
    """Sleep for `base_ms` milliseconds scaled by the playback speed."""
    seconds = _seconds(base_ms)
    if seconds:
        await asyncio.sleep(seconds)


def pause_sync(base_ms: float) -> None:
    """Blocking twin of `pause`, for synchronous tools (they run in a worker thread)."""
    seconds = _seconds(base_ms)
    if seconds:
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class Call:
    """One tool call in a turn."""

    tool: str
    args: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Turn:
    """One model response: optional thinking, optional text, then any number of tool calls."""

    text: str = ""
    calls: tuple[Call, ...] = ()
    thinking: str = ""


def call(tool: str, /, **args: Any) -> Call:
    """A call to the tool named `tool`."""
    return Call(tool, args)


def finish(**args: Any) -> Call:
    """A call to the agent's output tool, ending the run with structured output."""
    return Call(OUTPUT, args)


class History:
    """Read-only view of the conversation so far, as seen by the scripted model."""

    def __init__(self, messages: list[ModelMessage]) -> None:
        self.messages = messages

    @property
    def turn(self) -> int:
        """Number of model responses so far, which is the index of the turn to play."""
        return sum(isinstance(message, ModelResponse) for message in self.messages)

    @property
    def prompt(self) -> str:
        """Text of the most recent user prompt."""
        for part in self._request_parts():
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
        raise LookupError("no user prompt in the message history")

    def results(self, tool: str) -> list[Any]:
        """Every successful result of `tool`, oldest first."""
        found = [
            part.content
            for part in self._request_parts()
            if isinstance(part, ToolReturnPart) and part.tool_name == tool and part.outcome == "success"
        ]
        found.reverse()
        return found

    def result(self, tool: str) -> Any:
        """The most recent successful result of `tool`."""
        results = self.results(tool)
        if not results:
            raise LookupError(f"no result for tool {tool!r} in the message history")
        return results[-1]

    def _request_parts(self) -> Iterator[Any]:
        """Request parts, newest first."""
        for message in reversed(self.messages):
            if isinstance(message, ModelRequest):
                yield from reversed(message.parts)


TurnFunction = Callable[[History], Turn]


def text_chunks(text: str) -> list[str]:
    """Split text into word-sized chunks whose concatenation is exactly `text`."""
    return [chunk for chunk in re.split(r"(?<=\s)(?=\S)", text) if chunk]


def json_chunks(args_json: str) -> list[str]:
    """Split tool-call argument JSON into two or three pieces (one if it is tiny)."""
    pieces = 1 if len(args_json) < 8 else 2 if len(args_json) <= 48 else 3
    size = -(-len(args_json) // pieces)
    return [args_json[start : start + size] for start in range(0, len(args_json), size)]


def scripted_model(model_name: str, *turns: Turn | TurnFunction) -> FunctionModel:
    """Build a deterministic `FunctionModel` that plays `turns` in order.

    Streamed and non-streamed requests produce the same response and take the same
    (speed-scaled) time; streaming just delivers it a word at a time.
    """

    def resolve(messages: list[ModelMessage], info: AgentInfo) -> tuple[Turn, list[tuple[str, str, str]]]:
        history = History(messages)
        index = history.turn
        if index >= len(turns):
            raise RuntimeError(f"{model_name}: the script has {len(turns)} turns, but turn {index + 1} was requested")
        entry = turns[index]
        turn = entry(history) if callable(entry) else entry
        calls: list[tuple[str, str, str]] = []
        for position, item in enumerate(turn.calls, start=1):
            name = item.tool
            if name == OUTPUT:
                if not info.output_tools:
                    raise RuntimeError(f"{model_name}: turn {index + 1} finishes with output, but there is none")
                name = info.output_tools[0].name
            calls.append((name, json.dumps(item.args), f"call_{index + 1}_{position}"))
        return turn, calls

    async def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn, calls = resolve(messages, info)
        chunks = len(text_chunks(turn.thinking)) + len(text_chunks(turn.text))
        chunks += sum(len(json_chunks(args)) for _, args, _ in calls)
        await pause(CHUNK_MS * chunks)
        parts: list[ModelResponsePart] = []
        if turn.thinking:
            parts.append(ThinkingPart(turn.thinking))
        if turn.text:
            parts.append(TextPart(turn.text))
        parts.extend(ToolCallPart(name, args, tool_call_id) for name, args, tool_call_id in calls)
        return ModelResponse(parts=parts)

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls | DeltaThinkingCalls]:
        turn, calls = resolve(messages, info)
        for chunk in text_chunks(turn.thinking):
            await pause(CHUNK_MS)
            yield {0: DeltaThinkingPart(content=chunk)}
        for chunk in text_chunks(turn.text):
            await pause(CHUNK_MS)
            yield chunk
        # Index 0 is reserved for the thinking part, so tool calls start at 1.
        for index, (name, args, tool_call_id) in enumerate(calls, start=1):
            for position, piece in enumerate(json_chunks(args)):
                await pause(CHUNK_MS)
                if position == 0:
                    yield {index: DeltaToolCall(name=name, json_args=piece, tool_call_id=tool_call_id)}
                else:
                    yield {index: DeltaToolCall(json_args=piece)}

    return FunctionModel(function, stream_function=stream_function, model_name=model_name)
