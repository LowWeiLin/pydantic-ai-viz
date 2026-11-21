from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pydantic_ai.capabilities
import pytest
from inline_snapshot import snapshot
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, RunContext, TextOutput, ToolOutput
from pydantic_ai.capabilities import (
    MCP,
    AbstractCapability,
    Capability,
    Hooks,
    PrefixTools,
    Thinking,
    Toolset,
    WebFetch,
    WebSearch,
)
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, ExternalToolset, FunctionToolset, ToolsetTool

import pydantic_ai_viz
from pydantic_ai_viz import AgentBlueprint, blueprint
from pydantic_ai_viz._capabilities import _GROUPS
from pydantic_ai_viz._introspect import agent_closure, find_delegates, introspect_agent
from pydantic_ai_viz._models import Delegate, ModelInfo

MODULE = __name__


def _agent_blueprint(agent: Agent[Any, Any]) -> AgentBlueprint:
    result = blueprint(agent)
    assert isinstance(result, AgentBlueprint)
    # The dump must satisfy the closed wire model (every field present, nothing extra).
    assert AgentBlueprint.model_validate(result.model_dump(mode="json")) == result
    return result


# ------------------------------------------------------------ the full agent


@dataclass
class Deps:
    user: str = "ann"


class Answer(BaseModel):
    """Final structured answer."""

    city: str
    temp_c: float


def _scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("unused")])


researcher = Agent(
    TestModel(custom_output_text="research notes"),
    name="researcher",
    description="Looks things up",
    instructions="You research.",
)


@researcher.tool_plain
def lookup(topic: str) -> str:
    """Look up a topic."""
    return f"facts about {topic}"


extra = FunctionToolset[Deps](id="extra_ts")


@extra.tool_plain
def add(a: int, b: int) -> int:
    """Add two ints."""
    return a + b


main_agent = Agent(
    FunctionModel(_scripted, model_name="scripted-fn"),
    name="main",
    description="Main demo agent",
    deps_type=Deps,
    output_type=Answer,
    instructions="Static instructions.",
    system_prompt="Static system prompt.",
    toolsets=[extra],
    retries=3,
    tool_timeout=30,
    model_settings={"temperature": 0.2},
)


@main_agent.instructions
def dyn_instructions(ctx: RunContext[Deps]) -> str:
    return f"User is {ctx.deps.user}."


@main_agent.system_prompt
def dyn_sysprompt(ctx: RunContext[Deps]) -> str:
    return f"sys for {ctx.deps.user}"


@main_agent.tool
def get_weather(ctx: RunContext[Deps], city: str) -> dict[str, Any]:
    """Get the weather.

    Args:
        city: The city name.
    """
    return {"city": city, "temp_c": 21.5}


@main_agent.tool_plain(retries=2)
def flaky(x: int) -> str:
    """Fails the first time."""
    return "ok"


@main_agent.tool
async def delegate_research(ctx: RunContext[Deps], topic: str) -> str:
    """Delegate to the researcher agent."""
    result = await researcher.run(f"research {topic}", usage=ctx.usage)
    return result.output


def test_full_agent_blueprint() -> None:
    assert _agent_blueprint(main_agent).model_dump(mode="json") == snapshot(
        {
            "id": "a1",
            "name": "main",
            "label": "main",
            "description": "Main demo agent",
            "source": "object",
            "location": None,
            "model": {
                "id": "function:scripted-fn",
                "name": "scripted-fn",
                "provider": "function",
                "offline": True,
                "expression": None,
                "model_class": "FunctionModel",
                "env": None,
            },
            "deps_type": "test_introspect_agent.Deps",
            "instructions": [
                {
                    "kind": "instructions",
                    "dynamic": False,
                    "text": "Static instructions.",
                    "function": None,
                    "expression": None,
                    "location": None,
                },
                {
                    "kind": "instructions",
                    "dynamic": True,
                    "text": None,
                    "function": "test_introspect_agent.dyn_instructions",
                    "expression": None,
                    "location": None,
                },
                {
                    "kind": "system_prompt",
                    "dynamic": False,
                    "text": "Static system prompt.",
                    "function": None,
                    "expression": None,
                    "location": None,
                },
                {
                    "kind": "system_prompt",
                    "dynamic": True,
                    "text": None,
                    "function": "test_introspect_agent.dyn_sysprompt",
                    "expression": None,
                    "location": None,
                },
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
                    "label": "FunctionToolset 'extra_ts'",
                    "kind": "function",
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
                    "return_schema": {"additionalProperties": True, "type": "object"},
                    "kind": "function",
                    "takes_ctx": True,
                    "is_async": False,
                    "max_retries": None,
                    "function": "test_introspect_agent.get_weather",
                    "origin": "object",
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
                    "return_schema": {"type": "string"},
                    "kind": "function",
                    "takes_ctx": False,
                    "is_async": False,
                    "max_retries": 2,
                    "function": "test_introspect_agent.flaky",
                    "origin": "object",
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
                    "return_schema": {"type": "string"},
                    "kind": "function",
                    "takes_ctx": True,
                    "is_async": True,
                    "max_retries": None,
                    "function": "test_introspect_agent.delegate_research",
                    "origin": "object",
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
                    "return_schema": {"type": "integer"},
                    "kind": "function",
                    "takes_ctx": False,
                    "is_async": False,
                    "max_retries": None,
                    "function": "test_introspect_agent.add",
                    "origin": "object",
                    "location": None,
                    "library_version": None,
                },
            ],
            "native_tools": ["tool_search"],
            "output": {
                "mode": "auto",
                "allows_text": True,
                "type_name": "Answer",
                "json_schema": {
                    "description": "Final structured answer.",
                    "properties": {
                        "city": {"title": "City", "type": "string"},
                        "temp_c": {"title": "Temp C", "type": "number"},
                    },
                    "required": ["city", "temp_c"],
                    "title": "Answer",
                    "type": "object",
                },
                "tool_names": ["final_result"],
                "location": None,
            },
            "capabilities": [
                {
                    "name": "ToolSearch",
                    "builtin": True,
                    "category": "context",
                    "package": "pydantic_ai",
                    "deferred": False,
                    "details": {},
                    "expressions": {},
                    "toolsets": [],
                    "native_tools": ["tool_search"],
                    "capability_id": "tool_search",
                    "stack": None,
                    "skills": [],
                },
                {
                    "name": "PendingMessageDrainCapability",
                    "builtin": True,
                    "category": "runtime",
                    "package": "pydantic_ai",
                    "deferred": False,
                    "details": {},
                    "expressions": {},
                    "toolsets": [],
                    "native_tools": [],
                    "capability_id": None,
                    "stack": None,
                    "skills": [],
                },
            ],
            "settings": {
                "model_settings": {"temperature": 0.2},
                "end_strategy": "graceful",
                "tool_retries": 3,
                "output_retries": 3,
                "tool_timeout": 30.0,
                "expressions": {},
                "defaults": ["end_strategy"],
            },
            "delegates": [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "object"}],
            "warnings": [],
        }
    )


# -------------------------------------------------------------------- models


def test_model_instance() -> None:
    assert _agent_blueprint(Agent(TestModel())).model == snapshot(
        ModelInfo(
            id="test:test",
            name="test",
            provider="test",
            offline=True,
            expression=None,
            model_class="TestModel",
            env=None,
        )
    )


def test_model_string_with_deferred_check_is_not_resolved() -> None:
    agent = Agent("openai:gpt-5.2", defer_model_check=True)
    assert _agent_blueprint(agent).model == snapshot(
        ModelInfo(
            id="openai:gpt-5.2",
            name="gpt-5.2",
            provider="openai",
            offline=False,
            expression=None,
            model_class=None,
            env=None,
        )
    )
    assert agent.model == "openai:gpt-5.2"


def test_model_string_without_provider_prefix() -> None:
    agent = Agent("test", defer_model_check=True)
    assert _agent_blueprint(agent).model == snapshot(
        ModelInfo(id="test", name="test", provider=None, offline=False, expression=None, model_class=None, env=None)
    )


def test_model_none() -> None:
    assert _agent_blueprint(Agent()).model is None


def test_offline_flag_follows_the_provider() -> None:
    assert _agent_blueprint(Agent("function:x", defer_model_check=True)).model == snapshot(
        ModelInfo(
            id="function:x", name="x", provider="function", offline=True, expression=None, model_class=None, env=None
        )
    )


# -------------------------------------------------------------------- output


class Left(BaseModel):
    x: int


class Right(BaseModel):
    y: str


def shout(text: str) -> str:
    return text.upper()


def test_text_output() -> None:
    assert _agent_blueprint(Agent()).output.model_dump() == snapshot(
        {
            "mode": "text",
            "allows_text": True,
            "type_name": "str",
            "json_schema": {"type": "string"},
            "tool_names": [],
            "location": None,
        }
    )


def test_structured_output() -> None:
    assert _agent_blueprint(Agent(output_type=Left)).output.model_dump() == snapshot(
        {
            "mode": "auto",
            "allows_text": True,
            "type_name": "Left",
            "json_schema": {
                "properties": {"x": {"title": "X", "type": "integer"}},
                "required": ["x"],
                "title": "Left",
                "type": "object",
            },
            "tool_names": ["final_result"],
            "location": None,
        }
    )


def test_multi_output() -> None:
    assert _agent_blueprint(Agent(output_type=[Left, Right])).output.model_dump() == snapshot(
        {
            "mode": "auto",
            "allows_text": True,
            "type_name": "Left | Right",
            "json_schema": {
                "anyOf": [
                    {
                        "properties": {"x": {"title": "X", "type": "integer"}},
                        "required": ["x"],
                        "title": "Left",
                        "type": "object",
                    },
                    {
                        "properties": {"y": {"title": "Y", "type": "string"}},
                        "required": ["y"],
                        "title": "Right",
                        "type": "object",
                    },
                ]
            },
            "tool_names": ["final_result_Left", "final_result_Right"],
            "location": None,
        }
    )


@pytest.mark.parametrize(
    ("output_type", "expected"),
    [
        (
            Left | Right,
            snapshot(
                {
                    "mode": "auto",
                    "allows_text": True,
                    "type_name": "Left | Right",
                    "tool_names": ["final_result_Left", "final_result_Right"],
                    "location": None,
                }
            ),
        ),
        (
            ToolOutput(Left, name="give_left"),
            snapshot(
                {
                    "mode": "tool",
                    "allows_text": False,
                    "type_name": "Left",
                    "tool_names": ["give_left"],
                    "location": None,
                }
            ),
        ),
        (
            NativeOutput(Left),
            snapshot({"mode": "native", "allows_text": True, "type_name": "Left", "tool_names": [], "location": None}),
        ),
        (
            PromptedOutput([Left, Right]),
            snapshot(
                {
                    "mode": "prompted",
                    "allows_text": True,
                    "type_name": "Left | Right",
                    "tool_names": [],
                    "location": None,
                }
            ),
        ),
        (
            TextOutput(shout),
            snapshot({"mode": "text", "allows_text": True, "type_name": "shout", "tool_names": [], "location": None}),
        ),
        (
            [ToolOutput(Left, name="give_left"), Right, shout],
            snapshot(
                {
                    "mode": "tool",
                    "allows_text": False,
                    "type_name": "Left | Right | shout",
                    "tool_names": ["give_left", "final_result_Right", "final_result_shout"],
                    "location": None,
                }
            ),
        ),
        (
            list[Left],
            snapshot(
                {
                    "mode": "auto",
                    "allows_text": True,
                    "type_name": "list[Left]",
                    "tool_names": ["final_result"],
                    "location": None,
                }
            ),
        ),
    ],
    ids=["union", "tool", "native", "prompted", "text-function", "mixed", "generic"],
)
def test_output_modes(output_type: Any, expected: dict[str, Any]) -> None:
    output = _agent_blueprint(Agent(output_type=output_type)).output
    assert output.json_schema is not None
    assert output.model_dump(exclude={"json_schema"}) == expected


# ------------------------------------------------- instructions and settings


def test_instruction_order() -> None:
    calls: list[str] = []
    agent = Agent(instructions=["first", "second"], system_prompt=("sp one", "sp two"))

    @agent.instructions
    def late() -> str:
        calls.append("instructions")
        return "late"

    @agent.system_prompt(dynamic=True)
    async def fresh() -> str:
        calls.append("system_prompt")
        return "fresh"

    assert [i.model_dump() for i in _agent_blueprint(agent).instructions] == snapshot(
        [
            {
                "kind": "instructions",
                "dynamic": False,
                "text": "first",
                "function": None,
                "expression": None,
                "location": None,
            },
            {
                "kind": "instructions",
                "dynamic": False,
                "text": "second",
                "function": None,
                "expression": None,
                "location": None,
            },
            {
                "kind": "instructions",
                "dynamic": True,
                "text": None,
                "function": "test_introspect_agent.test_instruction_order.<locals>.late",
                "expression": None,
                "location": None,
            },
            {
                "kind": "system_prompt",
                "dynamic": False,
                "text": "sp one",
                "function": None,
                "expression": None,
                "location": None,
            },
            {
                "kind": "system_prompt",
                "dynamic": False,
                "text": "sp two",
                "function": None,
                "expression": None,
                "location": None,
            },
            {
                "kind": "system_prompt",
                "dynamic": True,
                "text": None,
                "function": "test_introspect_agent.test_instruction_order.<locals>.fresh",
                "expression": None,
                "location": None,
            },
        ]
    )
    assert calls == []


def test_agent_without_prompts_or_settings() -> None:
    result = _agent_blueprint(Agent())
    assert result.instructions == []
    assert result.settings.model_dump() == snapshot(
        {
            "model_settings": None,
            "end_strategy": "graceful",
            "tool_retries": 1,
            "output_retries": 1,
            "tool_timeout": None,
            "expressions": {},
            "defaults": ["end_strategy", "tool_retries", "output_retries"],
        }
    )
    assert result.deps_type is None
    assert result.description is None


def test_deps_type_is_qualified() -> None:
    assert _agent_blueprint(Agent(deps_type=Deps)).deps_type == f"{MODULE}.Deps"
    assert _agent_blueprint(Agent(deps_type=int)).deps_type == "int"
    assert _agent_blueprint(Agent(deps_type=type(None))).deps_type is None


# ------------------------------------------------------------------ toolsets

kb = FunctionToolset[Any](id="kb")


@kb.tool_plain
def search(query: str) -> str:
    """Search the knowledge base."""
    return query


maths = FunctionToolset[Any]()


@maths.tool_plain
async def plus(a: int, b: int) -> int:
    return a + b


frontend = ExternalToolset[Any](
    [ToolDefinition(name="click", description="Click an element.", parameters_json_schema={"type": "object"})],
    id="frontend",
)


class CustomToolset(AbstractToolset[Any]):
    """A toolset whose tools exist only at run time."""

    @property
    def id(self) -> str | None:
        return None

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        raise AssertionError("introspection must not enumerate tools")

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        raise AssertionError("introspection must not call tools")


class FakeMCPToolset(CustomToolset):
    """Stands in for ``pydantic_ai.mcp.MCPToolset``, which needs the ``mcp`` extra."""


def per_run_toolset(ctx: RunContext[Any]) -> FunctionToolset[Any]:
    raise AssertionError("introspection must not resolve dynamic toolsets")


def _toolset_agent() -> Agent[None, str]:
    agent = Agent(
        toolsets=[
            CombinedToolset([kb.prefixed("kb").filtered(lambda ctx, tool_def: True), maths.renamed({"sum": "plus"})]),
            frontend.approval_required(),
            CustomToolset(),
            FakeMCPToolset(),
            per_run_toolset,
        ]
    )

    @agent.tool_plain
    def ping() -> str:
        return "pong"

    return agent


def test_nested_wrapped_and_combined_toolsets() -> None:
    result = _agent_blueprint(_toolset_agent())
    assert [t.model_dump() for t in result.toolsets] == snapshot(
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
                "label": "FunctionToolset 'kb'",
                "kind": "function",
                "wrappers": ["FilteredToolset", "PrefixedToolset"],
                "opaque": False,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts2",
                "toolset_id": None,
                "label": "FunctionToolset",
                "kind": "function",
                "wrappers": ["RenamedToolset"],
                "opaque": False,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts3",
                "toolset_id": "frontend",
                "label": "ExternalToolset 'frontend'",
                "kind": "other",
                "wrappers": ["ApprovalRequiredToolset"],
                "opaque": False,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts4",
                "toolset_id": None,
                "label": "CustomToolset",
                "kind": "other",
                "wrappers": [],
                "opaque": True,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts5",
                "toolset_id": None,
                "label": "FakeMCPToolset",
                "kind": "mcp",
                "wrappers": [],
                "opaque": True,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts6",
                "toolset_id": None,
                "label": "DynamicToolset",
                "kind": "dynamic",
                "wrappers": [],
                "opaque": True,
                "expression": None,
                "location": None,
            },
        ]
    )
    assert [(t.name, t.toolset, t.kind, t.function) for t in result.tools] == snapshot(
        [
            ("ping", "ts0", "function", "test_introspect_agent._toolset_agent.<locals>.ping"),
            ("kb_search", "ts1", "function", "test_introspect_agent.search"),
            ("sum", "ts2", "function", "test_introspect_agent.plus"),
            ("click", "ts3", "external", None),
        ]
    )
    assert result.warnings == []


def test_external_tools_have_no_python_function() -> None:
    click = next(t for t in _agent_blueprint(_toolset_agent()).tools if t.name == "click")
    assert click.model_dump() == snapshot(
        {
            "name": "click",
            "toolset": "ts3",
            "description": "Click an element.",
            "parameters_schema": {"type": "object"},
            "return_schema": None,
            "kind": "external",
            "takes_ctx": None,
            "is_async": None,
            "max_retries": None,
            "function": None,
            "origin": "object",
            "location": None,
            "library_version": None,
        }
    )


def test_empty_agent_toolset_is_left_out() -> None:
    result = _agent_blueprint(Agent(toolsets=[kb]))
    assert [(t.id, t.kind) for t in result.toolsets] == [("ts0", "function")]


def test_broken_toolset_becomes_opaque_with_a_warning() -> None:
    class Exploding(FunctionToolset[Any]):
        @property
        def tools(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
            raise RuntimeError("boom")

        @tools.setter
        def tools(self, value: dict[str, Any]) -> None:  # pyright: ignore[reportIncompatibleVariableOverride]
            pass

    result = _agent_blueprint(Agent(toolsets=[Exploding(id="bad"), kb]))
    assert [t.model_dump() for t in result.toolsets] == snapshot(
        [
            {
                "id": "ts0",
                "toolset_id": "bad",
                "label": "Exploding 'bad'",
                "kind": "other",
                "wrappers": [],
                "opaque": True,
                "expression": None,
                "location": None,
            },
            {
                "id": "ts1",
                "toolset_id": "kb",
                "label": "FunctionToolset 'kb'",
                "kind": "function",
                "wrappers": [],
                "opaque": False,
                "expression": None,
                "location": None,
            },
        ]
    )
    assert result.warnings == snapshot(["toolset Exploding could not be inspected (RuntimeError: boom)"])
    assert [t.name for t in result.tools] == ["search"]


def test_tools_from_methods_and_builtins() -> None:
    class Service:
        async def fetch(self, key: str) -> str:
            return key

    result = _agent_blueprint(Agent(tools=[Service().fetch, len]))
    assert [(t.name, t.is_async, t.function) for t in result.tools] == snapshot(
        [
            ("fetch", True, "test_introspect_agent.test_tools_from_methods_and_builtins.<locals>.Service.fetch"),
            ("len", False, "len"),
        ]
    )


# -------------------------------------------------------------- capabilities


def test_builtin_capabilities_are_flagged() -> None:
    result = _agent_blueprint(Agent(capabilities=[Hooks()]))
    assert [c.model_dump() for c in result.capabilities] == snapshot(
        [
            {
                "name": "ToolSearch",
                "builtin": True,
                "category": "context",
                "package": "pydantic_ai",
                "deferred": False,
                "details": {},
                "expressions": {},
                "toolsets": [],
                "native_tools": ["tool_search"],
                "capability_id": "tool_search",
                "stack": None,
                "skills": [],
            },
            {
                "name": "PendingMessageDrainCapability",
                "builtin": True,
                "category": "runtime",
                "package": "pydantic_ai",
                "deferred": False,
                "details": {},
                "expressions": {},
                "toolsets": [],
                "native_tools": [],
                "capability_id": None,
                "stack": None,
                "skills": [],
            },
            {
                "name": "Hooks",
                "builtin": False,
                "category": "loop",
                "package": "pydantic_ai",
                "deferred": False,
                "details": {},
                "expressions": {},
                "toolsets": [],
                "native_tools": [],
                "capability_id": None,
                "stack": None,
                "skills": [],
            },
        ]
    )
    assert result.native_tools == snapshot(["tool_search"])


def _double(x: int) -> int:
    return x * 2


def test_capabilities_say_what_they_are_given_and_what_they_give_the_agent() -> None:
    result = _agent_blueprint(
        Agent(
            TestModel(),
            capabilities=[
                Thinking(effort="high"),
                WebSearch(),
                MCP(url="https://h.example/mcp?k=1", native=True, local=False, authorization_token="s"),
                PrefixTools(WebFetch(), "w"),
                Capability(tools=[_double], id="mine"),
                Toolset(FunctionToolset([_double], id="kb")),
                Thinking(effort="low", id="t2", defer_loading=True),
                Hooks(),
            ],
        )
    )
    shown = {(c.name, c.deferred): c for c in result.capabilities[2:]}
    assert {c.package for c in result.capabilities} == {"pydantic_ai"}
    thinking = shown["Thinking", False]
    assert (thinking.category, thinking.details) == ("reasoning", {"effort": "high"})
    search = shown["WebSearch", False]
    assert (search.category, search.details, search.native_tools) == ("web", {}, ["web_search"])
    mcp = shown["MCP", False]
    assert (mcp.category, mcp.details["url"], mcp.native_tools) == ("tools", "https://h.example/mcp", ["mcp_server"])
    assert "authorization_token" not in mcp.details
    prefixed = shown["PrefixTools", False]
    assert (prefixed.category, prefixed.details, prefixed.native_tools) == (
        "loop",
        {"wrapped": "WebFetch", "prefix": "w"},
        ["web_fetch"],
    )
    labels = {t.id: t.label for t in result.toolsets}
    capability = shown["Capability", False]
    assert (capability.category, [labels[i] for i in capability.toolsets]) == ("custom", ["FunctionToolset 'mine'"])
    assert capability.details == {"tools": [f"{MODULE}._double"]}
    toolset = shown["Toolset", False]
    assert (toolset.category, [labels[i] for i in toolset.toolsets]) == ("tools", ["FunctionToolset 'kb'"])
    assert shown["Thinking", True].details == {"effort": "low"}
    hooks = shown["Hooks", False]
    assert (hooks.category, hooks.details) == ("loop", {})
    assert all(c.expressions == {} for c in result.capabilities)
    assert result.native_tools == ["tool_search", "web_search", "mcp_server", "web_fetch"]
    assert result.warnings == []


def test_the_native_tool_of_a_capability_loaded_on_demand_is_named_by_its_kind() -> None:
    result = _agent_blueprint(Agent(TestModel(), capabilities=[WebSearch(local=_double, defer_loading=True, id="ws")]))
    assert result.native_tools == ["tool_search", "web_search"]
    assert result.warnings == []


def test_a_harness_stack_is_one_entry_beside_the_capabilities_it_holds() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    for stack, members in [
        ("Coder", ["FileSystem", "Shell", "RepoContext", "SubAgents", "ClearToolResults", "RepairToolArguments"]),
        ("Researcher", ["WebSearch", "WebFetch", "SubAgents", "ToolOutputLimits"]),
    ]:
        result = _agent_blueprint(Agent(TestModel(), capabilities=[getattr(harness, stack)()]))
        shown = [(c.name, c.category, c.package) for c in result.capabilities[2:]]
        assert shown[0] == (stack, "harness", "pydantic_ai_harness")
        # The stack's helpers are left out: private classes, and Researcher's instructions as a plain Capability.
        assert [name for name, _, _ in shown[1:]] == members


def test_a_capability_with_the_id_of_a_stack_helper_is_listed_without_that_stack() -> None:
    result = _agent_blueprint(Agent(TestModel(), capabilities=[Capability(id="researcher_instructions")]))
    assert [c.name for c in result.capabilities[2:]] == ["Capability"]


def test_a_capability_a_stack_puts_there_names_that_stack() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    for given, expected in [
        (
            [harness.Researcher(), Thinking()],
            [("Researcher", None), *[(n, "Researcher") for n in _RESEARCHER], ("Thinking", None)],
        ),
        ([harness.Coder(), harness.Memory()], [("Coder", None), *[(n, "Coder") for n in _CODER], ("Memory", None)]),
    ]:
        result = _agent_blueprint(Agent(TestModel(), capabilities=given))
        assert [(c.name, c.stack) for c in result.capabilities[2:]] == expected
    assert [c.stack for c in _agent_blueprint(Agent(TestModel(), capabilities=[WebSearch()])).capabilities] == [
        None,
        None,
        None,
    ]


def test_a_capability_the_author_gives_after_a_stack_is_not_one_the_stack_put_there() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    helper = [harness.SubAgent(Agent(TestModel(), name="helper"), name="h", description="d")]
    for given, members in [
        ([harness.Coder(), harness.Shell(default_timeout=5)], _CODER),  # a class the stack put there already
        ([harness.Coder(), harness.FileSystem(id="mine")], _CODER),
        ([harness.Researcher(), harness.SubAgents(agents=helper, id="mine")], _RESEARCHER),
        # Merged with the stack's own of the same id, it moves to the author's place: out of the stack's order.
        ([harness.Researcher(), harness.SubAgents(agents=helper)], ["WebSearch", "WebFetch", "ToolOutputLimits"]),
        ([harness.Researcher(), WebSearch(local=_double)], ["WebFetch", "SubAgents", "ToolOutputLimits"]),
    ]:
        result = _agent_blueprint(Agent(TestModel(), capabilities=given))
        assert [c.name for c in result.capabilities if c.stack is not None] == members
        assert result.capabilities[-1].stack is None  # the author's


def test_a_stack_entry_gives_the_toolsets_of_its_helpers() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    result = _agent_blueprint(Agent(TestModel(), capabilities=[harness.Researcher()]))
    labels = {t.id: t.label for t in result.toolsets}
    entry = next(c for c in result.capabilities if c.name == "Researcher")
    assert [labels[i] for i in entry.toolsets] == ["FunctionToolset 'researcher_instructions'"]
    given = {t for c in result.capabilities for t in c.toolsets}
    assert {t.id for t in result.toolsets} <= given  # every toolset has a capability that gives it


_CODER = ["FileSystem", "Shell", "RepoContext", "SubAgents", "ClearToolResults", "RepairToolArguments"]
_RESEARCHER = ["WebSearch", "WebFetch", "SubAgents", "ToolOutputLimits"]


def test_a_researcher_that_needs_no_workspace_leaves_nothing_to_tell_it_by() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    from pydantic_ai_harness.tool_output_limits import LocalFileOverflowStore

    researcher = harness.Researcher(instructions=None, store=LocalFileOverflowStore())
    result = _agent_blueprint(Agent(TestModel(), capabilities=[researcher]))
    assert [c.name for c in result.capabilities[2:]] == ["WebSearch", "WebFetch", "SubAgents", "ToolOutputLimits"]


def test_a_model_fallback_shows_its_chain_of_models() -> None:
    model_fallback: Any = getattr(pydantic_ai.capabilities, "ModelFallback", None)
    if model_fallback is None:
        pytest.skip("ModelFallback is not in this release")
    (fallback,) = _agent_blueprint(Agent(capabilities=[model_fallback("openai:gpt-5", "test")])).capabilities[2:]
    assert (fallback.category, fallback.details["models"]) == ("loop", ["openai:gpt-5", "test"])


@pytest.mark.parametrize(("category", "name"), [(category, names[0]) for category, names in _GROUPS.items()])
def test_a_capability_of_each_group_of_the_docs_is_put_in_it(category: str, name: str) -> None:
    # Only the name and the package count, so a stand-in for a class of the harness, which is not installed, does.
    stand_in = type(name, (AbstractCapability,), {"__module__": "pydantic_ai_harness.stand_in"})
    (found,) = _agent_blueprint(Agent(capabilities=[stand_in()])).capabilities[2:]
    assert (found.name, found.category, found.package) == (name, category, "pydantic_ai_harness")


@dataclass
class Memory(AbstractCapability[Any]):
    size: int = 10
    api_key: str | None = None


Memory.__module__ = "pydantic_ai_harness.memory._capability"


class MyMCP(Thinking):
    pass


class Audit(AbstractCapability[Any]):
    pass


def test_a_capability_is_placed_by_the_class_of_pydantic_ai_it_is() -> None:
    result = _agent_blueprint(Agent(capabilities=[Memory(size=20, api_key="sk"), MyMCP(), Audit()]))
    assert [(c.name, c.category, c.package, c.details) for c in result.capabilities[2:]] == [
        ("Memory", "knowledge", "pydantic_ai_harness", {"size": 20}),
        ("MyMCP", "reasoning", None, {}),  # a class of the project, in the group of the class it derives from
        ("Audit", "custom", None, {}),
    ]


@dataclass
class Flaky(AbstractCapability[Any]):
    fail: bool = False

    def get_native_tools(self) -> list[Any]:
        if self.fail:
            raise RuntimeError("boom")
        return []


def test_a_capability_that_fails_to_say_its_native_tools_is_still_shown() -> None:
    flaky = Flaky()
    agent = Agent(capabilities=[flaky])
    flaky.fail = True  # Pydantic AI asks once, when the agent is made
    result = _agent_blueprint(agent)
    (found,) = result.capabilities[2:]
    assert (found.name, found.native_tools, found.details) == ("Flaky", [], {"fail": True})
    assert result.warnings == ["capability Flaky could not be read (RuntimeError: boom)"]


@dataclass(init=False)
class Window(AbstractCapability[Any]):
    size: int
    ratio: float

    def __init__(self, size: int = 10, ratio: float = 0.5) -> None:
        self.size = size
        self.ratio = ratio


def test_a_capability_with_its_own_init_leaves_out_its_defaults_and_writes_valid_json() -> None:
    result = _agent_blueprint(Agent(capabilities=[Window(), Window(size=20, ratio=float("inf"))]))
    assert [c.details for c in result.capabilities[2:]] == [{}, {"size": 20, "ratio": "inf"}]
    json.dumps(result.model_dump(mode="json"), allow_nan=False)  # ``Infinity`` is not JSON
    retry: Any = getattr(pydantic_ai.capabilities, "RetryOnModelErrors", None)
    if retry is not None:
        (found,) = _agent_blueprint(Agent(capabilities=[retry()])).capabilities[2:]
        assert found.details == {}


def test_values_the_class_fills_in_itself_are_left_out() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    cases: list[tuple[Any, dict[str, Any]]] = [
        (harness.Shell(), {}),
        (harness.Advisor("test"), {"model": "test"}),  # what the class requires is shown, default or not
        (harness.BackgroundTools(), {}),
        (harness.Shell(denied_commands=["rm"]), {"denied_commands": ["rm"]}),
        (Thinking(effort="high"), {"effort": "high"}),
    ]
    for capability, expected in cases:
        (found,) = _agent_blueprint(Agent(TestModel(), capabilities=[capability])).capabilities[2:]
        assert found.details == expected, type(capability).__name__


def _extra_instructions() -> str:
    return "Be brief."


def test_a_plain_capability_shows_the_instructions_it_was_given() -> None:
    one = _agent_blueprint(Agent(TestModel(), capabilities=[Capability(instructions="Be kind.")]))
    several = _agent_blueprint(
        Agent(TestModel(), capabilities=[Capability(instructions=["Be kind.", _extra_instructions])])
    )
    assert one.capabilities[-1].details == {"instructions": "Be kind."}
    assert several.capabilities[-1].details == {"instructions": ["Be kind.", f"{MODULE}._extra_instructions"]}


def test_a_capability_says_the_id_pydantic_ai_knows_it_by() -> None:
    result = _agent_blueprint(
        Agent(TestModel(), capabilities=[Thinking(), WebSearch(id="search", defer_loading=True), Hooks()])
    )
    assert [(c.name, c.capability_id) for c in result.capabilities] == [
        ("ToolSearch", "tool_search"),
        ("PendingMessageDrainCapability", None),
        ("Thinking", "thinking"),
        ("WebSearch", "search"),
        ("Hooks", None),
    ]


def test_settings_say_which_values_are_pydantic_ais_defaults() -> None:
    assert _agent_blueprint(Agent("test")).settings.defaults == ["end_strategy", "tool_retries", "output_retries"]
    assert _agent_blueprint(Agent("test", retries=3)).settings.defaults == ["end_strategy"]
    given = Agent("test", end_strategy="exhaustive", retries=3, tool_timeout=5)
    assert _agent_blueprint(given).settings.defaults == []


# ---------------------------------------------------------------- delegation


def test_delegation_through_a_global() -> None:
    assert _agent_blueprint(main_agent).delegates == snapshot(
        [Delegate(tool_name="delegate_research", agent_id="a2", evidence="object")]
    )
    assert find_delegates(main_agent) == [("delegate_research", researcher)]


def test_the_agents_of_sub_agents_are_delegates_through_its_tool() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    helper = Agent(TestModel(), name="helper")
    boss = Agent(
        TestModel(),
        name="boss",
        capabilities=[harness.SubAgents(agents=[harness.SubAgent(helper, name="h", description="Helps.")])],
    )
    renamed = Agent(
        TestModel(),
        capabilities=[harness.SubAgents([harness.SubAgent(helper)], tool_name="hand_off", include_self=True)],
    )
    assert find_delegates(boss) == [("delegate_task", helper)]
    assert agent_closure(boss) == [boss, helper]
    result = _agent_blueprint(boss)
    assert result.delegates == [Delegate(tool_name="delegate_task", agent_id="a2", evidence="object")]
    (sub_agents,) = result.capabilities[2:]
    assert sub_agents.details == {"agents": [{"agent": "helper", "name": "h", "description": "Helps."}]}
    assert _agent_blueprint(renamed).delegates == [Delegate(tool_name="hand_off", agent_id="a2", evidence="object")]
    with pydantic_ai_viz.record(boss, instrument=False) as session:
        agents = [event["agent"] for event in session.events if event["type"] == "blueprint.agent"]
    assert [(a["label"], a["delegates"]) for a in agents] == [
        ("boss", [{"tool_name": "delegate_task", "agent_id": agents[1]["id"], "evidence": "object"}]),
        ("helper", []),
    ]


def _closure_agents() -> tuple[Agent[None, str], Agent[None, str], Agent[None, str]]:
    writer = Agent(name="writer")
    critic = Agent(name="critic")
    planner = Agent(name="planner")

    @planner.tool_plain
    async def write(topic: str) -> str:
        return (await writer.run(topic)).output

    @planner.tool_plain
    async def review(draft: str) -> str:
        return (await critic.run(draft)).output

    @critic.tool_plain
    async def ask_planner(question: str) -> str:
        return (await planner.run(question)).output

    @writer.tool_plain
    async def rewrite(draft: str) -> str:
        return (await writer.run(draft)).output

    return planner, writer, critic


def test_delegation_through_a_closure_is_cycle_safe() -> None:
    planner, writer, critic = _closure_agents()
    assert agent_closure(planner) == [planner, writer, critic]
    assert [d.model_dump() for d in _agent_blueprint(planner).delegates] == snapshot(
        [
            {"tool_name": "write", "agent_id": "a2", "evidence": "object"},
            {"tool_name": "review", "agent_id": "a3", "evidence": "object"},
        ]
    )
    # Seen from the critic, the numbering restarts: it is "a1" and the planner "a2".
    assert [d.model_dump() for d in _agent_blueprint(critic).delegates] == snapshot(
        [{"tool_name": "ask_planner", "agent_id": "a2", "evidence": "object"}]
    )
    # A tool that calls its own agent points back at the root.
    assert [d.model_dump() for d in _agent_blueprint(writer).delegates] == snapshot(
        [{"tool_name": "rewrite", "agent_id": "a1", "evidence": "object"}]
    )


def test_delegation_through_a_renamed_tool_uses_the_visible_name() -> None:
    helper = Agent(name="helper")
    tools = FunctionToolset[Any]()

    @tools.tool_plain
    async def ask(question: str) -> str:
        return (await helper.run(question)).output

    result = _agent_blueprint(Agent(toolsets=[tools.prefixed("team")]))
    assert [d.model_dump() for d in result.delegates] == snapshot(
        [{"tool_name": "team_ask", "agent_id": "a2", "evidence": "object"}]
    )


def test_session_seam_ids_and_labels_come_from_the_caller() -> None:
    planner, writer, critic = _closure_agents()
    ids = {id(writer): "a9"}
    result = introspect_agent(planner, id="a7", label="my_planner", delegate_id=lambda agent: ids.get(id(agent)))
    assert result.id == "a7"
    assert result.label == "planner"
    # The critic has no id, so its edge is dropped rather than left dangling.
    assert [d.model_dump() for d in result.delegates] == snapshot(
        [{"tool_name": "write", "agent_id": "a9", "evidence": "object"}]
    )
    assert critic.name == "critic"


# --------------------------------------------------------------------- label


def test_label_falls_back_to_the_caller_label_then_to_the_ordinal() -> None:
    unnamed = Agent()
    assert _agent_blueprint(unnamed).label == "agent-1"
    assert introspect_agent(unnamed, id="a12", delegate_id=lambda agent: None).label == "agent-12"
    assert introspect_agent(unnamed, id="a12", label="triage", delegate_id=lambda agent: None).label == "triage"
    assert _agent_blueprint(unnamed).name is None


# -------------------------------------------------------------------- purity


def test_blueprint_is_pure() -> None:
    agent = Agent()
    first = _agent_blueprint(agent)
    assert agent.name is None, "introspection must not trigger name inference"
    assert _agent_blueprint(agent) == first
    assert first.source == "object"


def test_blueprint_rejects_other_objects() -> None:
    with pytest.raises(TypeError, match=r"expects a pydantic_ai.Agent or a pydantic_graph.Graph, got int"):
        blueprint(42)  # pyright: ignore[reportArgumentType]


def test_public_exports() -> None:
    assert {"blueprint", "AgentBlueprint", "GraphBlueprint", "__version__"} <= set(pydantic_ai_viz.__all__)
