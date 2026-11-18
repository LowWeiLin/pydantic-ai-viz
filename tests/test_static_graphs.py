"""Graphs read from source: what the builder calls spell out, and an honest warning for the rest."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest
from pydantic_graph import Graph
from static_support import FIXTURES, analyze_source, fixture, graph

from pydantic_ai_viz import GraphBlueprint, analyze, blueprint
from pydantic_ai_viz._analyze import _graphs
from pydantic_ai_viz._models import ENVELOPE_ADAPTER, SourceLocation, to_wire


def shape(found: GraphBlueprint) -> dict[str, Any]:
    """What a graph looks like, without what only one of the two ways of reading it can know."""
    return {
        "name": found.name,
        "types": (found.state_type, found.input_type, found.output_type),
        "nodes": [(node.id, node.kind, node.label, node.note) for node in found.nodes],
        "edges": [(edge.source, edge.target, edge.label) for edge in found.edges],
        "mermaid": found.mermaid,
    }


# ----------------------------------------------- the same graph, read both ways

RUNNABLE = {
    "graphs.py": ["pipeline", "fan_out", "wired"],
    "graphs_nodes.py": ["reach", "auto", "matchnode", "mixed", "roots", "multi"],
    "graphs_unreadable.py": ["counter"],
}


@pytest.mark.parametrize(
    ("name", "variable"), [(name, variable) for name, variables in RUNNABLE.items() for variable in variables]
)
def test_a_graph_read_from_source_is_the_graph_pydantic_graph_builds(name: str, variable: str) -> None:
    path = FIXTURES / name
    # These fixtures are ours and harmless, so they can be run to get the real graph to compare with.
    live = runpy.run_path(str(path), run_name=path.stem)[variable]
    assert isinstance(live, Graph)
    found = analyze(f"{path}:{variable}")
    assert found.agents == []
    (static,) = found.graphs
    expected = blueprint(live)
    assert isinstance(expected, GraphBlueprint)
    assert shape(static) == shape(expected)
    assert static.warnings == []
    assert (static.id, static.source) == ("g1", "code")


# ------------------------------------------------------------- builder graphs


def test_steps_decisions_and_edges_with_where_each_is_defined() -> None:
    found = fixture("graphs.py")
    assert found.notices == []
    assert [(g.id, g.label, g.name) for g in found.graphs] == [
        ("g1", "pipeline", "pipeline"),
        ("g2", "fan_out", None),  # no name: the variable the built graph is assigned to
        ("g3", "wiring", "wiring"),
    ]
    pipeline = graph(found, "pipeline")
    assert pipeline.location == SourceLocation(file="graphs.py", line=18)
    assert (pipeline.state_type, pipeline.input_type, pipeline.output_type) == ("graphs.Draft", "str", "str")
    assert [(n.id, n.kind, n.label, n.note, n.location and n.location.line) for n in pipeline.nodes] == [
        ("__start__", "start", None, None, None),
        ("write", "step", "Write draft", None, 22),
        ("review", "step", "Review", None, 27),
        ("decision", "decision", None, "approved?", 44),
        ("publish", "step", None, None, 32),
        ("__end__", "end", None, None, None),
    ]
    assert [(e.source, e.target, e.label) for e in pipeline.edges] == [
        ("__start__", "write", None),
        ("write", "review", None),
        ("review", "decision", None),
        ("decision", "publish", "approved"),
        ("decision", "write", "needs work"),
        ("publish", "__end__", None),
    ]
    assert pipeline.mermaid is not None
    assert pipeline.mermaid.splitlines()[:4] == [
        "stateDiagram-v2",
        "  direction LR",
        "  write: Write draft",
        "  review: Review",
    ]
    ENVELOPE_ADAPTER.validate_python({"seq": 1, "ts": 0.0, "type": "blueprint.graph", "graph": to_wire(pipeline)})


def test_maps_broadcasts_and_joins_become_fork_and_join_nodes() -> None:
    fan = graph(fixture("graphs.py"), "fan_out")
    assert [(n.id, n.kind) for n in fan.nodes] == [
        ("__start__", "start"),
        ("source", "step"),
        ("square", "step"),
        ("negate", "step"),
        ("map", "map"),
        ("broadcast", "broadcast"),
        ("reduce_list_append", "join"),  # named after its reducer
        ("__end__", "end"),
    ]
    assert ("map", "broadcast", "each") in [(e.source, e.target, e.label) for e in fan.edges]
    assert (fan.state_type, fan.input_type, fan.output_type) == (None, "list[int]", "list[int]")


def test_edges_added_one_at_a_time_and_ids_given_in_the_source() -> None:
    wired = graph(fixture("graphs.py"), "wiring")
    assert [(n.id, n.label, n.note) for n in wired.nodes] == [
        ("__start__", None, None),
        ("halve", "Halve it", None),
        ("size", None, "how big?"),  # node_id="size"
        ("_describe_large", None, None),  # a step made by calling builder.step(function)
        ("_describe_small", "Small", None),
        ("__end__", None, None),
    ]
    assert [(e.source, e.target, e.label) for e in wired.edges] == [
        ("__start__", "halve", "in"),
        ("halve", "size", None),
        ("size", "_describe_large", "big"),
        ("size", "_describe_small", None),
        ("_describe_large", "__end__", "out"),
        ("_describe_small", "__end__", None),
    ]


def test_node_classes_go_where_their_run_method_says_it_returns() -> None:
    found = fixture("graphs_nodes.py")
    reach = graph(found, "reach")
    assert [(n.id, n.kind, n.location and n.location.line) for n in reach.nodes] == [
        ("__start__", "start", None),
        ("first", "step", 33),
        ("A", "step", 12),  # a BaseNode class is a step named after the class
        ("decision", "decision", 12),  # `-> B | C`
        ("B", "step", 18),
        ("C", "step", 24),
        ("decision_2", "decision", 24),  # `-> Union[A, End[int]]`
        ("__end__", "end", None),
    ]
    assert [(e.source, e.target) for e in reach.edges] == [
        ("__start__", "first"),
        ("first", "A"),  # the step's own return type: `-> A`
        ("A", "decision"),
        ("decision", "B"),
        ("decision", "C"),
        ("B", "C"),  # written as the string "C"
        ("C", "decision_2"),
        ("decision_2", "A"),
        ("decision_2", "__end__"),
    ]
    mixed = graph(found, "mixed")
    # `Annotated[StepNode[...], tail]` names the step `tail`.
    assert ("decision", "tail") in [(e.source, e.target) for e in mixed.edges]


def test_a_node_that_starts_two_paths_gets_the_fork_pydantic_graph_puts_there() -> None:
    multi = graph(fixture("graphs_nodes.py"), "multi")
    assert ("origin_broadcast_fork", "broadcast") in [(n.id, n.kind) for n in multi.nodes]
    edges = [(e.source, e.target, e.label) for e in multi.edges]
    assert ("origin", "origin_broadcast_fork", None) in edges
    assert ("origin_broadcast_fork", "left", None) in edges
    assert ("origin_broadcast_fork", "right", "r") in edges
    assert not any(source == "origin" and target in ("left", "right") for source, target, _ in edges)


def test_a_graph_or_its_builder_can_be_selected_by_name() -> None:
    path = FIXTURES / "graphs.py"
    by_graph = analyze(f"{path}:wired").graphs
    by_builder = analyze(f"{path}:wiring").graphs
    assert [g.label for g in by_graph] == [g.label for g in by_builder] == ["wiring"]
    assert by_graph[0].id == "g1"  # renumbered: it is the only one shown


def test_builders_in_functions_and_unnamed_graphs(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import pydantic_graph
        from pydantic_graph import GraphBuilder as Builder

        def make():
            builder = Builder(state_type=dict, output_type=None)

            @builder.step
            async def only(ctx):
                return None

            builder.add(builder.edge_from(builder.start_node).to(only), builder.edge_from(only).to(builder.end_node))
            return builder.build()

        pydantic_graph.GraphBuilder(name=compute_name())
        """,
    )
    made, nameless = found.graphs
    # No name, so the label is where the built graph goes: out of `make`.
    assert (made.label, made.name, made.state_type, made.output_type) == ("make", None, "dict", None)
    assert [n.id for n in made.nodes] == ["__start__", "only", "__end__"]
    assert made.warnings == []
    assert (nameless.label, nameless.name, nameless.nodes) == ("graph-2", None, [])
    assert len(nameless.warnings) == 1


def test_only_pydantic_graph_builders_are_graphs(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from somewhere_else import GraphBuilder
        from pydantic_graph import Graph

        builder = GraphBuilder(name="not ours")
        builder.add(builder.edge_from(builder.start_node).to(builder.end_node))

        class Graph2(Graph): ...
        annotated: Graph[None, None, str, str]
        """,
    )
    assert found.graphs == []


# ------------------------------------------------- what cannot be read is said


def test_edges_added_in_a_loop_are_reported_as_unread_and_point_at_import() -> None:
    looped = graph(fixture("graphs_unreadable.py"), "looped")
    # The edge written out is there; the two added by the loop are not invented.
    assert [(e.source, e.target) for e in looped.edges] == [("__start__", "first")]
    assert looped.warnings == [
        "part of this graph is built by `looped_builder.edge_from(before).to(after)`, which cannot be read from "
        "source; run `pydantic-ai-viz view --import` on trusted code to see the whole graph"
    ]


def test_a_graph_whose_paths_come_from_a_helper_lists_its_steps_and_says_so() -> None:
    helped = graph(fixture("graphs_unreadable.py"), "helped")
    assert [(n.id, n.kind) for n in helped.nodes] == [("alone", "step"), ("also_alone", "step")]
    assert (helped.edges, helped.mermaid) == ([], None)
    assert helped.warnings == [
        "the edges of this graph could not be read from source; run `pydantic-ai-viz view --import` on trusted "
        "code to see the whole graph",
        "part of this graph is built by `*make_paths(helped)`, which cannot be read from source; run "
        "`pydantic-ai-viz view --import` on trusted code to see the whole graph",
    ]


def test_paths_the_reader_does_not_understand_are_named_not_guessed(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_graph import BaseNode, GraphBuilder
        from other_package import Imported

        b = GraphBuilder(name="odd")

        @b.step
        async def one(ctx) -> int: ...

        class Vague(BaseNode):
            async def run(self, ctx) -> BaseNode: ...

        class Named(BaseNode):
            @classmethod
            def get_node_id(cls):
                return compute()

            async def run(self, ctx) -> Vague: ...

        b.add(b.edge_from(b.start_node).to(one))
        b.add(b.edge_from(one).to(unknown_step))
        b.add(b.edge_from(one).to(Imported))
        b.add(b.node(Vague), b.node(Imported), b.node(Named))
        b.add(some_path)
        b.add(b.edge_from(one).to(b.decision().branch(b.match(int).to(nowhere))))
        b.add(b.edge_from(one).frobnicate().to(b.end_node))
        b.add_reachable(Vague, Imported)
        """,
    )
    (odd,) = found.graphs
    assert [(e.source, e.target) for e in odd.edges] == [("__start__", "one")]
    unread = [warning.split("`")[1] for warning in odd.warnings]
    assert unread == [
        "b.edge_from(one).to(unknown_step)",
        "b.edge_from(one).to(Imported)",  # a class from a package that is not analysed
        "b.node(Vague)",  # `-> BaseNode` does not say which node
        "b.node(Imported)",
        "b.node(Named)",  # the class computes its own node id
        "some_path",
        "b.edge_from(one).to(b.decision().branch(b.match(int).to(nowhere)))",
        "b.edge_from(one).frobnicate().to(b.end_node)",
        "Imported",
        "Vague.run",
    ]
    assert all("--import" in warning for warning in odd.warnings)


def test_a_graph_too_large_to_follow_to_the_end_says_that_it_is_cut_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ring = "".join(
        f"class N{index}(BaseNode):\n    async def run(self, ctx) -> 'N{(index + 1) % 12}': ...\n"
        for index in range(12)
    )
    source = f"from pydantic_graph import BaseNode, GraphBuilder\nb = GraphBuilder()\n{ring}b.add_reachable(N0)\n"
    (whole,) = analyze_source(tmp_path, source).graphs
    assert (len(whole.nodes), whole.warnings) == (12, [])

    monkeypatch.setattr(_graphs, "_MAX_REACHABLE", 5)
    (cut,) = analyze_source(tmp_path, source).graphs
    assert 0 < len(cut.nodes) < 12
    assert [warning.split("`")[1] for warning in cut.warnings] == ["b.add_reachable(N0)"]


def test_deeply_nested_decisions_are_given_up_on_not_followed_for_ever(tmp_path: Path) -> None:
    nested = "b.decision().branch(b.match(int).to(" * 40 + "s" + "))" * 40
    source = (
        "from pydantic_graph import GraphBuilder\nb = GraphBuilder()\n@b.step\nasync def s(ctx): ...\n"
        f"b.add(b.edge_from(b.start_node).to({nested}))\n"
    )
    (found,) = analyze_source(tmp_path, source).graphs
    assert found.edges == []
    assert len(found.warnings) == 2


# --------------------------------------------------------- Graph(nodes=[...])


def test_a_graph_given_as_a_list_of_node_classes_is_read_from_their_return_types() -> None:
    found = fixture("graphs_classic.py")
    quiz = graph(found, "quiz")
    assert (quiz.name, quiz.state_type, quiz.input_type, quiz.output_type) == (
        "quiz",
        "graphs_classic.QuizState",
        None,
        "str",
    )
    assert [(n.id, n.kind, n.location and n.location.line) for n in quiz.nodes] == [
        ("__start__", "start", None),
        ("Ask", "step", 24),
        ("Answer", "step", 33),
        ("Evaluate", "step", 41),
        ("Reprimand", "step", 53),
        ("__end__", "end", None),
    ]
    assert [(e.source, e.target, e.label) for e in quiz.edges] == [
        ("__start__", "Ask", None),  # from `quiz.run(Ask(), ...)`
        ("Ask", "Answer", None),
        ("Answer", "Evaluate", None),
        ("Evaluate", "__end__", "correct"),  # Annotated[End[str], Edge(label="correct")]
        ("Evaluate", "Reprimand", "wrong"),
        ("Reprimand", "Ask", None),
    ]
    assert quiz.warnings == []
    assert quiz.mermaid is not None
    assert "  Evaluate --> [*]: correct" in quiz.mermaid.splitlines()


def test_what_a_list_of_node_classes_does_not_say_is_warned_about() -> None:
    found = fixture("graphs_classic.py")
    crawler = graph(found, "crawler")
    assert [n.id for n in crawler.nodes] == ["Fetch", "Parse", "Retry", "Archive", "__end__"]
    assert [(e.source, e.target) for e in crawler.edges] == [
        ("Fetch", "Parse"),
        ("Fetch", "Archive"),  # imported, but listed as a node
        ("Fetch", "__end__"),
        ("Parse", "Fetch"),
        ("Parse", "Retry"),
    ]
    assert crawler.warnings == [
        "the node this graph starts at is chosen where the graph is run, and no such call was found in the "
        "analysed source",
        "the node `Archive` is not a node class defined in the analysed source, so where it leads is not shown",
        "`Parse.run` can return `int`, which is not a node class defined in the analysed source, so that edge is "
        "not shown",
        "`Retry.run` does not declare what it returns in a way that can be read, so the edges out of `Retry` are "
        "not shown",
    ]
    computed = graph(found, "computed")
    assert (computed.nodes, computed.edges, computed.mermaid) == ([], [], None)
    assert computed.warnings == [
        "the nodes of this graph are given by `discover_nodes()`, so they are only known when the code runs"
    ]
    # The installed pydantic-graph cannot import this style of graph, so no warning sends the reader to --import.
    assert not any("--import" in warning for g in found.graphs for warning in g.warnings)
