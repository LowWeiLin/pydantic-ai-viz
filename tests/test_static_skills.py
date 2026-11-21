"""The skills of a harness ``Skills(...)`` read from source: from the ``SKILL.md`` files of the analysed folder."""

from __future__ import annotations

import textwrap
from importlib.util import find_spec
from pathlib import Path

import pytest
from static_support import live_agents, one_agent

from pydantic_ai_viz import Limits, analyze
from pydantic_ai_viz._analyze._skills import MAX_SKILLS, frontmatter
from pydantic_ai_viz._models import CapabilityInfo, SkillInfo


def write_skill(library: Path, name: str, front: str, body: str = "Do the thing.") -> Path:
    path = library / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{textwrap.dedent(front).strip()}\n---\n\n{body}\n", encoding="utf-8")
    return path


def skills_of(capabilities: list[CapabilityInfo]) -> list[SkillInfo]:
    (found,) = (c for c in capabilities if c.name == "Skills")
    return found.skills


def test_the_skills_of_a_library_given_by_position_are_read_from_the_workspace(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.\nwhen_to_use: Before writing.")
    write_skill(tmp_path / "skills", "archive", "description: Old briefs.")
    (tmp_path / "skills" / "notes").mkdir()  # no SKILL.md: not a skill
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.capabilities import LocalWorkspace
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[LocalWorkspace("."), Skills("skills")])
        """,
    )
    assert skills_of(found.capabilities) == [
        SkillInfo(
            name="archive",
            description="Old briefs.",
            when_to_use=None,
            model_invocable=True,
            path="skills/archive/SKILL.md",
            body="Do the thing.",
        ),
        SkillInfo(
            name="house-style",
            description="How to write.",
            when_to_use="Before writing.",
            model_invocable=True,
            path="skills/house-style/SKILL.md",
            body="Do the thing.",
        ),
    ]
    (capability,) = (c for c in found.capabilities if c.name == "Skills")
    assert (capability.details, capability.capability_id, capability.stack) == ({"directories": ["skills"]}, None, None)
    assert found.warnings == []


def test_several_libraries_are_read_in_order_and_a_selection_applies(tmp_path: Path) -> None:
    for name in ("one", "two", "three"):
        write_skill(tmp_path / "a", name, f"description: {name} from a.")
    write_skill(tmp_path / "b", "four", "description: four from b.")
    write_skill(tmp_path / "b", "one", "description: one again.")
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        everything = Skills(directories=["a", "b"], workspace=LocalWorkspaceBackend("."))
        agent = Agent(
            "test",
            capabilities=[
                everything,
                Skills(["a"], include=["two"], workspace=LocalWorkspaceBackend(".")),
                Skills(["a"], exclude=["two"], workspace=LocalWorkspaceBackend(".")),
            ],
        )
        """,
    )
    listed = [[s.name for s in c.skills] for c in found.capabilities]
    assert listed == [["one", "three", "two", "four"], ["two"], ["one", "three"]]
    assert found.warnings == ["skill `b/one/SKILL.md` was left out: another skill has its name"]


def test_the_frontmatter_is_read_as_agent_skills_write_it(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "skills",
        "quoted",
        """
        name: "quoted"
        description: 'It''s "quoted".'
        when_to_use: "Say \\"when\\"."
        disable-model-invocation: true
        """,
    )
    write_skill(
        tmp_path / "skills",
        "blocks",
        """
        description: >
          Folded onto
          one line.
        when_to_use: |
          Kept on
          two lines.
        metadata:
          owner: desk
        """,
        "\n\n# Body\n\nText.\n\n",
    )
    write_skill(tmp_path / "skills", "folded", "description: A plain value\n  that goes on.\nuser-invocable: false")
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills("skills", workspace=LocalWorkspaceBackend("."))])
        """,
    )
    assert [
        (s.name, s.description, s.when_to_use, s.model_invocable, s.body) for s in skills_of(found.capabilities)
    ] == [
        ("blocks", "Folded onto one line.", "Kept on\ntwo lines.", True, "# Body\n\nText."),
        ("folded", "A plain value that goes on.", None, True, "Do the thing."),
        ("quoted", 'It\'s "quoted".', 'Say "when".', False, "Do the thing."),
    ]
    assert found.warnings == []


@pytest.mark.parametrize(
    "front",
    [
        "name: other\ndescription: The name is not the directory's.",
        "when_to_use: No description.",
        "description: Bad flag.\ndisable-model-invocation: yes",
        "description: One.\ndescription: Twice.",
        "description: Formats briefs. Use when: the user asks for a brief.",  # a mapping inside: not YAML
        "description: [unclosed\n  : : :",
        "description: 'never closed",
        'description: "Quoted" and more.',
        "description: *alias",
        "description:\n  a: mapping",
    ],
)
def test_a_skill_the_harness_would_not_take_is_left_out_with_a_warning(tmp_path: Path, front: str) -> None:
    write_skill(tmp_path / "skills", "broken", front)
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills("skills", workspace=LocalWorkspaceBackend("."))])
        """,
    )
    assert skills_of(found.capabilities) == []
    assert found.warnings == ["skill `skills/broken/SKILL.md` was left out: its SKILL.md could not be read"]


def test_a_comment_after_a_value_is_not_part_of_it() -> None:
    assert frontmatter('---\ndescription: "Checks facts" # why\nwhen_to_use: Often # or not\n---\n') == (
        {"description": "Checks facts", "when_to_use": "Often"},
        "",
    )
    assert frontmatter("---\ndescription: C# and a hash:tag\n---\n") == (
        {"description": "C# and a hash:tag"},
        "",
    )


@pytest.mark.skipif(find_spec("pydantic_ai_harness") is None, reason="needs pydantic-ai-harness")
@pytest.mark.parametrize(
    "front",
    [
        "description: Formats briefs. Use when: the user asks for a brief.",
        'description: "Checks facts" # trailing comment',
        "description: It's #1, a hash:tag\nmetadata:\n  owner: desk",
        "description: [unclosed\n  : : :",
    ],
)
def test_a_frontmatter_is_taken_as_the_harness_takes_it(front: str) -> None:
    from pydantic_ai_harness.skills._loader import parse_skill

    text = f"---\n{front}\n---\nBody."
    try:
        harness: str | None = parse_skill(text, "x/SKILL.md").description  # pyright: ignore[reportUnknownMemberType]
    except ValueError:
        harness = None
    parsed = frontmatter(text)
    assert (None if parsed is None else parsed[0]["description"]) == harness


def test_a_skill_larger_than_the_limit_is_named_as_such(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "huge", "description: Big.", "x" * 1000)
    (tmp_path / "agent.py").write_text(
        "from pydantic_ai import Agent\n"
        "from pydantic_ai.workspaces import LocalWorkspaceBackend\n"
        "from pydantic_ai_harness import Skills\n\n"
        "agent = Agent('test', capabilities=[Skills('skills', workspace=LocalWorkspaceBackend('.'))])\n",
        encoding="utf-8",
    )
    (found,) = analyze(tmp_path, limits=Limits(max_file_bytes=500)).agents
    assert skills_of(found.capabilities) == []
    assert found.warnings == [
        "skill `skills/huge/SKILL.md` is not shown: its SKILL.md is larger than the 500 byte limit of the analysis; "
        "a run still offers it"
    ]


def test_text_without_frontmatter_has_none() -> None:
    assert frontmatter("# Just a heading\n") is None
    assert frontmatter("---\ndescription: never closed\n") is None
    assert frontmatter("---\n  indented: first\n---\n") is None


def test_a_library_that_is_not_there_is_named_in_a_warning(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills("missing", workspace=LocalWorkspaceBackend("."))])
        """,
    )
    assert skills_of(found.capabilities) == []
    assert found.warnings == [
        "skill library `missing` was not found in the analysed files, so its skills are not shown"
    ]


def test_nothing_outside_the_analysed_folder_is_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_skill(tmp_path / "outside", "secret", "description: Not part of the project.")
    found = one_agent(
        project,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent(
            "test",
            capabilities=[Skills(["../outside", "~/skills"], workspace=LocalWorkspaceBackend("."))],
        )
        """,
    )
    assert skills_of(found.capabilities) == []
    assert found.warnings == [
        "skill library `../outside` was not found in the analysed files, so its skills are not shown",
        "skill library `~/skills` was not found in the analysed files, so its skills are not shown",
    ]
    assert "Not part of the project" not in found.model_dump_json()


def test_a_workspace_in_a_folder_of_the_project_is_read_there(tmp_path: Path) -> None:
    write_skill(tmp_path / "sub" / "skills", "house-style", "description: How to write.")
    write_skill(tmp_path / "skills", "decoy", "description: Not in the workspace.")
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills("skills", workspace=LocalWorkspaceBackend("sub"))])
        """,
    )
    assert [(s.name, s.path) for s in skills_of(found.capabilities)] == [
        ("house-style", "sub/skills/house-style/SKILL.md")
    ]
    assert found.warnings == []


def test_a_workspace_decided_when_the_code_runs_is_looked_for_next_to_the_file(tmp_path: Path) -> None:
    write_skill(tmp_path / "skills", "house-style", "description: How to write.")
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai_harness import Skills

        agent = Agent(
            "test",
            capabilities=[Skills("skills", workspace=make_workspace(), scanner=make_scanner())],
        )
        """,
    )
    assert [s.name for s in skills_of(found.capabilities)] == ["house-style"]
    assert found.warnings == [
        "`Skills` reads `skills` from a workspace decided when the code runs; the skills shown are those in `skills`",
        "skills are shown unscanned; a run scans them first",
    ]


def test_computed_directories_are_only_known_when_the_code_runs(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills(skill_folders())])
        """,
    )
    assert skills_of(found.capabilities) == []
    assert found.warnings == [
        "the skill directories are computed by `skill_folders()`; the skills are only known when the code runs"
    ]


def test_a_library_of_many_skills_shows_the_first_ones(tmp_path: Path) -> None:
    for number in range(MAX_SKILLS + 1):
        write_skill(tmp_path / "skills", f"skill-{number:03}", f"description: Number {number}.")
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness import Skills

        agent = Agent("test", capabilities=[Skills("skills", workspace=LocalWorkspaceBackend("."))])
        """,
    )
    assert len(skills_of(found.capabilities)) == MAX_SKILLS
    assert found.warnings == [f"only the first {MAX_SKILLS} skills of `Skills` are shown"]


@pytest.mark.skipif(find_spec("pydantic_ai_harness") is None, reason="needs pydantic-ai-harness")
def test_the_skills_read_from_source_are_those_the_live_agent_reads(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "skills",
        "house-style",
        """
        name: house-style
        description: >
          How the newsroom
          formats a briefing.
        when_to_use: 'Before writing: always.'
        """,
        "# House style\n\n1. A verdict.\n2. Three bullets.",
    )
    write_skill(tmp_path / "skills", "archive", "description: Old briefs.\ndisable-model-invocation: true")
    path = tmp_path / "team.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            from pydantic_ai import Agent
            from pydantic_ai.models.test import TestModel
            from pydantic_ai.workspaces import LocalWorkspaceBackend, WindowsWorkspaceBackend
            from pydantic_ai_harness import Skills

            HERE = Path(__file__).parent
            backend = WindowsWorkspaceBackend(HERE) if sys.platform == "win32" else LocalWorkspaceBackend(HERE)
            writer = Agent(TestModel(), name="writer", capabilities=[Skills("skills", workspace=backend)])
            """
        ),
        encoding="utf-8",
    )
    live = live_agents(path)["writer"]
    (static,) = analyze(path).agents
    assert skills_of(static.capabilities) == skills_of(live.capabilities)
    directories = [c.details["directories"] for a in (static, live) for c in a.capabilities if c.name == "Skills"]
    assert directories == [["skills"], ["skills"]]  # one directory by position is a list, as the harness holds it
    assert [s.name for s in skills_of(static.capabilities)] == ["archive", "house-style"]
