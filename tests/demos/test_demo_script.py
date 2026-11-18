from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from pydantic_ai_viz.demos import _script
from pydantic_ai_viz.demos._script import History, Turn, call, finish, json_chunks, scripted_model, text_chunks

from .conftest import tool_calls, transcript

pytestmark = pytest.mark.anyio


def test_text_chunks_split_by_word_and_rejoin_exactly() -> None:
    text = "Let me check\nthat  order for you."
    chunks = text_chunks(text)
    assert "".join(chunks) == text
    assert chunks == ["Let ", "me ", "check\n", "that  ", "order ", "for ", "you."]
    assert text_chunks("") == []


@pytest.mark.parametrize(
    ("args", "pieces"),
    [({}, 1), ({"order_id": "A-1042"}, 2), ({"order_id": "A-1042", "reason": "Glass carafe cracked on arrival."}, 3)],
)
def test_json_chunks_rejoin_exactly(args: dict[str, Any], pieces: int) -> None:
    args_json = json.dumps(args)
    chunks = json_chunks(args_json)
    assert len(chunks) == pieces
    assert "".join(chunks) == args_json


def _weather_agent() -> Agent[None, str]:
    def forecast(history: History) -> Turn:
        return Turn(text=f"{history.prompt} {history.result('get_weather')}, checked {history.results('today')}.")

    agent = Agent(
        scripted_model(
            "scripted-test",
            Turn(
                thinking="Need the weather first.",
                text="One moment.",
                calls=(call("get_weather", city="Oslo"), call("today"), call("today")),
            ),
            forecast,
        ),
        name="weather",
    )

    @agent.tool_plain
    def get_weather(city: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"Snow in {city}"

    @agent.tool_plain
    def today() -> str:  # pyright: ignore[reportUnusedFunction]
        return "Monday"

    return agent


async def test_turns_follow_history_and_tool_results() -> None:
    result = await _weather_agent().run("Forecast:")
    assert result.output == "Forecast: Snow in Oslo, checked ['Monday', 'Monday']."
    first = result.all_messages()[1]
    assert isinstance(first, ModelResponse)
    assert [type(part) for part in first.parts] == [ThinkingPart, TextPart, ToolCallPart, ToolCallPart, ToolCallPart]
    assert tool_calls(result.all_messages())[0] == [("get_weather", {"city": "Oslo"}), ("today", {}), ("today", {})]


async def test_streamed_and_plain_requests_give_the_same_conversation() -> None:
    plain = await _weather_agent().run("Forecast:")
    events: list[Any] = []

    async def handler(ctx: Any, stream: Any) -> None:
        events.extend([event async for event in stream])

    streamed = await _weather_agent().run("Forecast:", event_stream_handler=handler)
    assert transcript(streamed.all_messages()) == transcript(plain.all_messages())
    assert streamed.output == plain.output

    # Text arrives a word at a time and tool-call arguments in more than one piece.
    deltas = [event.delta for event in events if type(event).__name__ == "PartDeltaEvent"]
    text_deltas = [delta.content_delta for delta in deltas if delta.part_delta_kind == "text"]
    assert text_deltas[:2] == ["moment.", "Snow "]  # "One " and "Forecast: " each opened a part
    args_deltas = [delta.args_delta for delta in deltas if delta.part_delta_kind == "tool_call"]
    assert len(args_deltas) >= 1
    assert all(isinstance(delta, str) for delta in args_deltas)


async def test_output_tool_placeholder_resolves_to_the_agents_output_tool() -> None:
    class Answer(BaseModel):
        value: int

    agent = Agent(scripted_model("scripted-test", Turn(calls=(finish(value=3),))), output_type=Answer)
    result = await agent.run("go")
    assert result.output == Answer(value=3)


async def test_running_past_the_script_is_an_error() -> None:
    agent = Agent(scripted_model("scripted-test", Turn(calls=(call("noop"),))))

    @agent.tool_plain
    def noop() -> str:  # pyright: ignore[reportUnusedFunction]
        return "ok"

    with pytest.raises(RuntimeError, match="the script has 1 turns, but turn 2 was requested"):
        await agent.run("go")


def test_history_ignores_retried_results() -> None:
    history = History(
        [
            ModelRequest(parts=[UserPromptPart("hello")]),
            ModelResponse(parts=[TextPart("hi")]),
            ModelRequest(
                parts=[
                    ToolReturnPart("lookup", "bad id", tool_call_id="c1", outcome="retried"),
                    ToolReturnPart("lookup", "first", tool_call_id="c2"),
                ]
            ),
            ModelResponse(parts=[TextPart("again")]),
            ModelRequest(parts=[ToolReturnPart("lookup", "second", tool_call_id="c3")]),
        ]
    )
    assert history.turn == 2
    assert history.prompt == "hello"
    assert history.results("lookup") == ["first", "second"]
    assert history.result("lookup") == "second"
    with pytest.raises(LookupError):
        history.result("missing")


async def test_delays_scale_with_speed_and_vanish_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await _script.pause(400)  # outside playback() nothing sleeps
    with _script.playback(0):
        await _script.pause(400)
    assert slept == []

    with _script.playback(1):
        await _script.pause(400)
    with _script.playback(4):
        await _script.pause(400)
    assert slept == [pytest.approx(0.4), pytest.approx(0.1)]


async def test_a_scripted_request_takes_the_same_time_streamed_or_not(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def drain(ctx: Any, stream: Any) -> None:
        async for _ in stream:
            pass

    with _script.playback(2):
        await _weather_agent().run("Forecast:")
        plain, slept[:] = sum(slept), []
        await _weather_agent().run("Forecast:", event_stream_handler=drain)
    assert plain > 0
    assert sum(slept) == pytest.approx(plain)
    assert all(seconds == pytest.approx(_script.CHUNK_MS / 1000 / 2) for seconds in slept)


def test_model_identity_is_honest() -> None:
    model = scripted_model("scripted-test", Turn(text="hi"))
    assert model.model_name == "scripted-test"
    assert model.system == "function"
