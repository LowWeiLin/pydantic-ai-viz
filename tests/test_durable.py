"""Durable-execution wrappers (``TemporalAgent`` and the like): the agent they wrap is shown with the wrapper's name."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.agent import WrapperAgent
from pydantic_ai.models.test import TestModel
from static_support import agent as blueprint_of
from static_support import analyze_source

from pydantic_ai_viz import Session, blueprint


class TemporalAgent(WrapperAgent[Any, Any]):
    """Stands in for the real class, which needs ``temporalio``: same name, same module."""


TemporalAgent.__module__ = "pydantic_ai.durable_exec.temporal._agent"


class OtherWrapper(WrapperAgent[Any, Any]):
    """A wrapper that is not a durable-execution one."""


def _wrapped() -> Agent[None, str]:
    return Agent(TestModel(), name="worker")


def test_blueprint_unwraps_a_durable_agent() -> None:
    wrapped = _wrapped()
    result = blueprint(TemporalAgent(wrapped))  # pyright: ignore[reportArgumentType]
    assert (result.id, result.name, result.wrapper) == ("a1", "worker", "TemporalAgent")  # type: ignore[union-attr]
    assert blueprint(wrapped).wrapper is None  # type: ignore[union-attr]


def test_a_wrapper_inside_a_wrapper_is_followed() -> None:
    result = blueprint(OtherWrapper(TemporalAgent(_wrapped())))  # pyright: ignore[reportArgumentType]
    assert result.wrapper == "TemporalAgent"  # type: ignore[union-attr]


def test_a_wrapper_that_is_not_durable_is_not_an_agent() -> None:
    with pytest.raises(TypeError, match="OtherWrapper"):
        blueprint(OtherWrapper(_wrapped()))  # pyright: ignore[reportArgumentType]


def test_register_takes_a_wrapper() -> None:
    with Session(instrument=False, mode="blueprint") as session:
        session.register(TemporalAgent(_wrapped()))  # pyright: ignore[reportArgumentType]
        events = session.events
    assert [e["agent"]["wrapper"] for e in events[1:]] == ["TemporalAgent"]


def test_a_wrapper_registered_after_its_agent_updates_the_agent_blueprint() -> None:
    inner = _wrapped()
    with Session(instrument=False, mode="blueprint") as session:
        session.register(inner)
        session.register(TemporalAgent(inner))  # pyright: ignore[reportArgumentType]
        events = session.events
    assert [(e["agent"]["id"], e["agent"]["wrapper"]) for e in events[1:]] == [("a1", None), ("a1", "TemporalAgent")]


def test_a_module_shows_the_wrapped_agent_once_whichever_comes_first() -> None:
    for order in (("agent", "durable"), ("durable", "agent")):
        inner = _wrapped()
        members = {"agent": inner, "durable": TemporalAgent(inner)}
        module = types.ModuleType("app")
        for key in order:
            setattr(module, key, members[key])
        with Session(instrument=False, mode="blueprint") as session:
            session.register(module)
            events = session.events
        agents = [e["agent"] for e in events if e["type"] == "blueprint.agent"]
        assert [(a["name"], a["wrapper"]) for a in agents] == [("worker", "TemporalAgent")], order


def test_static_analysis_marks_the_agent_a_wrapper_is_given(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.durable_exec.temporal import TemporalAgent
        from pydantic_ai.durable_exec.dbos import DBOSAgent

        worker = Agent("test", name="worker")
        other = Agent("test", name="other")
        plain = Agent("test", name="plain")
        durable = TemporalAgent(worker)
        also = DBOSAgent(wrapped=other)
        unknown = TemporalAgent(make_it())
        """,
    )
    assert {a.label: a.wrapper for a in found.agents} == {
        "worker": "TemporalAgent",
        "other": "DBOSAgent",
        "plain": None,
    }
    assert blueprint_of(found, "worker").source == "code"
