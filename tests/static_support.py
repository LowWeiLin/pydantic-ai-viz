"""Helpers shared by the ``test_static_*`` modules."""

from __future__ import annotations

import copy
import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from pydantic_ai_viz import AgentBlueprint, Analysis, GraphBlueprint, analyze, blueprint
from pydantic_ai_viz._models import Tool

FIXTURES = Path(__file__).parent / "fixtures" / "static"
"""Source files the analyser reads. They are data: ruff, pyright and pytest leave them alone."""


def fixture(name: str) -> Analysis:
    return analyze(FIXTURES / name)


def analyze_source(directory: Path, source: str, name: str = "module.py") -> Analysis:
    """Write ``source`` (dedented) to a file and analyse it."""
    path = directory / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return analyze(path)


def agent(found: Analysis, label: str) -> AgentBlueprint:
    matches = [blueprint for blueprint in found.agents if blueprint.label == label]
    assert len(matches) == 1, f"{label!r} matches {[b.label for b in found.agents]}"
    return matches[0]


def graph(found: Analysis, label: str) -> GraphBlueprint:
    matches = [blueprint for blueprint in found.graphs if blueprint.label == label]
    assert len(matches) == 1, f"{label!r} matches {[b.label for b in found.graphs]}"
    return matches[0]


def tool(blueprint: AgentBlueprint, name: str) -> Tool:
    matches = [item for item in blueprint.tools if item.name == name]
    assert len(matches) == 1, f"{name!r} matches {[t.name for t in blueprint.tools]}"
    return matches[0]


def properties(schema: Any) -> dict[str, Any]:
    assert isinstance(schema, dict), schema
    return schema.get("properties", {})


def one_agent(directory: Path, source: str) -> AgentBlueprint:
    """The single agent of a source snippet."""
    found = analyze_source(directory, source)
    assert len(found.agents) == 1, [b.label for b in found.agents]
    return found.agents[0]


def live_agents(path: Path) -> dict[str, AgentBlueprint]:
    """Run a fixture file and describe each agent it makes from the live object, by label."""
    spec = importlib.util.spec_from_file_location(f"live_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is made
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    found = [blueprint(value) for value in vars(module).values() if isinstance(value, Agent)]
    return {b.label: b for b in found if isinstance(b, AgentBlueprint)}


def without_titles(schema: Any) -> Any:
    """A schema without the per-field titles pydantic derives from field names."""
    schema = copy.deepcopy(schema)
    stack: list[Any] = [schema]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            properties = item.get("properties")
            if isinstance(properties, dict):
                for field in properties.values():
                    if isinstance(field, dict):
                        field.pop("title", None)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return schema
