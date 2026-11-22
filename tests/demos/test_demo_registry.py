from __future__ import annotations

import dataclasses
import inspect
import subprocess
import sys
from importlib.util import find_spec
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph

from pydantic_ai_viz.demos import DEMOS, UNAVAILABLE, Demo, pipeline, research, support

HARNESS = find_spec("pydantic_ai_harness") is not None


def test_demos_are_listed_in_display_order() -> None:
    assert list(DEMOS) == ["support", "research", "pipeline", *(["capabilities", "operations"] if HARNESS else [])]
    assert [demo.name for demo in DEMOS.values()] == list(DEMOS)
    assert all(isinstance(demo, Demo) for demo in DEMOS.values())


def test_demo_interface() -> None:
    assert [field.name for field in dataclasses.fields(Demo)] == ["name", "title", "description", "targets", "run"]
    for demo in DEMOS.values():
        assert demo.title
        assert demo.description.isascii()
        assert demo.description.endswith(".")
        assert "\n" not in demo.description
        assert inspect.iscoroutinefunction(demo.run)
        assert all(isinstance(target, Agent | Graph) for target in demo.targets)
    frozen: Any = DEMOS["support"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.name = "other"


def test_targets_are_root_first() -> None:
    assert DEMOS["support"].targets == (support.triage, support.refunds)
    assert DEMOS["research"].targets == (research.planner, research.searcher, research.writer)
    assert DEMOS["pipeline"].targets == (pipeline.graph, pipeline.writer, pipeline.critic)
    if HARNESS:
        from pydantic_ai_viz.demos import capabilities, operations

        assert DEMOS["capabilities"].targets == (capabilities.briefing,)
        assert DEMOS["operations"].targets == (operations.oncall, operations.log_reader)


def test_models_are_offline_and_honestly_named() -> None:
    for demo in DEMOS.values():
        agents = [target for target in demo.targets if isinstance(target, Agent)]
        assert agents
        for agent in agents:
            model: Any = agent.model
            assert model.model_name == f"scripted-{demo.name}"
            assert model.system == "function"


def test_a_demo_needing_the_harness_is_unavailable_without_it() -> None:
    assert (UNAVAILABLE == {}) is HARNESS
    code = (
        "import sys; sys.modules['pydantic_ai_harness'] = None\n"
        "from pydantic_ai_viz.demos import DEMOS, UNAVAILABLE\n"
        "print(list(DEMOS)); print(UNAVAILABLE)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)
    shown, unavailable = result.stdout.splitlines()[-2:]
    assert shown == "['support', 'research', 'pipeline']"
    needs = "needs pydantic-ai-harness: pip install 'pydantic-ai-viz[demos]'"
    assert unavailable == repr({"capabilities": needs, "operations": needs})


@pytest.mark.skipif(not HARNESS, reason="needs pydantic-ai-harness")
def test_a_missing_dependency_of_the_harness_is_named_not_reported_as_a_missing_harness() -> None:
    # The Skills capability needs PyYAML, from the harness skills extra that pydantic-ai-viz[demos] asks for.
    code = (
        "import sys; sys.modules['yaml'] = None\n"
        "from pydantic_ai_viz.demos import DEMOS, UNAVAILABLE\n"
        "print(list(DEMOS)); print(UNAVAILABLE['capabilities'])"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)
    shown, unavailable = result.stdout.splitlines()[-2:]
    assert shown == "['support', 'research', 'pipeline', 'operations']"
    assert "pydantic-ai-harness:" not in unavailable
    assert "PyYAML is required" in unavailable
    assert unavailable.endswith("pip install 'pydantic-ai-viz[demos]'")
