"""Contract tests for the private pydantic-ai surface named in ``_compat``.

The first half fails loudly when an upgrade removes something we read. The second
half proves that, when it does, users get a degraded blueprint and not a crash.
"""

from __future__ import annotations

from typing import Any

import pytest
from inline_snapshot import snapshot
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from pydantic_ai_viz import AgentBlueprint, _compat, blueprint


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


def test_builtin_capability_names_match_the_installed_release() -> None:
    names = {type(capability).__name__ for capability in Agent().root_capability.capabilities}
    assert names <= _compat.BUILTIN_CAPABILITIES
    assert names == snapshot({"PendingMessageDrainCapability", "ToolSearch"})


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
        {"mode": "unknown", "allows_text": None, "type_name": "Answer", "tool_names": []}
    )
    assert result.settings.model_dump() == snapshot(
        {
            "model_settings": None,
            "end_strategy": "graceful",
            "tool_retries": None,
            "output_retries": None,
            "tool_timeout": None,
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
