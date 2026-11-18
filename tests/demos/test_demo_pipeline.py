from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

import pydantic_ai_viz
from pydantic_ai_viz.demos import pipeline
from pydantic_ai_viz.demos.pipeline import Draft, critic, graph, writer

from .conftest import Spy, tool_calls, transcript

pytestmark = pytest.mark.anyio

PUBLISHED = (
    "# Dark mode comes to Harbor\n\n"
    "Harbor now has a dark mode. Open Settings, choose Appearance and pick Dark, or pick Auto to follow "
    "your system setting. Notes, code blocks and attachments all adapt, and the editor keeps its contrast "
    "at every text size. It is available today on desktop and mobile."
)


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_scenario(spy: Spy, stream: bool) -> None:
    runs = spy(writer, critic, stream=stream)
    state = Draft()
    visits: Counter[str] = Counter()
    async with graph.iter(state=state, inputs=pipeline.BRIEF) as run:
        async for item in run:
            if isinstance(item, list):
                visits.update(str(task.node_id) for task in item)

    # `write` and `review` are visited twice (the "needs work" loop is taken once), `publish` once.
    assert (visits["write"], visits["review"], visits["publish"]) == (2, 2, 1)
    assert runs == Counter(writer=2, critic=2)
    assert run.output == PUBLISHED
    assert state.revision == 2
    assert state.brief == pipeline.BRIEF

    # The critic checked both drafts, rejected the first with concrete feedback and approved the second.
    calls = [turn[0] for turn in tool_calls(state.critic_messages)]
    assert [name for name, _ in calls] == ["check_draft", "final_result", "check_draft", "final_result"]
    assert calls[1][1] == {
        "approved": False,
        "feedback": "Cut the hype (thrilled, revolutionary, game-changing, cutting-edge). "
        "Tell readers how to turn it on.",
    }
    assert calls[3][1] == {"approved": True, "feedback": "Clear and concrete in 46 words. Ship it."}

    # The revision is a real follow-up turn that carries the feedback.
    prompts = [
        part.content for message in state.writer_messages for part in message.parts if part.part_kind == "user-prompt"
    ]
    assert prompts == [
        f"Write the post for this brief: {pipeline.BRIEF}",
        f"Revise the draft. Reviewer feedback: {calls[1][1]['feedback']}",
    ]


async def test_graph_run_is_deterministic() -> None:
    first, second = Draft(), Draft()
    assert await graph.run(state=first, inputs=pipeline.BRIEF) == PUBLISHED
    assert await graph.run(state=second, inputs=pipeline.BRIEF) == PUBLISHED
    assert transcript(first.writer_messages) == transcript(second.writer_messages)
    assert transcript(first.critic_messages) == transcript(second.critic_messages)


async def test_graph_structure() -> None:
    mermaid = graph.render(direction="LR")
    for expected in ("write", "review", "publish", "approved", "needs work"):
        assert expected in mermaid


async def test_demo_run_goes_through_run_graph_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_run_graph(target: Any, **kwargs: Any) -> Any:
        seen.update(kwargs, graph=target)
        return await target.run(state=kwargs["state"], inputs=kwargs["inputs"])

    # Patched after `pipeline` was imported, so this only works if the lookup happens inside `run`.
    monkeypatch.setattr(pydantic_ai_viz, "run_graph", fake_run_graph, raising=False)
    assert await pipeline.demo.run(0) == PUBLISHED
    assert seen["graph"] is graph
    assert isinstance(seen["state"], Draft)
    assert seen["inputs"] == pipeline.BRIEF
    assert set(seen) == {"graph", "state", "inputs"}
