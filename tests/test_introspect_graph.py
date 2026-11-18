from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from inline_snapshot import snapshot
from pydantic_graph import BaseNode, End, Graph, GraphBuilder, GraphRunContext, StepContext, reduce_list_append

from pydantic_ai_viz import GraphBlueprint, _compat, blueprint
from pydantic_ai_viz._introspect import introspect_graph, walk_graph

AnyGraph = Graph[Any, Any, Any, Any]


@dataclass
class State:
    n: int = 0


# A graph of BaseNode classes: edges come from the return annotations of run().


@dataclass
class Increment(BaseNode[State, None, int]):
    async def run(self, ctx: GraphRunContext[State, None]) -> Check:
        ctx.state.n += 1
        return Check()


@dataclass
class Check(BaseNode[State, None, int]):
    async def run(self, ctx: GraphRunContext[State, None]) -> Increment | End[int]:
        return End(ctx.state.n) if ctx.state.n >= 3 else Increment()


def _node_graph() -> AnyGraph:
    builder = GraphBuilder(name="counter", state_type=State, output_type=int)
    builder.add(builder.edge_from(builder.start_node).to(Increment), builder.node(Increment), builder.node(Check))
    return builder.build()


def _step_graph() -> AnyGraph:
    builder = GraphBuilder(name="sizes", state_type=State, input_type=int, output_type=str)

    @builder.step
    async def double(ctx: StepContext[State, None, int]) -> int:
        return ctx.inputs * 2

    @builder.step(label="Big number")
    async def big(ctx: StepContext[State, None, int]) -> str:
        return f"big {ctx.inputs}"

    @builder.step
    async def small(ctx: StepContext[State, None, int]) -> str:
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
    return builder.build()


def _fan_out_graph() -> AnyGraph:
    builder = GraphBuilder(input_type=list[int], output_type=list[int])

    @builder.step
    async def source(ctx: StepContext[None, None, list[int]]) -> list[int]:
        return ctx.inputs

    @builder.step
    async def square(ctx: StepContext[None, None, int]) -> int:
        return ctx.inputs**2

    @builder.step
    async def negate(ctx: StepContext[None, None, int]) -> int:
        return -ctx.inputs

    collect = builder.join(reduce_list_append, initial_factory=list[int])
    builder.add(
        builder.edge_from(builder.start_node).to(source),
        builder.edge_from(source).map().label("each").to(square, negate),
        builder.edge_from(square, negate).to(collect),
        builder.edge_from(collect).to(builder.end_node),
    )
    return builder.build()


GRAPHS = {"nodes": _node_graph, "steps": _step_graph, "fan-out": _fan_out_graph}


def _graph_blueprint(graph: AnyGraph) -> GraphBlueprint:
    result = blueprint(graph)
    assert isinstance(result, GraphBlueprint)
    assert GraphBlueprint.model_validate(result.model_dump(mode="json")) == result
    return result


# ---------------------------------------------------------------- blueprints


def test_base_node_graph() -> None:
    assert _graph_blueprint(_node_graph()).model_dump(mode="json") == snapshot(
        {
            "id": "g1",
            "name": "counter",
            "label": "counter",
            "state_type": "test_introspect_graph.State",
            "input_type": None,
            "output_type": "int",
            "nodes": [
                {"id": "__start__", "kind": "start", "label": None, "note": None},
                {"id": "Increment", "kind": "step", "label": None, "note": None},
                {"id": "Check", "kind": "step", "label": None, "note": None},
                {"id": "decision", "kind": "decision", "label": None, "note": None},
                {"id": "__end__", "kind": "end", "label": None, "note": None},
            ],
            "edges": [
                {"source": "__start__", "target": "Increment", "label": None},
                {"source": "Increment", "target": "Check", "label": None},
                {"source": "Check", "target": "decision", "label": None},
                {"source": "decision", "target": "Increment", "label": None},
                {"source": "decision", "target": "__end__", "label": None},
            ],
            "mermaid": """\
stateDiagram-v2
  direction LR
  Increment
  Check
  state decision <<choice>>

  [*] --> Increment
  Increment --> Check
  Check --> decision
  decision --> Increment
  decision --> [*]\
""",
            "warnings": [],
        }
    )


def test_step_graph_with_decision() -> None:
    assert _graph_blueprint(_step_graph()).model_dump(mode="json") == snapshot(
        {
            "id": "g1",
            "name": "sizes",
            "label": "sizes",
            "state_type": "test_introspect_graph.State",
            "input_type": "int",
            "output_type": "str",
            "nodes": [
                {"id": "__start__", "kind": "start", "label": None, "note": None},
                {"id": "double", "kind": "step", "label": None, "note": None},
                {"id": "decision", "kind": "decision", "label": None, "note": "size?"},
                {"id": "big", "kind": "step", "label": "Big number", "note": None},
                {"id": "small", "kind": "step", "label": None, "note": None},
                {"id": "__end__", "kind": "end", "label": None, "note": None},
            ],
            "edges": [
                {"source": "__start__", "target": "double", "label": None},
                {"source": "double", "target": "decision", "label": None},
                {"source": "decision", "target": "big", "label": ">10"},
                {"source": "decision", "target": "small", "label": "otherwise"},
                {"source": "big", "target": "__end__", "label": None},
                {"source": "small", "target": "__end__", "label": None},
            ],
            "mermaid": """\
stateDiagram-v2
  direction LR
  double
  state decision <<choice>>
  note right of decision
    size?
  end note
  big: Big number
  small

  [*] --> double
  double --> decision
  decision --> big: >10
  decision --> small: otherwise
  big --> [*]
  small --> [*]\
""",
            "warnings": [],
        }
    )


def test_fan_out_graph_node_kinds_and_edges() -> None:
    result = _graph_blueprint(_fan_out_graph())
    assert [(n.id, n.kind) for n in result.nodes] == snapshot(
        [
            ("__start__", "start"),
            ("source", "step"),
            ("square", "step"),
            ("negate", "step"),
            ("map", "map"),
            ("broadcast", "broadcast"),
            ("reduce_list_append", "join"),
            ("__end__", "end"),
        ]
    )
    # Compared as a set: pydantic-graph 2.60 and 2.61 list the branches of a broadcast in the
    # opposite order to later releases, and the order carries no meaning.
    assert {(e.source, e.target, e.label) for e in result.edges} == snapshot(
        {
            ("__start__", "source", None),
            ("source", "map", None),
            ("square", "reduce_list_append", None),
            ("negate", "reduce_list_append", None),
            ("map", "broadcast", "each"),
            ("broadcast", "square", None),
            ("broadcast", "negate", None),
            ("reduce_list_append", "__end__", None),
        }
    )
    assert len(result.edges) == 8
    assert (result.name, result.label, result.state_type) == (None, "graph-1", None)
    assert (result.input_type, result.output_type) == ("list[int]", "list[int]")


def test_session_seam_id_and_label_come_from_the_caller() -> None:
    result = introspect_graph(_fan_out_graph(), id="g4", label="fan_out")
    assert (result.id, result.name, result.label) == ("g4", None, "fan_out")
    assert introspect_graph(_fan_out_graph(), id="g4").label == "graph-4"
    assert introspect_graph(_step_graph(), id="g4", label="ignored").label == "sizes"


@pytest.mark.anyio
async def test_blueprint_matches_the_graph_that_runs() -> None:
    graph = _step_graph()
    result = _graph_blueprint(graph)
    visited: list[str] = []
    async with graph.iter(state=State(), inputs=7) as run:
        async for batch in run:
            if isinstance(batch, list):
                visited.extend(str(task.node_id) for task in batch)
    assert run.output == "big 14"
    assert set(visited) <= {node.id for node in result.nodes}
    assert visited == snapshot(["double", "decision", "big", "__end__"])


# ------------------------------------------------------------------ fallback


def test_compat_contract_build_mermaid_graph_exists() -> None:
    build = _compat.load_build_mermaid_graph()
    assert build is not None, "pydantic_graph.graph_builder.build_mermaid_graph is gone"
    graph = _step_graph()
    mermaid = build(graph.nodes, graph.edges_by_source)
    assert {"id", "kind", "label", "note"} <= set(vars(mermaid.nodes[0]))
    assert {"start_id", "end_id", "label"} <= set(vars(mermaid.edges[0]))


@pytest.mark.parametrize("name", GRAPHS)
def test_fallback_walker_agrees_with_build_mermaid_graph(name: str) -> None:
    graph = GRAPHS[name]()
    result = _graph_blueprint(graph)
    nodes, edges = walk_graph(graph)
    assert nodes == result.nodes
    assert edges == result.edges
    assert result.warnings == []


@pytest.mark.parametrize("name", GRAPHS)
def test_forced_fallback_gives_the_same_blueprint_plus_a_warning(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = GRAPHS[name]()
    expected = _graph_blueprint(graph)
    monkeypatch.setattr(_compat, "load_build_mermaid_graph", lambda: None)

    result = _graph_blueprint(graph)

    assert result.warnings == [
        "build_mermaid_graph unavailable (pydantic-graph internals changed); used the public graph fields"
    ]
    assert result.model_copy(update={"warnings": []}) == expected


def test_fallback_when_build_mermaid_graph_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> Any:
        raise ValueError("new signature")

    graph = _step_graph()
    expected = _graph_blueprint(graph)
    monkeypatch.setattr(_compat, "load_build_mermaid_graph", lambda: broken)

    result = _graph_blueprint(graph)

    assert result.warnings == snapshot(
        ["build_mermaid_graph failed (ValueError: new signature); used the public graph fields"]
    )
    assert (result.nodes, result.edges, result.mermaid) == (expected.nodes, expected.edges, expected.mermaid)


def test_blueprint_survives_build_mermaid_graph_being_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real upgrade scenario: the function is gone, so ``graph.render()`` breaks too."""
    graph = _step_graph()
    expected = _graph_blueprint(graph)
    monkeypatch.delattr(f"{_compat.MERMAID_MODULE}.{_compat.MERMAID_FUNCTION}")
    assert _compat.load_build_mermaid_graph() is None

    result = _graph_blueprint(graph)

    assert (result.nodes, result.edges) == (expected.nodes, expected.edges)
    assert result.mermaid is None
    assert result.warnings == snapshot(
        [
            "build_mermaid_graph unavailable (pydantic-graph internals changed); used the public graph fields",
            "mermaid diagram unavailable (NameError: name 'build_mermaid_graph' is not defined)",
        ]
    )


def test_unreadable_graph_still_yields_a_valid_blueprint() -> None:
    class Hollow:
        name = None
        state_type = input_type = output_type = None

    result = introspect_graph(Hollow(), id="g1")  # pyright: ignore[reportArgumentType]

    assert GraphBlueprint.model_validate(result.model_dump(mode="json")) == result
    assert (result.nodes, result.edges, result.mermaid, result.label) == ([], [], None, "graph-1")
    assert len(result.warnings) == 3
