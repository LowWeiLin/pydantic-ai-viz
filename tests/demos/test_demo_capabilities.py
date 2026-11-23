from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolReturnPart

pytest.importorskip("pydantic_ai_harness")

from pydantic_ai_viz import AgentBlueprint, analyze, record
from pydantic_ai_viz.demos import capabilities
from pydantic_ai_viz.demos.capabilities import briefing

from .conftest import Spy, tool_calls, transcript

pytestmark = pytest.mark.anyio

CAUSE = "https://archive.example/apollo-13/cause"
RETURN = "https://archive.example/apollo-13/return"


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_scenario(spy: Spy, stream: bool) -> None:
    runs = spy(briefing, stream=stream)
    result = await briefing.run(capabilities.QUESTION)
    messages = result.all_messages()

    assert runs == Counter(briefing=1)
    calls = tool_calls(messages)
    assert [[name for name, _ in turn] for turn in calls] == [
        ["write_plan"],
        ["search_archive"],
        ["fetch_page", "fetch_page"],
        ["load_capability"],
        ["write_memory"],
        [],
    ]
    assert [args["url"] for _, args in calls[2]] == [CAUSE, RETURN]
    assert calls[3] == [("load_capability", {"id": "house-style"})]

    first = messages[1]
    assert isinstance(first, ModelResponse)
    assert [type(part) for part in first.parts[:2]] == [ThinkingPart, TextPart]

    returns: dict[str, list[Any]] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                returns.setdefault(part.tool_name, []).append(part.content)
    assert "oxygen tank 2" in returns["fetch_page"][0]
    assert "lifeboat" in returns["fetch_page"][1]
    assert "Capability 'house-style' loaded" in returns["load_capability"][0]["message"]
    assert returns["write_memory"][0]["status"] == "created"

    # The skill came from the bundled file, and its text reached the model as instructions.
    assert any(
        "# Skill: house-style" in str(getattr(part, "content", ""))
        for message in messages
        for part in message.parts
        if part.part_kind == "instruction-delta"
    )
    assert isinstance(result.output, str)
    assert result.output.startswith("Verdict:")
    assert result.output.endswith("Sources: Apollo 13 review board report; Apollo 13 mission report.")


async def test_deterministic_plain_and_streamed(spy: Spy) -> None:
    first = await briefing.run(capabilities.QUESTION)
    second = await briefing.run(capabilities.QUESTION)
    spy(briefing, stream=True)
    streamed = await briefing.run(capabilities.QUESTION)
    assert transcript(first.all_messages()) == transcript(second.all_messages())
    assert transcript(first.all_messages()) == transcript(streamed.all_messages())
    assert first.output == second.output == streamed.output


async def test_the_guard_stops_a_prompt_the_desk_does_not_cover() -> None:
    result = await briefing.run("Give me medical advice about the crew's carbon dioxide.")
    assert result.output == "The briefing desk does not give medical advice."


def test_the_blueprint_shows_one_capability_per_category() -> None:
    with record(briefing, instrument=False) as session:
        events = session.events
    agent = next(AgentBlueprint.model_validate(e["agent"]) for e in events if e["type"] == "blueprint.agent")
    shown = {c.name: c for c in agent.capabilities if not c.builtin}
    assert {name: c.category for name, c in shown.items()} == {
        "Thinking": "reasoning",
        "WebSearch": "web",
        "WebFetch": "web",
        "MCP": "tools",
        "Skills": "knowledge",
        "Memory": "knowledge",
        "Planning": "reasoning",
        "ModelFallback": "loop",
        "InputGuardrail": "safety",
    }
    assert [c.name for c in agent.capabilities if c.native_tools] == ["ToolSearch", "MCP"]


def test_the_blueprint_lists_both_skills_and_reading_the_source_finds_the_same() -> None:
    with record(briefing, instrument=False) as session:
        events = session.events
    agent = next(AgentBlueprint.model_validate(e["agent"]) for e in events if e["type"] == "blueprint.agent")
    (skills,) = (c for c in agent.capabilities if c.name == "Skills")
    assert [(s.name, s.path, s.model_invocable) for s in skills.skills] == [
        ("fact-check", "_skills/fact-check/SKILL.md", True),
        ("house-style", "_skills/house-style/SKILL.md", True),
    ]
    assert all(s.body for s in skills.skills)
    assert skills.deferred is False
    assert agent.warnings == []

    (found,) = (b for b in analyze(Path(capabilities.__file__)).agents if b.label == "briefing")
    (read,) = (c for c in found.capabilities if c.name == "Skills")
    assert read.skills == skills.skills
    assert found.warnings == [
        "`Skills` reads `_skills` from a workspace decided when the code runs; the skills shown are those in `_skills`"
    ]


async def test_nothing_is_written_outside_a_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = Path(capabilities.__file__).parent

    def files() -> set[Path]:
        return {path for path in package.rglob("*") if "__pycache__" not in path.parts}

    before = files()
    monkeypatch.chdir(tmp_path)
    report = await capabilities.demo.run(0)
    assert isinstance(report, str)
    assert report.startswith("Verdict:")
    assert files() == before
    assert not list(tmp_path.iterdir())


async def test_demo_run_returns_the_brief() -> None:
    brief = await capabilities.demo.run(0)
    assert isinstance(brief, str)
    assert brief.count("\n- ") == 3
    assert await capabilities.demo.run(0) == brief
