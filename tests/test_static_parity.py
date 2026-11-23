"""Accuracy: the bundled demos read from source say what their live objects say.

The demos are ours, so they can be imported. Each agent and graph is described
twice, by ``blueprint(obj)`` from the live object and by ``analyze(file)`` from
the source text, and the two descriptions must agree on everything the source
spells out.
"""

from __future__ import annotations

import textwrap
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph
from static_support import live_agents, without_titles

from pydantic_ai_viz import AgentBlueprint, Analysis, GraphBlueprint, Limits, analyze, record

DEMOS = ["support", "research", "pipeline"]
ALL_DEMOS = [
    *DEMOS,
    *(
        pytest.param(
            name,
            marks=pytest.mark.skipif(find_spec("pydantic_ai_harness") is None, reason="needs pydantic-ai-harness"),
        )
        for name in ("capabilities", "operations")
    ),
]


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
    # These tests check accuracy, not speed: a slow machine must not cut the analysis short.
    return live, analyze(Path(module.__file__), limits=Limits(max_seconds=600))


@pytest.mark.parametrize("name", ALL_DEMOS)
def test_the_same_agents_and_graphs_are_found(name: str) -> None:
    live, static = live_and_static(name)
    assert static.notices == []
    assert sorted(b.label for b in static.blueprints) == sorted(b.label for b in live)
    assert all(b.source == "code" and b.location is not None for b in static.blueprints)
    assert all(b.location.file == f"{name}.py" for b in static.blueprints if b.location is not None)


@pytest.mark.parametrize("name", DEMOS)
def test_agents_read_from_source_match_the_live_agents(name: str) -> None:
    live, static = live_and_static(name)
    assert static.notices == []
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


@pytest.mark.parametrize("name", ALL_DEMOS)
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
        # The demo's skills are read from a workspace its code makes, so the source says where it looked.
        assert found.warnings == (
            [
                "`Skills` reads `_skills` from a workspace decided when the code runs; the skills shown are those "
                "in `_skills`"
            ]
            if name == "capabilities"
            else []
        )
        # Pydantic AI's defaults are not passed off as something the source says.
        assert found.settings.end_strategy is None
        if name in DEMOS:
            assert (found.capabilities, found.native_tools) == ([], [])


def test_the_capabilities_read_from_source_match_the_live_agent() -> None:
    pytest.importorskip("pydantic_ai_harness")
    live, static = live_and_static("capabilities")
    (expected,) = (b for b in live if isinstance(b, AgentBlueprint))
    (found,) = static.agents
    # Pydantic AI adds some capabilities to every agent and the source never names them. The order of
    # the others is the library's (a guardrail wraps the run, so it may come last), so compare by name.
    own = [c for c in expected.capabilities if not c.builtin]
    assert sorted(
        (c.name, c.category, c.package, c.deferred, c.native_tools, c.toolsets) for c in found.capabilities
    ) == sorted((c.name, c.category, c.package, c.deferred, c.native_tools, c.toolsets) for c in own)
    assert not any(c.builtin for c in found.capabilities)
    # Native tools of the agent: those of the capabilities it was given.
    kinds = {kind for c in expected.capabilities if c.builtin for kind in c.native_tools}
    assert found.native_tools == [kind for kind in expected.native_tools if kind not in kinds]
    # A harness toolset has the tools its arguments decide, so the source can only say it is there.
    assert [(t.toolset_id, t.label, t.kind, t.wrappers) for t in found.toolsets] == [
        (t.toolset_id, t.label, t.kind, t.wrappers) for t in expected.toolsets
    ]
    harness = {c.name for c in found.capabilities if c.package == "pydantic_ai_harness"}
    owners = {id_: c.name for c in found.capabilities for id_ in c.toolsets}
    for toolset in found.toolsets:
        assert toolset.opaque == (owners[toolset.id] in harness)
    ignored = {"origin", "location"}
    drawn = {t.id for t in found.toolsets if not t.opaque}
    assert [t.model_dump(exclude=ignored) for t in found.tools if t.toolset in drawn] == [
        t.model_dump(exclude=ignored) for t in expected.tools if t.toolset in drawn
    ]
    # The skills, as the files the source names hold them and the live agent reads them.
    assert [(c.name, c.skills) for c in found.capabilities if c.skills] == [(c.name, c.skills) for c in own if c.skills]


def test_harness_toolsets_merged_capabilities_and_given_instructions_read_as_the_live_agent(tmp_path: Path) -> None:
    pytest.importorskip("pydantic_ai_harness")
    path = tmp_path / "team.py"
    path.write_text(
        textwrap.dedent(
            """
            from pydantic_ai import Agent
            from pydantic_ai.capabilities import Capability, Thinking, WebSearch
            from pydantic_ai.models.test import TestModel
            from pydantic_ai_harness import Advisor, AskUser, Skills, SubAgent, SubAgents

            def lookup(query: str) -> str:
                \"\"\"Look it up.\"\"\"
                return query

            def answer(question: str) -> str:
                return "yes"

            helper = Agent(TestModel(), name="helper")
            toolsets = Agent(
                TestModel(),
                name="toolsets",
                capabilities=[
                    AskUser(answerer=answer),
                    SubAgents(agents=[SubAgent(helper, name="h", description="Helps.")]),
                    Advisor("test"),
                    Skills(".", read_files=True),
                    WebSearch(local=lookup, allowed_domains=["a.example"]),
                ],
            )
            merged = Agent(
                TestModel(),
                name="merged",
                capabilities=[WebSearch(), Capability(instructions="Be kind."), WebSearch(max_uses=2)],
            )

            def other(query: str) -> str:
                \"\"\"Look elsewhere.\"\"\"
                return query

            later = Agent(TestModel(), name="later", capabilities=[WebSearch(local=lookup), WebSearch(local=other)])
            kept = Agent(TestModel(), name="kept", capabilities=[WebSearch(local=lookup), WebSearch()])
            native_only = Agent(
                TestModel(), name="native_only", capabilities=[WebSearch(local=lookup), WebSearch(max_uses=2)]
            )
            defaulted = Agent(
                TestModel(),
                name="defaulted",
                capabilities=[Thinking(effort="high"), Thinking(), WebSearch(local=lookup, native=False), WebSearch()],
            )
            """
        ),
        encoding="utf-8",
    )
    live = live_agents(path)
    static = {b.label: b for b in analyze(path).agents}
    labels = {b.id: b.label for b in static.values()}

    def toolsets(found: AgentBlueprint) -> list[tuple[object, ...]]:
        return [(t.toolset_id, t.label, t.kind, t.wrappers) for t in found.toolsets]

    assert toolsets(static["toolsets"]) == toolsets(live["toolsets"])
    # Each live agent is described on its own, so its delegate is ``a2`` of its own listing: the helper.
    assert [(d.tool_name, labels[d.agent_id]) for d in static["toolsets"].delegates] == [
        (d.tool_name, "helper") for d in live["toolsets"].delegates if d.agent_id == "a2"
    ]
    assert static["toolsets"].delegates
    for name in ("merged", "later", "kept", "native_only", "defaulted"):
        # Merged by class and id: the toolsets are made from the merged arguments, as the live agent makes them.
        own = [c for c in live[name].capabilities if not c.builtin]
        assert [(c.name, c.details, c.native_tools, c.toolsets) for c in static[name].capabilities] == [
            (c.name, c.details, c.native_tools, c.toolsets) for c in own
        ], name
        assert toolsets(static[name]) == toolsets(live[name]), name
        assert [t.name for t in static[name].tools] == [t.name for t in live[name].tools], name


def test_output_validators_read_from_source_match_the_live_agent(tmp_path: Path) -> None:
    path = tmp_path / "checked.py"
    path.write_text(
        textwrap.dedent(
            """
            from pydantic_ai import Agent, ModelRetry
            from pydantic_ai.models.test import TestModel

            checked = Agent(TestModel(), name="checked")
            plain = Agent(TestModel(), name="plain")

            @checked.output_validator
            def not_empty(ctx, output: str) -> str:
                return output

            @checked.output_validator
            async def short(ctx, output: str) -> str:
                return output

            checked.output_validator(lambda output: output)
            """
        ),
        encoding="utf-8",
    )
    live = live_agents(path)
    static = {b.label: b for b in analyze(path).agents}
    # The live module is loaded under another name than the file's, so only the function's own name is compared.
    for name, expected in (("checked", ["not_empty", "short", "<lambda>"]), ("plain", [])):
        for found in (static[name], live[name]):
            assert [v.rsplit(".", 1)[-1] for v in found.output.validators] == expected, name
    assert static["checked"].output.validators[-1] == "checked.<lambda>"


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
