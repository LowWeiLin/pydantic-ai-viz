from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterable, Callable
from typing import Any

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ToolCallPart

Transcript = list[tuple[str, list[tuple[Any, ...]]]]
Spy = Callable[..., Counter[str]]


def transcript(messages: list[ModelMessage]) -> Transcript:
    """Everything in a conversation that must be identical between runs (no timestamps, no usage)."""
    out: Transcript = []
    for message in messages:
        parts: list[tuple[Any, ...]] = []
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                parts.append((part.part_kind, part.tool_name, part.tool_call_id, part.args_as_dict()))
            else:
                parts.append((part.part_kind, getattr(part, "tool_name", None), getattr(part, "content", None)))
        out.append((message.kind, parts))
    return out


def tool_calls(messages: list[ModelMessage]) -> list[list[tuple[str, dict[str, Any]]]]:
    """Tool calls per model response, in order."""
    return [
        [(part.tool_name, part.args_as_dict()) for part in message.parts if isinstance(part, ToolCallPart)]
        for message in messages
        if message.kind == "response"
    ]


async def _drain(ctx: RunContext[Any], events: AsyncIterable[Any]) -> None:
    async for _ in events:
        pass


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    """Count `agent.run` calls per agent name; with `stream=True` also force every model request to stream.

    Streaming is forced the same way a token listener does it: by handing the run an event stream handler.
    """

    def install(*agents: Agent[Any, Any], stream: bool = False) -> Counter[str]:
        runs: Counter[str] = Counter()
        for agent in agents:

            async def run(
                *args: Any, _agent: Agent[Any, Any] = agent, _original: Any = agent.run, **kwargs: Any
            ) -> Any:
                assert _agent.name is not None
                runs[_agent.name] += 1
                if stream:
                    kwargs.setdefault("event_stream_handler", _drain)
                return await _original(*args, **kwargs)

            monkeypatch.setattr(agent, "run", run)
        return runs

    return install
