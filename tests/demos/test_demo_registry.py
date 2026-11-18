from __future__ import annotations

import dataclasses
import inspect
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph

from pydantic_ai_viz.demos import DEMOS, Demo, pipeline, research, support


def test_demos_are_listed_in_display_order() -> None:
    assert list(DEMOS) == ["support", "research", "pipeline"]
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


def test_models_are_offline_and_honestly_named() -> None:
    for demo in DEMOS.values():
        agents = [target for target in demo.targets if isinstance(target, Agent)]
        assert agents
        for agent in agents:
            model: Any = agent.model
            assert model.model_name == f"scripted-{demo.name}"
            assert model.system == "function"
