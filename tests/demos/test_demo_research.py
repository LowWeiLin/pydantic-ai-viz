from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolReturnPart

from pydantic_ai_viz.demos import research
from pydantic_ai_viz.demos.research import planner, searcher, writer

from .conftest import Spy, tool_calls, transcript

pytestmark = pytest.mark.anyio

QUERIES = [
    "cause of the Hubble primary mirror flaw",
    "why testing missed the Hubble mirror error before launch",
    "how Hubble's optics were repaired in orbit",
]


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_scenario(spy: Spy, stream: bool) -> None:
    runs = spy(planner, searcher, writer, stream=stream)
    result = await planner.run(research.QUESTION)
    messages = result.all_messages()

    # Three parallel searcher delegations through one tool, then one writer run.
    assert runs == Counter(planner=1, searcher=3, writer=1)
    calls = tool_calls(messages)
    assert [[name for name, _ in turn] for turn in calls] == [["search", "search", "search"], ["write_report"], []]
    assert [args["query"] for _, args in calls[0]] == QUERIES

    first = messages[1]
    assert isinstance(first, ModelResponse)
    assert [type(part) for part in first.parts[:2]] == [ThinkingPart, TextPart]

    # Each search found its own sources, and the writer was given all three findings.
    findings: list[Any] = [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "search"
    ]
    assert len(findings) == len(set(findings)) == 3
    assert "2.2 micrometres" in findings[0]
    assert "end-to-end optical test" in findings[1]
    assert "COSTAR" in findings[2]
    assert calls[1] == [("write_report", {"question": research.QUESTION, "findings": findings})]

    # Plain text output: the writer's report, passed through unchanged.
    assert isinstance(result.output, str)
    assert result.output.startswith("## Why Hubble launched with blurred vision")
    assert result.output.count("**") == 6

    # Sub-agent requests are counted once: 3 planner + 3 x 2 searcher + 1 writer.
    assert result.usage.requests == 3 + 6 + 1


async def test_searcher_reports_when_the_library_has_nothing() -> None:
    result = await searcher.run("What is the airspeed of a swallow?")
    assert result.output == "The library has nothing on: What is the airspeed of a swallow?"


async def test_deterministic_plain_and_streamed(spy: Spy) -> None:
    first = await planner.run(research.QUESTION)
    second = await planner.run(research.QUESTION)
    spy(planner, searcher, writer, stream=True)
    streamed = await planner.run(research.QUESTION)
    assert transcript(first.all_messages()) == transcript(second.all_messages())
    assert transcript(first.all_messages()) == transcript(streamed.all_messages())
    assert first.output == second.output == streamed.output


async def test_demo_run_returns_the_report() -> None:
    report = await research.demo.run(0)
    assert isinstance(report, str)
    assert report.startswith("## Why Hubble launched with blurred vision")
    assert await research.demo.run(0) == report
