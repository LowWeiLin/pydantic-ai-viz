"""Helpers shared by the ``test_static_*`` modules."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from pydantic_ai_viz import AgentBlueprint, Analysis, GraphBlueprint, analyze
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
