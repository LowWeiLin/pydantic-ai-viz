"""Instructions and system prompts that are templates: shown whole, the parts filled in at run time marked.

A template's text is written in the syntax of ``str.format``: a placeholder in braces for each part
the running code fills in, a literal brace doubled, and every placeholder whose value the source
gives already filled in. It is shown as it is, so it is not a warning.
"""

from __future__ import annotations

import string
from pathlib import Path

from static_support import analyze_source, one_agent


def _texts(tmp_path: Path, source: str) -> list[tuple[str, bool, str | None]]:
    found = one_agent(tmp_path, source)
    assert found.warnings == []
    return [(i.kind, i.dynamic, i.text) for i in found.instructions]


def test_an_f_string_with_parts_known_at_run_time_is_its_whole_template(tmp_path: Path) -> None:
    long_text = "You are a careful research assistant. " * 20
    texts = _texts(
        tmp_path,
        f'''
        from datetime import date
        from pydantic_ai import Agent

        ROLE = "editor"

        agent = Agent(
            "test",
            instructions=f"{long_text}Today is {{date.today()}}. You are the {{ROLE}} of {{user.name!r}}.",
            system_prompt=[f"""Reply as JSON: {{{{"answer": ...}}}}.
        Use {{count:>4}} items, {{ {{"a": x}}["a"]}} at most."""],
        )
        ''',
    )
    assert texts == [
        # All of it, not a copy cut to fit a sentence. ROLE is a constant: it is filled in.
        ("instructions", True, f"{long_text}Today is {{date.today()}}. You are the editor of {{user.name!r}}."),
        # A literal brace stays doubled; an expression that starts with a brace is set off by a space.
        (
            "system_prompt",
            True,
            "Reply as JSON: {{\"answer\": ...}}.\nUse {count:>4} items, { {'a': x}['a']} at most.",
        ),
    ]


def test_a_format_call_is_its_template_with_what_the_source_gives_filled_in(tmp_path: Path) -> None:
    texts = _texts(
        tmp_path,
        """
        from pydantic_ai import Agent

        PROMPT = "You are {role} for {team}. Today is {date:%A}; {{braces}} stay; {0}."

        agent = Agent(
            "test",
            instructions=[
                PROMPT.format("first", role="a pirate", team=load_team(), date=today()),
                "Ask {name}.".format(**context),
            ],
        )
        """,
    )
    assert texts == [
        # ``role`` and ``0`` are given; ``team`` is computed; ``date`` has a format spec, applied at run time.
        ("instructions", True, "You are a pirate for {team}. Today is {date:%A}; {{braces}} stay; first."),
        # ``**context``: which placeholder gets what is decided at run time.
        ("instructions", True, "Ask {name}."),
    ]


def test_a_template_text_is_read_back_by_str_format_as_the_text_it_shows(tmp_path: Path) -> None:
    # What is not a placeholder is literal text, braces and all: str.format gives it back unchanged.
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent

        agent = Agent("test", instructions=f"Return {{json}} for {user}.")
        """,
    )
    (instruction,) = found.instructions
    assert instruction.text == "Return {{json}} for {user}."
    assert instruction.text.format(user="{user}") == "Return {json} for {user}."
    fields = [field for _, field, _, _ in string.Formatter().parse(instruction.text) if field is not None]
    assert fields == ["user"]


def test_a_template_without_placeholders_left_is_a_plain_text(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        NAME = "Ada"
        agent = Agent("test", instructions=[f"Greet {NAME}.", "Greet {}.".format(NAME)])
        """,
    )
    (blueprint,) = found.agents
    assert [(i.dynamic, i.text) for i in blueprint.instructions] == [(False, "Greet Ada."), (False, "Greet Ada.")]
