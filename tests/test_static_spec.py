"""Agents made from a spec, read from source: ``Agent.from_file("agent.json")`` and ``Agent.from_spec({...})``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from static_support import live_agents, one_agent

from pydantic_ai_viz import AgentBlueprint, analyze, blueprint

SPEC: dict[str, Any] = {
    "model": "openai:gpt-5.2",
    "name": "writer",
    "description": "Writes briefs.",
    "instructions": ["Be brief.", "Cite sources."],
    "model_settings": {"temperature": 0.2, "max_tokens": 500},
    "retries": 3,
    "end_strategy": "exhaustive",
    "tool_timeout": 12.5,
    "capabilities": [{"Thinking": {"effort": "high"}}, "WebSearch", {"ReinjectSystemPrompt": True}],
}


def write_spec(directory: Path, spec: dict[str, Any] = SPEC, name: str = "agent.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def capabilities(found: AgentBlueprint) -> list[tuple[str, str, str | None, dict[str, Any]]]:
    return [(c.name, c.category, c.package, c.details) for c in found.capabilities if not c.builtin]


def test_an_agent_made_from_a_spec_is_described_live() -> None:
    made = Agent.from_spec({**SPEC, "model": "test"})
    found = blueprint(made)
    assert isinstance(found, AgentBlueprint)
    assert capabilities(found) == [
        ("Thinking", "reasoning", "pydantic_ai", {"effort": "high"}),
        ("WebSearch", "web", "pydantic_ai", {}),
        ("ReinjectSystemPrompt", "loop", "pydantic_ai", {"replace_existing": True}),
    ]


def test_a_json_spec_file_is_read_as_the_agent_it_makes(tmp_path: Path) -> None:
    write_spec(tmp_path)
    found = one_agent(tmp_path, 'from pydantic_ai import Agent\n\nagent = Agent.from_file("agent.json")\n')
    assert (found.name, found.description, found.label) == ("writer", "Writes briefs.", "writer")
    assert found.model is not None
    assert (found.model.provider, found.model.name) == ("openai", "gpt-5.2")
    assert [(i.text, i.dynamic, i.location) for i in found.instructions] == [
        ("Be brief.", False, found.instructions[0].location),
        ("Cite sources.", False, found.instructions[0].location),
    ]
    assert found.instructions[0].location is not None
    assert (found.instructions[0].location.file, found.instructions[0].location.line) == ("agent.json", 1)
    assert found.location is not None
    assert (found.location.file, found.location.line) == ("module.py", 3)
    settings = found.settings
    assert settings.model_settings == {"temperature": 0.2, "max_tokens": 500}
    assert (settings.end_strategy, settings.tool_retries, settings.output_retries, settings.tool_timeout) == (
        "exhaustive",
        3,
        3,
        12.5,
    )
    assert capabilities(found) == [
        ("Thinking", "reasoning", "pydantic_ai", {"effort": "high"}),
        ("WebSearch", "web", "pydantic_ai", {}),
        ("ReinjectSystemPrompt", "loop", "pydantic_ai", {"replace_existing": True}),
    ]
    assert found.native_tools == ["web_search"]
    assert found.warnings == []


def test_a_dict_given_to_from_spec_is_read_and_keywords_replace_or_add_to_it(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.capabilities import WebFetch

        agent = Agent.from_spec(
            {
                "model": "test",
                "name": "spec-name",
                "instructions": "From the spec. Hello {{name}}",
                "model_settings": {"temperature": 0.2, "max_tokens": 500},
                "retries": 3,
                "capabilities": ["WebSearch"],
                "output_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            },
            model="openai:gpt-5.2",
            name=None,
            instructions="From the call.",
            model_settings={"temperature": 0.9},
            retries={"output": 5},
            capabilities=[WebFetch()],
        )
        """,
    )
    assert found.model is not None
    assert found.model.id == "openai:gpt-5.2"
    assert found.name == "spec-name"  # ``name or spec.name``: a ``None`` keeps the spec's
    assert [(i.text, i.dynamic) for i in found.instructions] == [
        ("From the spec. Hello {{name}}", True),
        ("From the call.", False),
    ]
    assert found.settings.model_settings == {"temperature": 0.9, "max_tokens": 500}
    assert (found.settings.tool_retries, found.settings.output_retries) == (3, 5)
    assert [c.name for c in found.capabilities] == ["WebSearch", "WebFetch"]
    assert (found.output.mode, found.output.json_schema) == (
        "auto",
        {"type": "object", "properties": {"a": {"type": "string"}}},
    )
    assert found.warnings == []


def test_custom_capability_types_are_found_and_an_unknown_name_is_custom(tmp_path: Path) -> None:
    pytest.importorskip("pydantic_ai_harness")
    write_spec(
        tmp_path,
        {"capabilities": [{"Skills": ["skills"]}, {"Mystery": {"level": 2}}]},
    )
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai_harness import Skills

        agent = Agent.from_file("agent.json", model="test", custom_capability_types=[Skills])
        """,
    )
    skills, mystery = found.capabilities
    assert (skills.name, skills.category, skills.package, skills.details) == (
        "Skills",
        "knowledge",
        "pydantic_ai_harness",
        {"directories": ["skills"]},
    )
    assert (mystery.name, mystery.category, mystery.package, mystery.details) == (
        "Mystery",
        "custom",
        None,
        {"level": 2},
    )
    assert any("`Mystery`" in warning and "custom_capability_types" in warning for warning in found.warnings)


def test_a_missing_spec_file_is_said_to_be_missing(tmp_path: Path) -> None:
    found = one_agent(tmp_path, 'from pydantic_ai import Agent\n\nagent = Agent.from_file("nowhere.json", name="n")\n')
    assert found.name == "n"
    assert found.warnings == ["the agent spec `nowhere.json` was not found in the analysed files"]


def test_a_spec_file_outside_the_analysed_directories_is_not_read(tmp_path: Path) -> None:
    write_spec(tmp_path)
    inner = tmp_path / "app"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[project]\nname = 'app'\n", encoding="utf-8")
    found = one_agent(inner, 'from pydantic_ai import Agent\n\nagent = Agent.from_file("../agent.json")\n')
    assert found.name is None
    assert found.warnings == ["the agent spec `../agent.json` is outside the analysed directories, so it is not read"]


def test_a_computed_spec_is_said_to_be_read_when_the_code_runs(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        import os
        from pydantic_ai import Agent

        agent = Agent.from_file(os.environ["SPEC"])
        """,
    )
    assert found.warnings == ['the agent spec file is named by `os.environ["SPEC"]`; it is read when the code runs']


YAML = """\
model: test
name: yaml-agent
instructions: Be brief.
capabilities:
  - Thinking:
      effort: low
"""


def test_a_yaml_spec_is_read_with_pyyaml(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    (tmp_path / "agent.yaml").write_text(YAML, encoding="utf-8")
    found = one_agent(tmp_path, 'from pydantic_ai import Agent\n\nagent = Agent.from_file("agent.yaml")\n')
    assert found.name == "yaml-agent"
    assert [i.text for i in found.instructions] == ["Be brief."]
    assert [(c.name, c.details) for c in found.capabilities] == [("Thinking", {"effort": "low"})]


def test_a_yaml_spec_without_pyyaml_gives_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)  # ``import yaml`` raises ImportError
    (tmp_path / "agent.yml").write_text(YAML, encoding="utf-8")
    found = one_agent(tmp_path, 'from pydantic_ai import Agent\n\nagent = Agent.from_file("agent.yml", name="n")\n')
    assert found.name == "n"
    assert found.warnings == ["`agent.yml` is YAML; install PyYAML to read it"]


def test_an_agent_from_a_spec_file_reads_as_the_live_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_spec(tmp_path, {**SPEC, "model": "test"})
    monkeypatch.chdir(tmp_path)  # a run reads the file relative to its working directory
    path = tmp_path / "app.py"
    path.write_text('from pydantic_ai import Agent\n\nagent = Agent.from_file("agent.json")\n', encoding="utf-8")
    (expected,) = live_agents(path).values()
    (found,) = analyze(path).agents
    assert (found.name, found.description) == (expected.name, expected.description)
    assert found.model is not None
    assert expected.model is not None
    assert found.model.name == expected.model.name
    assert [(i.kind, i.dynamic, i.text) for i in found.instructions] == [
        (i.kind, i.dynamic, i.text) for i in expected.instructions
    ]
    assert capabilities(found) == capabilities(expected)
    ignored = {"defaults", "expressions"}
    assert found.settings.model_dump(exclude=ignored) == expected.settings.model_dump(exclude=ignored)


def test_a_sub_agent_made_from_a_spec_file_is_named_as_the_live_agent_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pydantic_ai_harness")
    write_spec(tmp_path, {"model": "test", "name": "writer"})
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "app.py"
    path.write_text(
        "\n".join(
            [
                "from pydantic_ai import Agent",
                "from pydantic_ai_harness import SubAgent, SubAgents",
                'helper = Agent("test", name="helper")',
                'writer = Agent.from_file("agent.json")',
                'subs = SubAgents(agents=[SubAgent(helper, name="h"), SubAgent(writer)])',
                'boss = Agent("test", name="boss", capabilities=[subs])',
            ]
        ),
        encoding="utf-8",
    )
    expected = [c.details for c in live_agents(path)["boss"].capabilities if c.name == "SubAgents"]
    found = [c.details for c in {b.label: b for b in analyze(path).agents}["boss"].capabilities]
    assert found == expected == [{"agents": [{"agent": "helper", "name": "h"}, {"agent": "writer"}]}]


def test_a_spec_file_given_as_the_target_is_read_as_its_agent(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    (tmp_path / "other.json").write_text('{"items": [1, 2]}', encoding="utf-8")
    found = analyze(path)
    (made,) = found.agents
    assert made.name == "writer"
    assert made.location is not None
    assert (made.location.file, made.location.line) == ("agent.json", 1)
    assert [c.name for c in made.capabilities] == ["Thinking", "WebSearch", "ReinjectSystemPrompt"]
    assert analyze(tmp_path / "other.json").notices == ["skipped other.json: it is not an agent spec"]
