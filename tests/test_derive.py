from __future__ import annotations

import json
from typing import Any

import pytest
from inline_snapshot import snapshot
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from pydantic_ai import Agent, InstrumentationSettings, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset
from scenarios import PROMPT, Deps, build_team

from pydantic_ai_viz import AgentBlueprint, blueprint, record
from pydantic_ai_viz._derive import add_delegate, carry_observed, merge_request, observed_blueprint
from pydantic_ai_viz._models import Delegate, parse_envelope

pytestmark = pytest.mark.anyio


def agent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for envelope in events:
        parse_envelope(json.dumps(envelope))
    return [envelope["agent"] for envelope in events if envelope["type"] == "blueprint.agent"]


def latest(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The viewer's state: blueprints upserted by id. Asserts a name never belongs to two of them."""
    by_id = {agent["id"]: agent for agent in agent_events(events)}
    names = [agent["name"] for agent in by_id.values() if agent["name"] is not None]
    assert len(names) == len(set(names))
    return by_id


def summary(agent: dict[str, Any]) -> str:
    delegates = [f"{d['tool_name']}->{d['agent_id']}:{d['evidence']}" for d in agent["delegates"]]
    return f"{agent['id']} {agent['name']} {agent['source']} tools={len(agent['tools'])} delegates={delegates}"


# ------------------------------------------------------------- in a session


async def test_unregistered_agent_gets_an_observed_blueprint_with_its_tools() -> None:
    team = build_team()
    with record() as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events

    assert [summary(agent) for agent in agent_events(events)] == snapshot(
        [
            "a1 main observed tools=0 delegates=[]",
            "a1 main observed tools=4 delegates=[]",
            "a2 researcher observed tools=0 delegates=[]",
            "a1 main observed tools=4 delegates=['delegate_research->a2:observed']",
            "a2 researcher observed tools=1 delegates=[]",
        ]
    )
    blueprints = latest(events)
    assert blueprints["a1"] == snapshot(
        {
            "id": "a1",
            "name": "main",
            "label": "main",
            "description": "Main demo agent",
            "source": "observed",
            "location": None,
            "model": {
                "id": None,
                "name": "scripted-fn",
                "provider": "function",
                "offline": True,
                "expression": None,
                "model_class": None,
                "env": None,
            },
            "deps_type": None,
            "instructions": [
                {
                    "kind": "instructions",
                    "dynamic": False,
                    "text": """\
Static instructions.

User is ann.\
""",
                    "function": None,
                    "expression": None,
                    "location": None,
                }
            ],
            "toolsets": [
                {
                    "id": "ts0",
                    "toolset_id": "<agent>",
                    "label": "Agent tools",
                    "kind": "agent",
                    "wrappers": [],
                    "opaque": False,
                    "expression": None,
                    "location": None,
                },
                {
                    "id": "ts1",
                    "toolset_id": "extra_ts",
                    "label": "Toolset 'extra_ts'",
                    "kind": "other",
                    "wrappers": [],
                    "opaque": False,
                    "expression": None,
                    "location": None,
                },
            ],
            "tools": [
                {
                    "name": "get_weather",
                    "toolset": "ts0",
                    "description": "Get the weather.",
                    "parameters_schema": {
                        "additionalProperties": False,
                        "properties": {"city": {"description": "The city name.", "type": "string"}},
                        "required": ["city"],
                        "type": "object",
                    },
                    "return_schema": None,
                    "kind": "function",
                    "takes_ctx": None,
                    "is_async": None,
                    "max_retries": None,
                    "function": None,
                    "origin": "observed",
                    "location": None,
                    "library_version": None,
                },
                {
                    "name": "flaky",
                    "toolset": "ts0",
                    "description": "Fails the first time.",
                    "parameters_schema": {
                        "additionalProperties": False,
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"],
                        "type": "object",
                    },
                    "return_schema": None,
                    "kind": "function",
                    "takes_ctx": None,
                    "is_async": None,
                    "max_retries": None,
                    "function": None,
                    "origin": "observed",
                    "location": None,
                    "library_version": None,
                },
                {
                    "name": "delegate_research",
                    "toolset": "ts0",
                    "description": "Delegate to the researcher agent.",
                    "parameters_schema": {
                        "additionalProperties": False,
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                        "type": "object",
                    },
                    "return_schema": None,
                    "kind": "function",
                    "takes_ctx": None,
                    "is_async": None,
                    "max_retries": None,
                    "function": None,
                    "origin": "observed",
                    "location": None,
                    "library_version": None,
                },
                {
                    "name": "add",
                    "toolset": "ts1",
                    "description": "Add two ints.",
                    "parameters_schema": {
                        "additionalProperties": False,
                        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                        "required": ["a", "b"],
                        "type": "object",
                    },
                    "return_schema": None,
                    "kind": "function",
                    "takes_ctx": None,
                    "is_async": None,
                    "max_retries": None,
                    "function": None,
                    "origin": "observed",
                    "location": None,
                    "library_version": None,
                },
            ],
            "native_tools": [],
            "output": {
                "mode": "tool",
                "allows_text": False,
                "type_name": None,
                "json_schema": None,
                "tool_names": ["final_result"],
                "validators": [],
                "location": None,
            },
            "capabilities": [],
            "settings": {
                "model_settings": None,
                "end_strategy": None,
                "tool_retries": None,
                "output_retries": None,
                "tool_timeout": None,
                "expressions": {},
                "defaults": [],
            },
            "delegates": [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "observed"}],
            "warnings": [],
        }
    )
    assert blueprints["a2"]["tools"] == snapshot(
        [
            {
                "name": "lookup",
                "toolset": "ts0",
                "description": "Look up a topic.",
                "parameters_schema": {
                    "additionalProperties": False,
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                    "type": "object",
                },
                "return_schema": None,
                "kind": "function",
                "takes_ctx": None,
                "is_async": None,
                "max_retries": None,
                "function": None,
                "origin": "observed",
                "location": None,
                "library_version": None,
            }
        ]
    )
    assert blueprints["a2"]["output"] == snapshot(
        {
            "mode": "text",
            "allows_text": True,
            "type_name": None,
            "json_schema": None,
            "tool_names": [],
            "validators": [],
            "location": None,
        }
    )


async def test_observed_blueprint_precedes_the_span_that_revealed_it() -> None:
    team = build_team()
    with record() as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events
    first_span = {}
    first_blueprint = {}
    for envelope in events:
        if envelope["type"] == "span.started" and envelope["span"]["kind"] == "agent_run":
            first_span.setdefault(envelope["span"]["name"], envelope["seq"])
        elif envelope["type"] == "blueprint.agent":
            first_blueprint.setdefault(envelope["agent"]["name"], envelope["seq"])
    assert first_blueprint["main"] == first_span["main"] - 1
    assert first_blueprint["researcher"] < first_span["researcher"]


async def test_registered_unnamed_agent_gets_its_name_on_first_run_with_the_same_id() -> None:
    nameless = Agent(TestModel())
    with record(nameless) as session:
        before = agent_events(session.events)
        await nameless.run("hi")  # Pydantic AI infers the name from this variable
        await nameless.run("again")
        after = agent_events(session.events)
    assert [(a["id"], a["name"], a["label"], a["source"]) for a in before] == [("a1", None, "agent-1", "object")]
    assert [(a["id"], a["name"], a["label"], a["source"]) for a in after] == [
        ("a1", None, "agent-1", "object"),
        ("a1", "nameless", "nameless", "object"),
    ]


async def test_one_registered_unnamed_agent_gets_the_runs_pydantic_ai_could_not_name() -> None:
    """Called through a list or an attribute, an agent's name cannot be inferred and its spans say "agent"."""
    held = [Agent(TestModel())]
    with record(*held) as session:
        await held[0].run("hi")
        await held[0].run("again")
        events = session.events
    assert held[0].name is None
    assert [(a["id"], a["name"], a["label"], a["source"]) for a in agent_events(events)] == [
        ("a1", None, "agent-1", "object"),
        ("a1", "agent", "agent-1", "object"),
    ]
    runs = [e["span"] for e in events if e["type"] == "span.started" and e["span"]["kind"] == "agent_run"]
    assert [run["agent_name"] for run in runs] == ["agent", "agent"]


async def test_several_registered_unnamed_agents_cannot_be_matched_and_say_so() -> None:
    held = [Agent(TestModel()), Agent(TestModel()), Agent(TestModel(), name="named")]
    with record(*held) as session:
        # Not through a loop variable: Pydantic AI would infer the name from it.
        await held[0].run("hi")
        await held[1].run("hi")
        await held[2].run("hi")
        await held[0].run("again")
        blueprints = latest(session.events)
    assert {id: (a["name"], a["source"], a["warnings"]) for id, a in blueprints.items()} == snapshot(
        {
            "a1": (
                None,
                "object",
                [
                    "runs of this agent cannot be matched to it: several registered agents have no name. "
                    "Pass name= to Agent(...)"
                ],
            ),
            "a2": (
                None,
                "object",
                [
                    "runs of this agent cannot be matched to it: several registered agents have no name. "
                    "Pass name= to Agent(...)"
                ],
            ),
            "a3": ("named", "object", []),
            "a4": (
                "agent",
                "observed",
                ["runs of every agent without a name are shown here. Pass name= to Agent(...) to tell them apart"],
            ),
        }
    )


async def test_a_run_named_like_no_registered_agent_is_never_bound_to_an_unnamed_one() -> None:
    held = [Agent(TestModel())]
    with record(*held) as session:
        await Agent(TestModel(), name="stranger").run("hi")
        blueprints = latest(session.events)
    assert {id: (a["name"], a["source"]) for id, a in blueprints.items()} == {
        "a1": (None, "object"),
        "a2": ("stranger", "observed"),
    }


async def test_tools_first_offered_on_a_later_request_still_reach_the_blueprint() -> None:
    """A dynamic toolset can offer different tools on every run."""
    agent = Agent(TestModel(call_tools=[]), name="dynamic")
    calls = 0

    @agent.toolset
    def offered(ctx: RunContext[Any]) -> FunctionToolset[Any]:  # pyright: ignore[reportUnusedFunction]
        nonlocal calls
        calls += 1
        return FunctionToolset([first_tool if calls == 1 else second_tool])

    def first_tool() -> str:
        return "1"

    def second_tool() -> str:
        return "2"

    with record(agent) as session:
        await agent.run("one")
        await agent.run("two")
        await agent.run("three")  # nothing new: no further blueprint event
        events = agent_events(session.events)
    assert [[(tool["name"], tool["origin"]) for tool in a["tools"]] for a in events] == [
        [],
        [("first_tool", "observed")],
        [("first_tool", "observed"), ("second_tool", "observed")],
    ]


async def test_delegation_edge_is_upgraded_to_observed() -> None:
    team = build_team()
    with record(team.main) as session:
        before = latest(session.events)
        await team.main.run(PROMPT, deps=Deps())
        await team.main.run(PROMPT, deps=Deps())  # seen again: nothing new to say
        events = session.events
    assert before["a1"]["delegates"] == [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "object"}]
    after = latest(events)
    assert after["a1"]["delegates"] == [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "observed"}]
    # Static blueprints already list every tool, so the upgrade is the only re-emit.
    assert [summary(agent) for agent in agent_events(events)] == snapshot(
        [
            "a1 main object tools=4 delegates=['delegate_research->a2:object']",
            "a2 researcher object tools=1 delegates=[]",
            "a1 main object tools=4 delegates=['delegate_research->a2:observed']",
        ]
    )
    assert {key: value for key, value in after["a1"].items() if key != "delegates"} == {
        key: value for key, value in before["a1"].items() if key != "delegates"
    }


async def test_static_registration_takes_over_the_observed_blueprints_id() -> None:
    team = build_team()
    with record() as session:
        await team.main.run(PROMPT, deps=Deps())
        observed = latest(session.events)
        session.register(team.main)
        after = latest(session.events)
        await team.main.run(PROMPT, deps=Deps())
        final = latest(session.events)
    assert {id: (a["name"], a["source"]) for id, a in observed.items()} == {
        "a1": ("main", "observed"),
        "a2": ("researcher", "observed"),
    }
    assert {id: (a["name"], a["source"]) for id, a in after.items()} == {
        "a1": ("main", "object"),
        "a2": ("researcher", "object"),
    }
    # What the earlier run proved is kept, and the static detail is all there.
    assert after["a1"]["delegates"] == [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "observed"}]
    assert after["a1"]["deps_type"] == "scenarios.Deps"
    assert [tool["origin"] for tool in after["a1"]["tools"]] == ["object"] * 4
    assert after["a1"]["instructions"][0] == {
        "kind": "instructions",
        "dynamic": False,
        "text": "Static instructions.",
        "function": None,
        "expression": None,
        "location": None,
    }
    assert final == after


async def test_tools_from_an_opaque_toolset_are_added_to_a_static_blueprint() -> None:
    agent = Agent(TestModel(call_tools=[]), name="dyn")

    @agent.tool_plain
    def fixed() -> str:  # pyright: ignore[reportUnusedFunction]
        return "fixed"

    @agent.toolset(id="late")
    def late_tools(ctx: RunContext[Any]) -> FunctionToolset[Any]:  # pyright: ignore[reportUnusedFunction]
        toolset: FunctionToolset[Any] = FunctionToolset(id="late-inner")

        @toolset.tool_plain
        def discovered(query: str) -> str:  # pyright: ignore[reportUnusedFunction]
            """Only exists at run time."""
            return query

        return toolset

    with record(agent) as session:
        static = latest(session.events)["a1"]
        await agent.run("hi")
        merged = latest(session.events)["a1"]
    assert [(tool["name"], tool["origin"]) for tool in static["tools"]] == [("fixed", "object")]
    assert [(t["id"], t["toolset_id"], t["kind"], t["opaque"]) for t in static["toolsets"]] == snapshot(
        [("ts0", "<agent>", "agent", False), ("ts1", "late", "dynamic", True)]
    )
    assert [(tool["name"], tool["toolset"], tool["origin"]) for tool in merged["tools"]] == snapshot(
        [("fixed", "ts0", "object"), ("discovered", "ts2", "observed")]
    )
    assert [(t["id"], t["toolset_id"], t["label"], t["kind"]) for t in merged["toolsets"]] == snapshot(
        [
            ("ts0", "<agent>", "Agent tools", "agent"),
            ("ts1", "late", "DynamicToolset 'late'", "dynamic"),
            ("ts2", "late-inner", "Toolset 'late-inner'", "other"),
        ]
    )
    assert merged["tools"][1] == snapshot(
        {
            "name": "discovered",
            "toolset": "ts2",
            "description": "Only exists at run time.",
            "parameters_schema": {
                "additionalProperties": False,
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "type": "object",
            },
            "return_schema": None,
            "kind": "function",
            "takes_ctx": None,
            "is_async": None,
            "max_retries": None,
            "function": None,
            "origin": "observed",
            "location": None,
            "library_version": None,
        }
    )


async def test_span_end_without_a_start_still_derives_a_blueprint() -> None:
    """A processor attached in the middle of a run only sees spans end."""
    team = build_team()
    with record(instrument=False) as session:

        class EndOnly(SpanProcessor):
            def on_end(self, span: ReadableSpan) -> None:
                session.span_processor.on_end(span)

        provider = TracerProvider()
        provider.add_span_processor(EndOnly())
        team.researcher.instrument = InstrumentationSettings(tracer_provider=provider)
        await team.researcher.run("research Paris")
        events = session.events
    assert [envelope["type"] for envelope in events] == snapshot(
        [
            "session.started",
            "blueprint.agent",
            "blueprint.agent",
            "span.ended",
            "span.ended",
            "span.ended",
            "span.ended",
        ]
    )
    (researcher,) = latest(events).values()
    assert (researcher["name"], researcher["source"]) == ("researcher", "observed")
    assert [tool["name"] for tool in researcher["tools"]] == ["lookup"]


async def test_content_capture_off_leaves_observed_instructions_empty() -> None:
    team = build_team()
    with record(include_content=False) as session:
        await team.researcher.run("research Paris")
        (researcher,) = latest(session.events).values()
    assert researcher["instructions"] == []
    assert [tool["name"] for tool in researcher["tools"]] == ["lookup"]  # structure is not content


# ------------------------------------------------------------ pure functions

_PARAMETERS: dict[str, Any] = {
    "function_tools": [
        {
            "name": "search",
            "description": "Search.",
            "parameters_json_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "kind": "function",
            "return_schema": None,
            "toolset_id": "<agent>",
        },
        {"name": "approve", "parameters_json_schema": {"type": "object"}, "kind": "unapproved", "toolset_id": "kb"},
        {"name": "odd", "parameters_json_schema": {"type": "object"}, "kind": "brand-new-kind", "toolset_id": None},
        {"name": "also_odd", "parameters_json_schema": {"type": "object"}},
        {"no": "name"},
        "garbage",
    ],
    "output_mode": "tool",
    "allow_text_output": False,
    "output_tools": [{"name": "final_result"}, {"name": "final_result_B"}],
}


def _observed() -> AgentBlueprint:
    return observed_blueprint("a7", "helper", description="Helps", model_name="gpt-x")


def test_observed_blueprint_shape() -> None:
    assert _observed().model_dump(mode="json") == snapshot(
        {
            "id": "a7",
            "name": "helper",
            "label": "helper",
            "description": "Helps",
            "source": "observed",
            "location": None,
            "model": {
                "id": None,
                "name": "gpt-x",
                "provider": None,
                "offline": False,
                "expression": None,
                "model_class": None,
                "env": None,
            },
            "deps_type": None,
            "instructions": [],
            "toolsets": [],
            "tools": [],
            "native_tools": [],
            "output": {
                "mode": "unknown",
                "allows_text": None,
                "type_name": None,
                "json_schema": None,
                "tool_names": [],
                "validators": [],
                "location": None,
            },
            "capabilities": [],
            "settings": {
                "model_settings": None,
                "end_strategy": None,
                "tool_retries": None,
                "output_retries": None,
                "tool_timeout": None,
                "expressions": {},
                "defaults": [],
            },
            "delegates": [],
            "warnings": [],
        }
    )
    assert observed_blueprint("a8", "", description=None, model_name=None).label == "a8"  # a label is never empty


def test_merge_request_into_an_observed_blueprint() -> None:
    merged = merge_request(_observed(), _PARAMETERS, instructions="Be brief.", provider="function")
    dumped = merged.model_dump(mode="json")
    assert dumped["toolsets"] == snapshot(
        [
            {
                "id": "ts0",
                "toolset_id": "<agent>",
                "label": "Agent tools",
                "kind": "agent",
                "wrappers": [],
                "opaque": False,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts1",
                "toolset_id": "kb",
                "label": "Toolset 'kb'",
                "kind": "other",
                "wrappers": [],
                "opaque": False,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts2",
                "toolset_id": None,
                "label": "Observed tools",
                "kind": "other",
                "wrappers": [],
                "opaque": False,
                "expression": None,
                "location": None,
            },
        ]
    )
    assert [(t["name"], t["toolset"], t["kind"], t["origin"]) for t in dumped["tools"]] == snapshot(
        [
            ("search", "ts0", "function", "observed"),
            ("approve", "ts1", "unapproved", "observed"),
            ("odd", "ts2", "function", "observed"),
            ("also_odd", "ts2", "function", "observed"),
        ]
    )
    assert dumped["tools"][0] == snapshot(
        {
            "name": "search",
            "toolset": "ts0",
            "description": "Search.",
            "parameters_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "return_schema": None,
            "kind": "function",
            "takes_ctx": None,
            "is_async": None,
            "max_retries": None,
            "function": None,
            "origin": "observed",
            "location": None,
            "library_version": None,
        }
    )
    assert dumped["output"] == snapshot(
        {
            "mode": "tool",
            "allows_text": False,
            "type_name": None,
            "json_schema": None,
            "tool_names": ["final_result", "final_result_B"],
            "validators": [],
            "location": None,
        }
    )
    assert dumped["instructions"] == [
        {
            "kind": "instructions",
            "dynamic": False,
            "text": "Be brief.",
            "function": None,
            "expression": None,
            "location": None,
        }
    ]
    assert dumped["model"] == {
        "id": None,
        "name": "gpt-x",
        "provider": "function",
        "offline": True,
        "expression": None,
        "model_class": None,
        "env": None,
    }
    assert AgentBlueprint.model_validate(dumped) == merged


def test_merge_request_is_idempotent_and_reports_no_change_by_identity() -> None:
    once = merge_request(_observed(), _PARAMETERS, instructions="Be brief.", provider="openai")
    assert merge_request(once, _PARAMETERS, instructions="Other text.", provider="other") is once
    untouched = _observed()
    assert merge_request(untouched, {}, instructions=None, provider=None) is untouched
    assert merge_request(
        untouched, {"function_tools": "garbage", "output_tools": 3}, instructions=None, provider=None
    ) is (untouched)


def test_merge_request_never_overrides_what_static_introspection_found() -> None:
    agent = Agent(TestModel(), name="static", instructions="Mine.")

    @agent.tool_plain
    def search(q: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """The real one."""
        return q

    static = blueprint(agent)
    assert isinstance(static, AgentBlueprint)
    merged = merge_request(static, _PARAMETERS, instructions="Resolved text.", provider="function")
    by_name = {tool.name: tool for tool in merged.tools}
    assert (by_name["search"].origin, by_name["search"].description) == ("object", "The real one.")
    assert [tool.name for tool in merged.tools] == ["search", "approve", "odd", "also_odd"]
    assert by_name["approve"].toolset != by_name["search"].toolset
    assert merged.instructions == static.instructions  # static blueprints keep their own instructions
    assert merged.model == static.model
    assert (merged.output.mode, merged.output.allows_text) == ("text", True)
    # An empty list is also what a failed private read leaves behind, so names seen in a run fill it.
    assert merged.output.tool_names == ["final_result", "final_result_B"]


def test_add_delegate_adds_upgrades_and_then_reports_no_change() -> None:
    base = _observed()
    added = add_delegate(base, "ask", "a2")
    assert added.delegates == [Delegate(tool_name="ask", agent_id="a2", evidence="observed")]
    assert add_delegate(added, "ask", "a2") is added
    assert base.delegates == []  # inputs are never mutated

    static = base.model_copy(update={"delegates": [Delegate(tool_name="ask", agent_id="a2", evidence="object")]})
    upgraded = add_delegate(static, "ask", "a2")
    assert upgraded.delegates == [Delegate(tool_name="ask", agent_id="a2", evidence="observed")]
    other = add_delegate(static, "ask", "a3")  # same tool, another agent: a second edge
    assert [(d.agent_id, d.evidence) for d in other.delegates] == [("a2", "object"), ("a3", "observed")]


def test_carry_observed_keeps_run_evidence_under_a_static_blueprint() -> None:
    previous = merge_request(_observed(), _PARAMETERS, instructions="Be brief.", provider="function")
    previous = add_delegate(previous, "search", "a2")

    agent = Agent(TestModel(), name="helper")

    @agent.tool_plain
    def search(q: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return q

    fresh = blueprint(agent)
    assert isinstance(fresh, AgentBlueprint)
    fresh = fresh.model_copy(update={"id": "a7", "output": fresh.output.model_copy(update={"mode": "unknown"})})
    carried = carry_observed(previous, fresh)
    assert carried.source == "object"
    assert carried.delegates == [Delegate(tool_name="search", agent_id="a2", evidence="observed")]
    assert [(tool.name, tool.origin) for tool in carried.tools] == [
        ("search", "object"),
        ("approve", "observed"),
        ("odd", "observed"),
        ("also_odd", "observed"),
    ]
    toolsets = {toolset.id: toolset.toolset_id for toolset in carried.toolsets}
    assert [toolsets[tool.toolset] for tool in carried.tools] == ["<agent>", "kb", None, None]
    assert carried.output.mode == "tool"  # static introspection could not tell; the run could
    assert carried.instructions == fresh.instructions
    assert AgentBlueprint.model_validate(carried.model_dump(mode="json")) == carried
    assert carry_observed(_observed(), fresh) is fresh  # nothing was observed: nothing to carry
