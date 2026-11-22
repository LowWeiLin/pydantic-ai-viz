from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import DeferredToolRequests, ModelRetry
from pydantic_ai.messages import ToolReturnPart

pytest.importorskip("pydantic_ai_harness")

from pydantic_ai_viz import AgentBlueprint, analyze, record
from pydantic_ai_viz.demos import operations
from pydantic_ai_viz.demos.operations import log_reader, oncall

from .conftest import Spy, tool_calls, transcript

pytestmark = pytest.mark.anyio


async def play(stream: bool = False) -> tuple[Any, Any]:
    """Both runs of the scenario, the second answering the approvals the first waits for."""
    handler = _drain if stream else None
    first = await oncall.run(operations.ALERT, event_stream_handler=handler)
    assert isinstance(first.output, DeferredToolRequests)
    answers = operations._answers(first.output)  # pyright: ignore[reportPrivateUsage]
    second = await oncall.run(
        message_history=first.all_messages(), deferred_tool_results=answers, event_stream_handler=handler
    )
    return first, second


async def _drain(ctx: Any, events: Any) -> None:
    async for _ in events:
        pass


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_scenario(spy: Spy, stream: bool) -> None:
    runs = spy(log_reader, stream=stream)
    first, second = await play(stream)

    assert runs["log_reader"] == 1
    assert [[name for name, _ in turn] for turn in tool_calls(first.all_messages())] == [
        ["delegate_task"],
        ["restart_service", "restart_service"],
    ]
    requests = first.output
    assert [(r.tool_name, r.args_as_dict()) for r in requests.approvals] == [
        ("restart_service", {"name": "checkout"}),
        ("restart_service", {"name": "payments-db"}),
    ]
    assert requests.calls == []

    returns = [
        (part.tool_name, part.outcome, part.content)
        for message in second.new_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns == [
        ("restart_service", "success", "checkout restarted and healthy"),
        ("restart_service", "denied", "Not the database: restart it only in the maintenance window."),
    ]
    assert second.output.startswith("Incident: checkout errors")
    assert "Cause:" in second.output
    assert "Action:" in second.output


async def test_deterministic_plain_and_streamed() -> None:
    first = await play()
    second = await play()
    streamed = await play(stream=True)
    for a, b, c in zip(first, second, streamed, strict=True):
        assert transcript(a.all_messages()) == transcript(b.all_messages()) == transcript(c.all_messages())
    assert first[1].output == second[1].output == streamed[1].output


def test_the_validator_sends_back_a_report_without_cause_and_action() -> None:
    with pytest.raises(ModelRetry, match="'Cause:' and an 'Action:'"):
        operations.check_report(None, "All good now.")  # pyright: ignore[reportArgumentType]
    assert operations.check_report(None, "Cause: x\nAction: y") == "Cause: x\nAction: y"  # pyright: ignore[reportArgumentType]


def test_the_blueprint_shows_delegation_hooks_approval_and_the_validator() -> None:
    with record(oncall, log_reader, instrument=False) as session:
        events = session.events
    agents = {
        b.label: b
        for b in (AgentBlueprint.model_validate(e["agent"]) for e in events if e["type"] == "blueprint.agent")
    }
    agent = agents["oncall"]
    assert [(d.tool_name, d.agent_id) for d in agent.delegates] == [("delegate_task", agents["log_reader"].id)]
    shown = {c.name: c for c in agent.capabilities if not c.builtin}
    assert list(shown) == ["SubAgents", "Hooks", "Pager"]
    assert [(h.point, h.function, h.tools) for h in shown["Hooks"].hooks] == [
        ("before_tool_execute", "pydantic_ai_viz.demos.operations.guard_restart", ["restart_service"]),
        ("after_model_request", "pydantic_ai_viz.demos.operations.log_response", None),
    ]
    assert shown["Pager"].capability_id == "pager"
    assert {t.name: t.kind for t in agent.tools}["restart_service"] == "unapproved"
    assert agent.output.validators == ["pydantic_ai_viz.demos.operations.check_report"]
    assert agent.warnings == []


def test_reading_the_source_finds_the_same_and_leaves_capture_events_out() -> None:
    with record(oncall, instrument=False) as session:
        events = session.events
    live = next(AgentBlueprint.model_validate(e["agent"]) for e in events if e["type"] == "blueprint.agent")
    analysis = analyze(Path(operations.__file__))
    assert analysis.notices == []
    found = next(b for b in analysis.agents if b.label == "oncall")
    own = [c for c in live.capabilities if not c.builtin]
    assert [(c.name, c.details, [(h.point, h.function, h.tools) for h in c.hooks]) for c in found.capabilities] == [
        (c.name, c.details, [(h.point, h.function, h.tools) for h in c.hooks]) for c in own
    ]
    assert found.output.validators == live.output.validators
    assert {t.name: t.kind for t in found.tools} == {"restart_service": "unapproved"}
    assert found.warnings == []


async def test_the_recording_shows_the_wait_the_event_and_the_resumed_run() -> None:
    with record(oncall, log_reader) as session:
        await operations.demo.run(0)
        events = session.events
    runs = [e["span"] for e in events if e["type"] == "span.ended" and e["span"]["kind"] == "agent_run"]
    assert [r["name"] for r in runs] == ["log_reader", "oncall", "oncall"]
    waiting, resumed = runs[1], runs[2]
    assert [(d["tool_name"], d["kind"]) for d in waiting["data"]["deferred"]] == [
        ("restart_service", "approval"),
        ("restart_service", "approval"),
    ]
    assert resumed["data"]["deferred"] == []
    assert waiting["data"]["conversation_id"] == resumed["data"]["conversation_id"]
    paged = [e for e in waiting["events"] if e["kind"] == "oncall.paged"]
    assert [(e["capability_id"], e["data"]["team"]) for e in paged] == [("pager", "platform")]
    assert not [e for e in resumed["events"] if e["kind"] == "oncall.paged"]


async def test_demo_run_returns_the_report() -> None:
    report = await operations.demo.run(0)
    assert isinstance(report, str)
    assert report.startswith("Incident: ")
    assert await operations.demo.run(0) == report
