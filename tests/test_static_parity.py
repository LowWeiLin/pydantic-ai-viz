"""Accuracy: the bundled demos read from source say what their live objects say.

The demos are ours, so they can be imported. Each agent and graph is described
twice, by ``blueprint(obj)`` from the live object and by ``analyze(file)`` from
the source text, and the two descriptions must agree on everything the source
spells out.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph
from static_support import without_titles

from pydantic_ai_viz import AgentBlueprint, Analysis, GraphBlueprint, analyze, record

DEMOS = ["support", "research", "pipeline"]


def live_and_static(name: str) -> tuple[list[AgentBlueprint | GraphBlueprint], Analysis]:
    module = import_module(f"pydantic_ai_viz.demos.{name}")
    assert module.__file__ is not None
    # One listing of the whole module, so that delegates refer to ids of the same listing.
    with record(module, instrument=False) as session:
        events = session.events
    live: list[AgentBlueprint | GraphBlueprint] = []
    for event in events:
        if event["type"] == "blueprint.agent":
            live.append(AgentBlueprint.model_validate(event["agent"]))
        elif event["type"] == "blueprint.graph":
            live.append(GraphBlueprint.model_validate(event["graph"]))
    assert len(live) == sum(isinstance(value, Agent | Graph) for value in vars(module).values())
    return live, analyze(Path(module.__file__))


@pytest.mark.parametrize("name", DEMOS)
def test_the_same_agents_and_graphs_are_found(name: str) -> None:
    live, static = live_and_static(name)
    assert static.notices == []
    assert sorted(b.label for b in static.blueprints) == sorted(b.label for b in live)
    assert all(b.source == "code" and b.location is not None for b in static.blueprints)
    assert all(b.location.file == f"{name}.py" for b in static.blueprints if b.location is not None)


@pytest.mark.parametrize("name", DEMOS)
def test_agents_read_from_source_match_the_live_agents(name: str) -> None:
    live, static = live_and_static(name)
    static_agents = {b.label: b for b in static.agents}
    live_agents = [b for b in live if isinstance(b, AgentBlueprint)]
    labels = {b.id: b.label for b in static.agents}
    live_labels = {b.id: b.label for b in live_agents}
    for expected in live_agents:
        found = static_agents[expected.label]
        assert (found.name, found.description, found.deps_type) == (
            expected.name,
            expected.description,
            expected.deps_type,
        )
        assert [(i.kind, i.dynamic, i.text, i.function) for i in found.instructions] == [
            (i.kind, i.dynamic, i.text, i.function) for i in expected.instructions
        ]
        assert [(t.id, t.toolset_id, t.label, t.kind, t.wrappers, t.opaque) for t in found.toolsets] == [
            (t.id, t.toolset_id, t.label, t.kind, t.wrappers, t.opaque) for t in expected.toolsets
        ]
        # Tools: every field but where the fact came from, the JSON Schemas included.
        ignored = {"origin", "location"}
        assert [t.model_dump(exclude=ignored) for t in found.tools] == [
            t.model_dump(exclude=ignored) for t in expected.tools
        ]
        assert (found.output.mode, found.output.type_name) == (expected.output.mode, expected.output.type_name)
        assert without_titles(found.output.json_schema) == without_titles(expected.output.json_schema)
        assert found.settings.model_settings == expected.settings.model_settings
        # A setting the source gives is the one in force; one it leaves out stays unknown.
        for setting in ("tool_retries", "output_retries", "end_strategy", "tool_timeout"):
            assert getattr(found.settings, setting) in (None, getattr(expected.settings, setting))
        # Delegation, by the label of the agent delegated to: each listing numbers its agents itself.
        assert [(d.tool_name, labels[d.agent_id]) for d in found.delegates] == [
            (d.tool_name, live_labels[d.agent_id]) for d in expected.delegates
        ]


@pytest.mark.parametrize("name", DEMOS)
def test_a_helper_in_another_file_is_followed_and_defaults_are_not_made_up(name: str) -> None:
    _, static = live_and_static(name)
    for found in static.agents:
        # The demos script their models with a helper from another file. That file is read as well,
        # so the model is known to be a FunctionModel; the expression stays the one at the call site.
        assert found.model is not None
        assert found.model.id is None
        assert found.model.expression is not None
        assert found.model.expression.startswith("scripted_model(")
        assert (found.model.provider, found.model.offline) == ("function", True)
        assert found.warnings == []
        # Pydantic AI's defaults are not passed off as something the source says.
        assert found.settings.end_strategy is None
        assert (found.capabilities, found.native_tools) == ([], [])


def test_the_graph_read_from_source_matches_the_live_graph() -> None:
    live, static = live_and_static("pipeline")
    (expected,) = (b for b in live if isinstance(b, GraphBlueprint))
    (found,) = static.graphs
    ignored = {"id", "source", "location", "nodes"}
    assert found.model_dump(exclude=ignored) == expected.model_dump(exclude=ignored)
    assert [node.model_dump(exclude={"location"}) for node in found.nodes] == [
        node.model_dump(exclude={"location"}) for node in expected.nodes
    ]
    assert found.warnings == []
