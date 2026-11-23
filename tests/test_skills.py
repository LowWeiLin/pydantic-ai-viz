"""The skills of a harness ``Skills`` capability, read from the live object as a run reads them."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

pytest.importorskip("pydantic_ai_harness")
if not hasattr(pytest.importorskip("pydantic_ai.workspaces"), "WindowsWorkspaceBackend"):
    pytest.skip("the workspaces of this release are not those Skills reads", allow_module_level=True)

import pydantic_ai_harness as harness
from pydantic_ai.workspaces import LocalWorkspaceBackend, WindowsWorkspaceBackend
from pydantic_ai.workspaces.unavailable import UnavailableWorkspace

from pydantic_ai_viz import AgentBlueprint, blueprint
from pydantic_ai_viz._models import MAX_STRING_CHARS, CapabilityInfo, SkillInfo


def host(path: Path) -> Any:
    """A workspace backend for a directory on this machine, of the kind this platform has."""
    return WindowsWorkspaceBackend(path) if sys.platform == "win32" else LocalWorkspaceBackend(path)


def workspace(path: Path) -> Any:
    """The capability that gives runs a workspace in ``path``."""
    from pydantic_ai.capabilities import LocalWorkspace, WindowsWorkspace

    return WindowsWorkspace(path) if sys.platform == "win32" else LocalWorkspace(path)


def write_skill(library: Path, name: str, frontmatter: str, body: str = "Do the thing.") -> Path:
    path = library / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{body}\n", encoding="utf-8")
    return path


def skills_capability(agent: Agent[Any, Any]) -> tuple[CapabilityInfo, AgentBlueprint]:
    result = blueprint(agent)
    assert isinstance(result, AgentBlueprint)
    assert AgentBlueprint.model_validate(result.model_dump(mode="json")) == result
    (found,) = (c for c in result.capabilities if c.name == "Skills")
    return found, result


def no_path_of(directory: Path, text: str) -> bool:
    """Whether ``text`` names ``directory`` in none of the ways a path may be written."""
    forms = {str(directory), directory.as_posix(), json.dumps(str(directory))[1:-1]}
    return not any(form in text for form in forms)


def test_each_skill_is_listed_with_what_its_frontmatter_and_body_say(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "skills",
        "house-style",
        """
        name: house-style
        description: How the newsroom formats a briefing.
        when_to_use: Before writing the briefing.
        """,
        "# House style\n\nOne verdict, three bullets.",
    )
    write_skill(
        tmp_path / "skills",
        "archive",
        """
        description: Where old briefs are kept.
        disable-model-invocation: true
        """,
    )
    found, result = skills_capability(
        Agent(TestModel(), capabilities=[harness.Skills("skills", workspace=host(tmp_path))])
    )
    assert found.skills == [
        SkillInfo(
            name="archive",
            description="Where old briefs are kept.",
            when_to_use=None,
            model_invocable=False,
            path="skills/archive/SKILL.md",
            body="Do the thing.",
        ),
        SkillInfo(
            name="house-style",
            description="How the newsroom formats a briefing.",
            when_to_use="Before writing the briefing.",
            model_invocable=True,
            path="skills/house-style/SKILL.md",
            body="# House style\n\nOne verdict, three bullets.",
        ),
    ]
    assert (found.capability_id, found.deferred, found.stack) == ("skills", False, None)
    assert found.details == {"directories": ["skills"], "workspace": type(host(tmp_path)).__name__}
    assert result.warnings == []
    assert no_path_of(tmp_path, result.model_dump_json())


def test_without_a_workspace_of_its_own_the_skills_are_read_from_the_agents_workspace(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.")
    found, result = skills_capability(Agent(TestModel(), capabilities=[workspace(tmp_path), harness.Skills("skills")]))
    assert [(s.name, s.path) for s in found.skills] == [("house-style", "skills/house-style/SKILL.md")]
    assert result.warnings == []


def test_without_any_workspace_the_skills_are_only_known_when_the_agent_runs(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.")
    found, result = skills_capability(Agent(TestModel(), capabilities=[harness.Skills("skills")]))
    assert found.skills == []
    assert result.warnings == [
        "the skills of `Skills` are read from the run's workspace, which is only known when it runs"
    ]


def test_a_workspace_not_on_this_machine_is_not_read() -> None:
    capability = harness.Skills("skills", workspace=UnavailableWorkspace("in a sandbox"))
    found, result = skills_capability(Agent(TestModel(), capabilities=[capability]))
    assert found.skills == []
    assert result.warnings == ["`Skills` reads its skills from a UnavailableWorkspace workspace, which is not read"]


def test_a_skill_that_cannot_be_read_is_a_warning_that_names_no_absolute_path(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "good", "description: Fine.")
    broken = write_skill(tmp_path / "skills", "broken", "name: another-name\ndescription: Misnamed.")
    found, result = skills_capability(
        Agent(TestModel(), capabilities=[harness.Skills("skills", workspace=host(tmp_path))])
    )
    assert [s.name for s in found.skills] == ["good"]
    assert result.warnings == ["skill `skills/broken/SKILL.md` was left out: its SKILL.md could not be read"]
    assert broken.is_file()
    assert no_path_of(tmp_path, result.model_dump_json())


def test_an_error_reading_the_skills_is_a_warning_without_the_workspace_path(tmp_path: Path) -> None:
    write_skill(tmp_path / "a", "same", "description: One.")
    write_skill(tmp_path / "b", "same", "description: Another.")
    found, result = skills_capability(
        Agent(TestModel(), capabilities=[harness.Skills(["a", "b"], workspace=host(tmp_path))])
    )
    assert found.skills == []
    (warning,) = result.warnings
    assert warning.startswith("the skills of `Skills` could not be read (ValueError: Duplicate skill name 'same'")
    assert no_path_of(tmp_path, result.model_dump_json())


def test_a_long_body_is_cut_as_every_long_string(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "long", "description: Long.", "x" * (MAX_STRING_CHARS + 10))
    found, _ = skills_capability(Agent(TestModel(), capabilities=[harness.Skills("skills", workspace=host(tmp_path))]))
    (skill,) = found.skills
    assert skill.body.startswith("x" * MAX_STRING_CHARS)
    assert skill.body.endswith(f"[truncated, {MAX_STRING_CHARS + 10} chars total]")


@pytest.mark.anyio
async def test_the_skills_are_read_while_an_event_loop_runs(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.")
    found, result = skills_capability(
        Agent(TestModel(), capabilities=[harness.Skills("skills", workspace=host(tmp_path))])
    )
    assert [s.name for s in found.skills] == ["house-style"]
    assert result.warnings == []


def test_reading_the_skills_changes_nothing_on_disk(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.")

    def listing() -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    before = listing()
    skills_capability(Agent(TestModel(), capabilities=[harness.Skills("skills", workspace=host(tmp_path))]))
    assert listing() == before
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "skills",
        "skills/house-style",
        "skills/house-style/SKILL.md",
    ]


def test_the_skills_chosen_are_listed_sorted_once_each() -> None:
    included, _ = skills_capability(Agent(TestModel(), capabilities=[harness.Skills("sk", include=["y", "x", "y"])]))
    excluded, _ = skills_capability(Agent(TestModel(), capabilities=[harness.Skills("sk", exclude=["y", "x", "y"])]))
    assert included.details == {"directories": ["sk"], "include": ["x", "y"]}
    assert excluded.details == {"directories": ["sk"], "exclude": ["x", "y"]}
