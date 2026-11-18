from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from inline_snapshot import snapshot
from opentelemetry import trace
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_graph import Graph, GraphBuilder, StepContext, reduce_list_append

import pydantic_ai_viz
from pydantic_ai_viz import record, run_graph
from pydantic_ai_viz._models import parse_envelope

pytestmark = pytest.mark.anyio


@dataclass
class Tally:
    visits: int = 0


def build_pipeline(name: str | None = "sizes") -> tuple[Graph[Tally, None, int, str], Agent[None, str]]:
    """``double`` (runs an agent) -> decision -> ``big`` | ``small``; ``big`` raises above 100."""
    helper = Agent(TestModel(custom_output_text="noted"), name="helper")
    builder = GraphBuilder(name=name, state_type=Tally, input_type=int, output_type=str)

    @builder.step(label="Double it")
    async def double(ctx: StepContext[Tally, None, int]) -> int:
        ctx.state.visits += 1
        await helper.run(f"doubling {ctx.inputs}")
        return ctx.inputs * 2

    @builder.step
    async def big(ctx: StepContext[Tally, None, int]) -> str:
        if ctx.inputs > 100:
            raise ValueError("too big")
        return f"big {ctx.inputs}"

    @builder.step
    async def small(ctx: StepContext[Tally, None, int]) -> str:
        return f"small {ctx.inputs}"

    builder.add(
        builder.edge_from(builder.start_node).to(double),
        builder.edge_from(double).to(
            builder.decision(note="size?")
            .branch(builder.match(int, matches=lambda value: value > 10).label(">10").to(big))
            .branch(builder.match(int).label("otherwise").to(small))
        ),
        builder.edge_from(big, small).to(builder.end_node),
    )
    return builder.build(), helper


def build_fan_out() -> tuple[Graph[None, None, list[int], list[int]], Agent[None, str]]:
    """Maps a list over two parallel steps, one of which runs an agent, and joins the results."""
    helper = Agent(TestModel(custom_output_text="noted"), name="helper")
    builder = GraphBuilder(name="fan", input_type=list[int], output_type=list[int])

    @builder.step
    async def source(ctx: StepContext[None, None, list[int]]) -> list[int]:
        return ctx.inputs

    @builder.step
    async def square(ctx: StepContext[None, None, int]) -> int:
        await helper.run(f"squaring {ctx.inputs}")
        return ctx.inputs * ctx.inputs

    collect = builder.join(reduce_list_append, initial_factory=list[int])
    builder.add(
        builder.edge_from(builder.start_node).to(source),
        builder.edge_from(source).map().to(square),
        builder.edge_from(square).to(collect),
        builder.edge_from(collect).to(builder.end_node),
    )
    return builder.build(), helper


def spans(events: list[dict[str, Any]], kind: str = "span.ended") -> list[dict[str, Any]]:
    for envelope in events:
        parse_envelope(json.dumps(envelope))
    return [envelope["span"] for envelope in events if envelope["type"] == kind]


def tree(found: list[dict[str, Any]]) -> list[str]:
    names = {span["span_id"]: span["name"] for span in found}
    return [f"{s['kind']} {s['name']} {s['status']} < {names.get(s['parent_id'])}" for s in found]


async def test_output_is_returned_and_the_run_is_one_span_per_task() -> None:
    graph, _ = build_pipeline()
    state = Tally()
    with record(graph) as session:
        assert await run_graph(graph, state=state, inputs=2) == "small 4"
        events = session.events
    assert state.visits == 1
    started = spans(events, "span.started")
    assert tree(started) == snapshot(
        [
            "graph_run sizes running < None",
            "graph_node double running < sizes",
            "agent_run helper running < double",
            "model_request test running < helper",
            "graph_node decision running < sizes",
            "graph_node small running < sizes",
            "graph_node __end__ running < sizes",
        ]
    )
    ended = {span["span_id"]: span for span in spans(events)}
    assert len(ended) == len(started)
    assert len({span["trace_id"] for span in started}) == 1

    run = ended[started[0]["span_id"]]
    assert run["data"] == {"graph_id": "g1", "graph_name": "sizes", "inputs": 2, "output": "small 4"}
    assert (run["agent_name"], run["run_id"], run["attributes"]) == (None, None, {})
    assert started[0]["data"] == {"graph_id": "g1", "graph_name": "sizes", "inputs": 2, "output": None}
    nodes = [ended[span["span_id"]] for span in started if span["kind"] == "graph_node"]
    assert [node["data"] for node in nodes] == snapshot(
        [
            {"graph_id": "g1", "node_id": "double", "inputs": 2},
            {"graph_id": "g1", "node_id": "decision", "inputs": 4},
            {"graph_id": "g1", "node_id": "small", "inputs": 4},
            {"graph_id": "g1", "node_id": "__end__", "inputs": "small 4"},
        ]
    )
    assert all(node["attributes"] == {} for node in nodes)


async def test_agent_run_nests_under_the_node_of_a_single_task_batch() -> None:
    graph, _ = build_pipeline()
    with record(graph) as session:
        await run_graph(graph, state=Tally(), inputs=2)
        found = spans(session.events)
    by_id = {span["span_id"]: span for span in found}
    (agent_run,) = (span for span in found if span["kind"] == "agent_run")
    node = by_id[agent_run["parent_id"]]
    assert (node["kind"], node["name"]) == ("graph_node", "double")
    assert by_id[node["parent_id"]]["kind"] == "graph_run"
    assert node["start_ms"] <= agent_run["start_ms"] <= agent_run["end_ms"] <= node["end_ms"]


async def test_graph_is_registered_on_first_run_and_only_once() -> None:
    graph, _ = build_pipeline()
    with record() as session:
        await run_graph(graph, state=Tally(), inputs=2)
        await run_graph(graph, state=Tally(), inputs=20)
        events = session.events
    graphs = [envelope for envelope in events if envelope["type"] == "blueprint.graph"]
    assert [(g["graph"]["id"], g["graph"]["name"]) for g in graphs] == [("g1", "sizes")]
    first_span = next(envelope for envelope in events if envelope["type"] == "span.started")
    assert graphs[0]["seq"] < first_span["seq"]  # the blueprint is there before its first run
    runs = [span for span in spans(events) if span["kind"] == "graph_run"]
    assert [run["data"]["output"] for run in runs] == ["small 4", "big 40"]
    assert len({run["trace_id"] for run in runs}) == 2  # one trace per graph run


async def test_exception_propagates_and_marks_the_run_and_the_node_as_failed() -> None:
    graph, _ = build_pipeline()
    with record(graph) as session:
        with pytest.raises(ValueError, match="too big"):
            await run_graph(graph, state=Tally(), inputs=60)
        found = spans(session.events)
        assert trace.get_current_span().get_span_context().is_valid is False  # no span left attached
    assert tree(found) == snapshot(
        [
            "model_request test ok < helper",
            "agent_run helper ok < double",
            "graph_node double ok < sizes",
            "graph_node decision ok < sizes",
            "graph_node big error < sizes",
            "graph_run sizes error < None",
        ]
    )
    by_name = {span["name"]: span for span in found}
    assert by_name["sizes"]["error"]["type"] == "ValueError"
    assert by_name["sizes"]["error"]["message"] == "too big"
    assert by_name["sizes"]["data"]["output"] is None
    assert by_name["big"]["error"]["type"] == "ValueError"
    assert by_name["big"]["data"]["inputs"] == 120


async def test_parallel_batch_puts_agent_runs_under_the_graph_run() -> None:
    """The documented limit: with several tasks in a batch there is no single node to attach."""
    graph, _ = build_fan_out()
    with record(graph) as session:
        assert sorted(await run_graph(graph, inputs=[2, 3])) == [4, 9]
        found = spans(session.events, "span.started")
    by_id = {span["span_id"]: span for span in found}
    squares = [span for span in found if span["name"] == "square"]
    assert [span["data"]["inputs"] for span in squares] == [2, 3]
    assert len({span["parent_id"] for span in squares}) == 1  # siblings under the graph run
    agent_runs = [span for span in found if span["kind"] == "agent_run"]
    assert len(agent_runs) == 2
    assert {by_id[span["parent_id"]]["kind"] for span in agent_runs} == {"graph_run"}
    assert sorted({span["name"] for span in found if span["kind"] == "graph_node"}) == snapshot(
        ["__end__", "map", "reduce_list_append", "source", "square"]
    )


def build_staggered() -> Graph[None, None, list[float], list[float]]:
    """Maps a list over ``slow`` -> ``after`` (runs an agent) and joins the results.

    ``slow`` sleeps for the size of its input, then raises if the input is negative.
    """
    helper = Agent(TestModel(custom_output_text="noted"), name="helper")
    builder = GraphBuilder(name="staggered", input_type=list[float], output_type=list[float])

    @builder.step
    async def source(ctx: StepContext[None, None, list[float]]) -> list[float]:
        return ctx.inputs

    @builder.step
    async def slow(ctx: StepContext[None, None, float]) -> float:
        await asyncio.sleep(abs(ctx.inputs))
        if ctx.inputs < 0:
            raise ValueError("slow failed")
        return ctx.inputs

    @builder.step
    async def after(ctx: StepContext[None, None, float]) -> float:
        await helper.run("after")
        return ctx.inputs

    collect = builder.join(reduce_list_append, initial_factory=list[float])
    builder.add(
        builder.edge_from(builder.start_node).to(source),
        builder.edge_from(source).map().to(slow),
        builder.edge_from(slow).to(after),
        builder.edge_from(after).to(collect),
        builder.edge_from(collect).to(builder.end_node),
    )
    return builder.build()


def duration(span: dict[str, Any]) -> float:
    return span["end_ms"] - span["start_ms"]


async def test_parallel_node_spans_end_when_their_own_task_finishes() -> None:
    graph = build_staggered()
    with record(graph) as session:
        assert await run_graph(graph, inputs=[0.05, 0.5]) == [0.05, 0.5]
        found = spans(session.events)
    by_id = {span["span_id"]: span for span in found}
    quick, long = sorted((span for span in found if span["name"] == "slow"), key=lambda span: span["data"]["inputs"])
    # Each step lasts as long as its own sleep, not as long as its sibling's.
    assert duration(quick) < 300
    assert duration(long) >= 400
    # A join only folds a value in: it is over at once, however long the other branch still runs.
    joins = [span for span in found if span["name"] == "reduce_list_append"]
    assert len(joins) == 2
    assert all(duration(join) < 200 for join in joins)
    # A step that follows a parallel one runs alone in its branch, so its agent run nests under it.
    agent_runs = [span for span in found if span["kind"] == "agent_run"]
    assert len(agent_runs) == 2
    for agent_run in agent_runs:
        node = by_id[agent_run["parent_id"]]
        assert (node["kind"], node["name"]) == ("graph_node", "after")
        assert node["start_ms"] <= agent_run["start_ms"] <= agent_run["end_ms"] <= node["end_ms"]


async def test_failure_in_a_parallel_branch_is_pinned_on_the_node_that_raised() -> None:
    graph = build_staggered()
    with record(graph) as session:
        with pytest.raises(ValueError, match="slow failed"):
            await run_graph(graph, inputs=[0.05, -0.4])
        found = [span for span in spans(session.events) if span["kind"].startswith("graph")]
    assert [(s["name"], s["data"].get("inputs"), s["status"]) for s in found if s["status"] == "error"] == [
        ("slow", -0.4, "error"),
        ("staggered", [0.05, -0.4], "error"),
    ]
    assert found[-2]["error"]["message"] == "slow failed"


async def test_failure_with_several_steps_in_flight_marks_only_the_graph_run() -> None:
    """Which of the running steps raised is not known, so none of them is blamed."""
    graph = build_staggered()
    with record(graph) as session:
        with pytest.raises(ValueError, match="slow failed"):
            await run_graph(graph, inputs=[-0.05, 0.5])
        started = spans(session.events, "span.started")
        found = [span for span in spans(session.events) if span["kind"].startswith("graph")]
    assert [(s["kind"], s["name"]) for s in found if s["status"] == "error"] == [("graph_run", "staggered")]
    assert len(found) == len(started)  # the steps still running were closed too


async def test_cancelled_run_is_recorded_as_failed_and_still_cancels() -> None:
    graph = build_staggered()
    with record(graph) as session:
        task = asyncio.ensure_future(run_graph(graph, inputs=[5.0]))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        found = [span for span in spans(session.events) if span["kind"].startswith("graph")]
    assert [(s["name"], s["status"]) for s in found] == [
        ("source", "ok"),
        ("map", "ok"),
        ("slow", "error"),
        ("staggered", "error"),
    ]
    assert found[-2]["error"] == {"type": "Error", "message": "cancelled", "stacktrace": None}
    assert found[-1]["data"]["output"] is None


async def test_content_capture_off_omits_inputs_and_output() -> None:
    graph, _ = build_pipeline()
    with record(graph, include_content=False) as session:
        assert await run_graph(graph, state=Tally(), inputs=2) == "small 4"
        found = spans(session.events)
    graph_spans = [span for span in found if span["kind"] in ("graph_run", "graph_node")]
    assert len(graph_spans) == 5
    assert [span["data"].get("inputs") for span in graph_spans] == [None] * 5
    assert next(span for span in found if span["kind"] == "graph_run")["data"]["output"] is None


async def test_explicit_session_wins_over_the_active_one() -> None:
    graph, _ = build_pipeline()
    with record() as active, record(instrument=False) as chosen_then_newest:
        with record(instrument=False) as newest:
            await run_graph(graph, state=Tally(), inputs=2, session=chosen_then_newest)
            assert spans(newest.events) == []
        graph_spans = [span for span in spans(chosen_then_newest.events) if span["kind"].startswith("graph")]
        assert len(graph_spans) == 5
        # The agent inside the step is instrumented by the instrumenting session, as always.
        assert [span["kind"] for span in spans(active.events)] == ["model_request", "agent_run"]


async def test_without_a_session_the_graph_just_runs() -> None:
    graph, _ = build_pipeline()
    assert await run_graph(graph, state=Tally(), inputs=2) == "small 4"
    closed = record()
    closed.close()
    assert await run_graph(graph, state=Tally(), inputs=20, session=closed) == "big 40"
    assert [envelope["type"] for envelope in closed.events] == ["session.started"]
    with pytest.raises(ValueError, match="too big"):
        await run_graph(graph, state=Tally(), inputs=60)


async def test_an_unnamed_graph_is_not_named_after_our_local_variable() -> None:
    graph, _ = build_pipeline(name=None)
    await run_graph(graph, state=Tally(), inputs=2)
    assert graph.name is None
    with record() as session:
        await run_graph(graph, state=Tally(), inputs=2)
        found = spans(session.events)
    assert graph.name is None
    run = next(span for span in found if span["kind"] == "graph_run")
    assert (run["name"], run["data"]["graph_name"]) == ("graph-1", "graph-1")


def test_run_graph_is_public() -> None:
    assert "run_graph" in pydantic_ai_viz.__all__
    assert pydantic_ai_viz.run_graph is run_graph
