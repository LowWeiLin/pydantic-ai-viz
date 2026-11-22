"""Contract tests for the private pydantic-ai surface named in ``_compat``.

The first half fails loudly when an upgrade removes something we read. The second
half proves that, when it does, users get a degraded blueprint and not a crash.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pydantic_ai.capabilities
import pytest
from inline_snapshot import snapshot
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability, Capability, WebFetch, WebSearch
from pydantic_ai.models.test import TestModel

from pydantic_ai_viz import AgentBlueprint, _compat, blueprint
from pydantic_ai_viz._analyze._agents import (
    _CAPABILITY_NATIVE_TOOLS,
    _HARNESS_POSITIONAL,
    _HARNESS_TOOLSETS,
    _LOCAL_FALLBACKS,
    _NATIVE_ONLY_FIELDS,
)
from pydantic_ai_viz._capabilities import category_of


class Answer(BaseModel):
    value: int


def _agent() -> Agent[None, Answer]:
    agent = Agent(
        TestModel(),
        output_type=Answer,
        instructions="Static instructions.",
        system_prompt="Static system prompt.",
        tool_timeout=5,
    )

    @agent.instructions
    def dynamic_instructions() -> str:
        return "dynamic"

    @agent.system_prompt
    def dynamic_system_prompt() -> str:
        return "dynamic"

    @agent.tool_plain
    def double(x: int) -> int:
        return x * 2

    return agent


def _blueprint(agent: Agent[Any, Any]) -> AgentBlueprint:
    result = blueprint(agent)
    assert isinstance(result, AgentBlueprint)
    assert AgentBlueprint.model_validate(result.model_dump(mode="json")) == result
    return result


# ------------------------------------------------------------------ contract


@pytest.mark.parametrize("attr", sorted(_compat.AGENT_PRIVATE_ATTRS))
def test_private_agent_attribute_exists(attr: str) -> None:
    assert hasattr(_agent(), attr), f"pydantic-ai no longer has Agent.{attr}"


def test_instrumentation_default_is_readable() -> None:
    assert hasattr(Agent, _compat.AGENT_INSTRUMENT_DEFAULT), "pydantic-ai no longer has Agent._instrument_default"
    assert _compat.instrumentation_default() is False
    Agent.instrument_all(True)
    assert _compat.instrumentation_default() is True


def test_private_attribute_shapes() -> None:
    agent: Any = _agent()

    static, dynamic = agent._instructions
    assert static.instruction == "Static instructions."
    assert static.dynamic is False
    assert callable(dynamic.instruction)
    assert dynamic.dynamic is True

    assert agent._system_prompts == ("Static system prompt.",)
    (runner,) = agent._system_prompt_functions
    assert runner.function.__name__ == "dynamic_system_prompt"

    assert isinstance(agent._max_tool_retries, int)
    assert isinstance(agent._max_output_retries, int)
    assert agent._tool_timeout == 5

    assert agent._output_schema.mode == "auto"
    assert isinstance(agent._output_schema.allows_text, bool)
    tool_defs = getattr(agent._output_toolset, _compat.OUTPUT_TOOLSET_TOOL_DEFS)
    assert [tool_def.name for tool_def in tool_defs] == ["final_result"]

    assert [tool.kind for tool in agent._cap_native_tools] == ["tool_search"]


def test_the_native_tool_of_a_capability_loaded_on_demand_is_held_by_the_function_in_its_place() -> None:
    def search(query: str) -> str:
        return query

    agent: Any = Agent(TestModel(), capabilities=[WebSearch(local=search, defer_loading=True, id="ws")])
    _, deferred = agent._cap_native_tools
    assert not hasattr(deferred, "kind")
    assert deferred.__kwdefaults__[_compat.DEFERRED_NATIVE_TOOL_ARG].kind == "web_search"


def test_builtin_capability_names_match_the_installed_release() -> None:
    names = {type(capability).__name__ for capability in Agent().root_capability.capabilities}
    assert names <= _compat.BUILTIN_CAPABILITIES
    assert names == snapshot({"PendingMessageDrainCapability", "ToolSearch"})


_CAPABILITY_BASES = frozenset(
    {
        "AbstractCapability",
        "Capability",
        "CombinedCapability",
        "WrapperCapability",
        "NativeOrLocalTool",
        "DynamicCapability",
    }
)
"""What ``pydantic_ai.capabilities`` exports to build capabilities with, rather than as capabilities of a kind."""


def test_every_capability_of_the_installed_release_is_in_a_group_of_the_docs() -> None:
    exported = [getattr(pydantic_ai.capabilities, name) for name in dir(pydantic_ai.capabilities)]
    classes = [
        cls
        for cls in exported
        if inspect.isclass(cls) and issubclass(cls, AbstractCapability) and cls.__name__ not in _CAPABILITY_BASES
    ]
    assert classes
    assert [cls.__name__ for cls in classes if category_of(cls.__module__, cls.__name__) is None] == []


def test_every_capability_the_installed_harness_exports_is_in_a_group_of_the_docs() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    classes: list[type] = []
    for name in harness.__all__:
        try:
            exported = getattr(harness, name)
        except ImportError:  # exported lazily, and needs an extra that is not installed
            continue
        if inspect.isclass(exported) and issubclass(exported, AbstractCapability):
            classes.append(exported)
    assert classes
    assert [cls.__name__ for cls in classes if category_of(cls.__module__, cls.__name__) is None] == []


@pytest.mark.parametrize(("name", "kind"), sorted(_CAPABILITY_NATIVE_TOOLS.items()))
def test_static_analysis_knows_the_native_tool_each_capability_gives(name: str, kind: str) -> None:
    capability = getattr(pydantic_ai.capabilities, name, None)
    if capability is None:
        pytest.skip(f"{name} is not in this release")
    assert [tool.kind for tool in capability().get_native_tools()] == [kind]


@pytest.mark.parametrize("name", sorted(_LOCAL_FALLBACKS))
def test_static_analysis_knows_the_toolset_a_capability_puts_its_local_tool_in(name: str) -> None:
    capability: Any = getattr(pydantic_ai.capabilities, name, None)
    if capability is None or "local" not in inspect.signature(capability).parameters:
        pytest.skip(f"{name} takes no local tool in this release")
    toolset_id = _LOCAL_FALLBACKS[name]

    def lookup(query: str) -> str:
        return query

    owned = "CapabilityOwnedToolset"
    for given, wrappers in [({}, [owned, "PreparedToolset"]), ({"native": False}, [owned])]:
        result = _blueprint(Agent(TestModel(), capabilities=[capability(local=lookup, **given)]))
        (toolset,) = result.toolsets
        assert (toolset.label, toolset.toolset_id, toolset.kind) == (
            f"FunctionToolset {toolset_id!r}",
            toolset_id,
            "function",
        )
        assert toolset.wrappers == wrappers
        assert [(tool.name, tool.toolset) for tool in result.tools] == [("lookup", toolset.id)]
    # Where static analysis reads what ``local=True`` stands for.
    assert callable(getattr(capability, _compat.LOCAL_STRATEGY_METHOD, None))


def _harness_needs(name: str, tmp_path: Path) -> dict[str, Any]:
    """What a harness capability must be given for it to give its toolset."""
    harness: Any = pytest.importorskip("pydantic_ai_harness")
    needs: dict[str, dict[str, Any]] = {
        "CapabilityCreation": {"directory": tmp_path},
        "SubAgents": {"agents": [harness.SubAgent(Agent(TestModel()), name="h", description="d")]},
        "AskUser": {"answerer": lambda question: "a"},  # pyright: ignore[reportUnknownLambdaType]
        "Advisor": {"model": "test"},
        "Skills": {"directories": tmp_path, "read_files": True},
    }
    return needs.get(name, {})


@pytest.mark.parametrize("name", sorted(_HARNESS_TOOLSETS))
def test_static_analysis_knows_the_toolset_each_harness_capability_gives(name: str, tmp_path: Path) -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    toolset_class, toolset_id, renamed, inner = _HARNESS_TOOLSETS[name]
    needs = _harness_needs(name, tmp_path)
    cases: list[tuple[dict[str, Any], str | None]] = [({}, toolset_id)]
    if "id" in inspect.signature(getattr(harness, name)).parameters:  # ``Skills`` has the one id its class names
        cases.append(({"id": "custom"}, "custom" if renamed else toolset_id))
    for given, expected in cases:
        result = _blueprint(Agent(TestModel(), capabilities=[getattr(harness, name)(**needs, **given)]))
        (toolset,) = result.toolsets
        label = toolset_class + (f" {expected!r}" if expected else "")
        assert (toolset.label, toolset.toolset_id, toolset.kind) == (label, expected, "function")
        assert toolset.wrappers == ["CapabilityOwnedToolset", *inner]
        assert result.capabilities[-1].toolsets == [toolset.id]
        assert result.capabilities[-1].native_tools == []
    if name == "FileSystem":  # static analysis adds the filter a read-only one puts around its toolset
        (toolset,) = _blueprint(Agent(TestModel(), capabilities=[harness.FileSystem(read_only=True)])).toolsets
        assert toolset.wrappers == ["CapabilityOwnedToolset", "FilteredToolset"]


def test_some_harness_capabilities_give_a_toolset_only_when_given_what_it_holds(tmp_path: Path) -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    for capability in [harness.SubAgents(), harness.Skills(tmp_path), harness.Skills(tmp_path, read_files=False)]:
        assert _blueprint(Agent(TestModel(), capabilities=[capability])).toolsets == []


def test_positional_parameters_of_harness_capabilities_are_those_their_classes_name() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    for name, expected in _HARNESS_POSITIONAL.items():
        parameters = inspect.signature(getattr(harness, name).__init__).parameters.values()
        positional = [p.name for p in parameters if p.kind is p.POSITIONAL_OR_KEYWORD and p.name != "self"]
        assert tuple(positional[: len(expected)]) == expected, name


@pytest.mark.parametrize(
    ("name", "field"), [(n, f) for n, fields in _NATIVE_ONLY_FIELDS.items() for f in sorted(fields)]
)
def test_a_constraint_only_the_provider_side_tool_honours_leaves_no_local_tool(name: str, field: str) -> None:
    def lookup(query: str) -> str:
        return query

    capability: Any = {"WebSearch": WebSearch, "WebFetch": WebFetch}[name]
    value: Any = 1 if field == "max_uses" else ["a.example"]
    assert _blueprint(Agent(TestModel(), capabilities=[capability(local=lookup, **{field: value})])).toolsets == []
    # Another setting keeps it: the set holds those that take it away, and only those.
    kept = {"user_location": {"city": "Paris"}} if name == "WebSearch" else {}
    assert len(_blueprint(Agent(TestModel(), capabilities=[capability(local=lookup, **kept)])).toolsets) == 1


def test_harness_stacks_put_only_these_capabilities_after_their_marker() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    assert sorted(_compat.HARNESS_STACK_MEMBERS) == sorted(_compat.HARNESS_STACKS)
    for stack in sorted(_compat.HARNESS_STACKS):
        capabilities = Agent(TestModel(), capabilities=[getattr(harness, stack)()]).root_capability.capabilities
        names = [type(c).__name__ for c in capabilities]
        after = names[names.index(_compat.HARNESS_STACK_MARKER) + 1 :]
        public = [n for n in after if not n.startswith("_") and n not in _compat.BUILTIN_CAPABILITIES]
        assert [n for n in public if n != "Capability"] == list(_compat.HARNESS_STACK_MEMBERS[stack]), stack


def test_the_harness_skills_capability_is_where_it_is_named() -> None:
    skills = pytest.importorskip("pydantic_ai_harness.skills")
    capability = skills.Skills(".")
    assert type(capability).__module__.startswith(_compat.HARNESS_SKILLS_MODULE)
    (source,) = getattr(capability, _compat.SKILLS_SOURCES)
    assert source.workspace is None
    assert callable(capability.load)


def test_sub_agents_names_its_tool_delegate_task_by_default() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    assert inspect.signature(harness.SubAgents).parameters["tool_name"].default == _compat.SUB_AGENTS_TOOL_NAME


def test_hooks_registers_each_keyword_and_decorator_for_the_point_named() -> None:
    from pydantic_ai.capabilities import Hooks

    def function(*args: Any, **kwargs: Any) -> None: ...

    hook: Any = function  # each keyword and decorator takes a function of its own signature

    assert Hooks.__module__ == _compat.HOOKS_MODULE
    keywords = [name for name in inspect.signature(Hooks).parameters if name not in ("ordering", "id", "description")]
    assert [name for name in keywords if name != "defer_loading"] == list(_compat.HOOK_KWARGS)
    for name, point in _compat.HOOK_KWARGS.items():
        by_keyword = Hooks(**{name: hook})
        by_decorator = Hooks()
        getattr(by_decorator.on, name)(hook)
        for hooks in (by_keyword, by_decorator):
            registry = getattr(hooks, _compat.HOOKS_REGISTRY)
            assert list(registry) == [point], name
            (entry,) = registry[point]
            assert (entry.func, entry.timeout, getattr(entry, "tools", None)) == (hook, None, None)
    tool_hooks = Hooks()
    tool_hooks.on.before_tool_execute(tools=["b", "a"], timeout=5)(hook)
    (entry,) = getattr(tool_hooks, _compat.HOOKS_REGISTRY)["before_tool_execute"]
    assert (entry.tools, entry.timeout) == (frozenset({"a", "b"}), 5)


def test_a_plain_capability_keeps_the_instructions_it_was_given() -> None:
    def extra() -> str:
        return "b"

    held = getattr(Capability(instructions=["a", extra]), _compat.CAPABILITY_INSTRUCTIONS)
    assert [item.instruction for item in held] == ["a", extra]


def test_a_toolset_a_capability_gives_still_names_the_capability() -> None:
    def double(x: int) -> int:
        return x * 2

    capabilities = _blueprint(Agent(capabilities=[Capability(tools=[double])])).capabilities
    assert capabilities[-1].toolsets == ["ts0"]


def test_a_harness_stack_leaves_its_marker_among_the_agents_capabilities() -> None:
    harness = pytest.importorskip("pydantic_ai_harness")
    assert sorted(_compat.HARNESS_STACKS) == ["Coder", "Researcher"]
    for stack in sorted(_compat.HARNESS_STACKS):
        capabilities = Agent(TestModel(), capabilities=[getattr(harness, stack)()]).root_capability.capabilities
        markers = [c for c in capabilities if type(c).__name__ == _compat.HARNESS_STACK_MARKER]
        assert [getattr(marker, _compat.HARNESS_STACK_OWNER) for marker in markers] == [stack]
        assert type(markers[0]).__module__.startswith("pydantic_ai_harness.")
        helpers = [type(c) for c in capabilities if type(c).__name__.startswith("_")]
        assert all(cls.__module__.startswith(_compat.HARNESS_STACK_MODULES) for cls in helpers)
        plain = [getattr(c, "id", None) for c in capabilities if type(c) is Capability]
        assert plain == [i for i, owner in _compat.HARNESS_STACK_HELPER_IDS.items() if owner == stack]


def test_pydantic_ai_version_is_reported() -> None:
    assert _compat.pydantic_ai_version().split(".")[0] == "2"


# --------------------------------------------------------------- degradation


def test_blueprint_survives_losing_every_private_attribute() -> None:
    agent = _agent()
    for attr in _compat.AGENT_PRIVATE_ATTRS:
        delattr(agent, attr)

    result = _blueprint(agent)

    assert result.warnings == snapshot(
        [
            "instructions unavailable (pydantic-ai internals changed)",
            "static system prompts unavailable (pydantic-ai internals changed)",
            "dynamic system prompts unavailable (pydantic-ai internals changed)",
            "native tools unavailable (pydantic-ai internals changed)",
            "output mode unavailable (pydantic-ai internals changed)",
            "output tool names unavailable (pydantic-ai internals changed)",
            "tool timeout unavailable (pydantic-ai internals changed)",
            "tool retries unavailable (pydantic-ai internals changed)",
            "output retries unavailable (pydantic-ai internals changed)",
        ]
    )
    assert result.instructions == []
    assert result.native_tools == []
    assert result.output.model_dump(exclude={"json_schema"}) == snapshot(
        {"mode": "unknown", "allows_text": None, "type_name": "Answer", "tool_names": [], "location": None}
    )
    assert result.settings.model_dump() == snapshot(
        {
            "model_settings": None,
            "end_strategy": "graceful",
            "tool_retries": None,
            "output_retries": None,
            "tool_timeout": None,
            "expressions": {},
            "defaults": ["end_strategy"],
        }
    )
    # Everything read through public API is still there.
    assert [tool.name for tool in result.tools] == ["double"]
    assert result.model is not None
    assert result.output.json_schema is not None


@pytest.mark.parametrize("attr", sorted(_compat.AGENT_PRIVATE_ATTRS))
def test_each_missing_attribute_yields_exactly_one_warning(attr: str) -> None:
    agent = _agent()
    delattr(agent, attr)
    label = _compat.AGENT_PRIVATE_ATTRS[attr]
    assert _blueprint(agent).warnings == [f"{label} unavailable (pydantic-ai internals changed)"]


def test_output_mode_falls_back_to_text_for_a_string_schema() -> None:
    agent = Agent(TestModel())
    delattr(agent, "_output_schema")
    output = _blueprint(agent).output
    assert (output.mode, output.allows_text) == ("text", True)


def test_missing_output_tool_defs_only_lose_the_tool_names() -> None:
    agent: Any = _agent()
    agent._output_toolset = object()
    result = _blueprint(agent)
    assert result.output.tool_names == []
    assert result.output.mode == "auto"
    assert result.warnings == ["output tool names unavailable (pydantic-ai internals changed)"]


def test_get_private_never_raises_and_does_not_repeat_warnings() -> None:
    class Broken:
        @property
        def _secret(self) -> int:
            raise RuntimeError("boom")

    warnings: list[str] = []
    assert _compat.get_private(Broken(), "_secret", 7, warnings) == 7
    assert _compat.get_private(Broken(), "_secret", 7, warnings) == 7
    assert _compat.get_private(Broken(), "_other_thing", None, warnings, "the other thing") is None
    assert warnings == [
        "secret unavailable (pydantic-ai internals changed)",
        "the other thing unavailable (pydantic-ai internals changed)",
    ]


def test_get_private_returns_falsy_values_as_they_are() -> None:
    class Holder:
        _value = None
        _items = ()

    warnings: list[str] = []
    assert _compat.get_private(Holder(), "_value", "default", warnings) is None
    assert _compat.get_private(Holder(), "_items", "default", warnings) == ()
    assert warnings == []
