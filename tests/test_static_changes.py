"""Values that are changed after they are created: ``tools.append(f)``, ``settings["k"] = v`` and the like.

A list or a dict that the source builds up in steps is not the literal it started
as. It is read with what was added where that can be followed, and not read at
all where it cannot.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from static_support import agent, analyze_source, one_agent

from pydantic_ai_viz import analyze

HEADER = """
import os
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import Thinking, WebSearch
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.toolsets import FunctionToolset


def first(x: int) -> int:
    return x


def second(x: int) -> int:
    return x


def third(x: int) -> int:
    return x

"""


def source(body: str) -> str:
    return HEADER + textwrap.dedent(body)


def test_a_list_is_read_with_what_is_appended_to_it_before_it_is_used(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        source(
            """
            tools = [first]
            tools.append(second)
            tools.extend([Tool(third, name="renamed")])
            tools.insert(0, first)
            built = Agent("test", tools=tools)
            tools.append(third)  # too late: the agent has been made
            """
        ),
    )
    assert [tool.name for tool in found.tools] == ["first", "second", "renamed", "first"]
    assert found.warnings == []


def test_an_empty_list_that_is_filled_afterwards_is_not_reported_as_empty(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        source(
            """
            toolsets = []
            toolsets.append(FunctionToolset([first], id="mine"))
            toolsets.append(MCPServerStdio("uvx", args=["server"]))
            capabilities = []
            capabilities.append(Thinking())
            built = Agent("test", toolsets=toolsets, capabilities=capabilities)
            """
        ),
    )
    assert [(ts.kind, ts.label) for ts in found.toolsets] == [
        ("function", "FunctionToolset 'mine'"),
        ("mcp", "MCPServerStdio"),
    ]
    assert [tool.name for tool in found.tools] == ["first"]
    assert [capability.name for capability in found.capabilities] == ["Thinking"]
    assert found.warnings == []


def test_what_is_added_under_a_condition_is_shown_and_said_to_depend_on_it(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        source(
            """
            tools = [first]
            if os.getenv("WITH_SECOND"):
                tools.append(second)
            for extra in ():
                tools.extend([third])
            appended = Agent("test", tools=tools)

            either = Agent(
                "test",
                tools=[first, second] if os.getenv("BOTH") else [first],
                capabilities=[WebSearch()] if os.getenv("SEARCH") else None,
            )

            def build(toolsets=None):
                return Agent("test", tools=None or [third], toolsets=toolsets or [])
            """
        ),
    )
    appended = agent(found, "appended")
    assert [tool.name for tool in appended.tools] == ["first", "second", "third"]
    assert appended.warnings == ["some of the tools in `tools` depend on a condition; those of every case are shown"]

    either = agent(found, "either")
    assert [tool.name for tool in either.tools] == ["first", "second", "first"]
    assert [capability.name for capability in either.capabilities] == ["WebSearch"]
    assert either.warnings == [
        'some of the tools in `[first, second] if os.getenv("BOTH") else [first]` depend on a condition; '
        "those of every case are shown",
        'some of the capabilities in `[WebSearch()] if os.getenv("SEARCH") else None` depend on a condition; '
        "those of every case are shown",
    ]

    built = agent(found, "build")
    assert [tool.name for tool in built.tools] == ["third"]
    # One alternative is an argument of the function: nothing can be said about it.
    assert built.warnings == [
        "some of the tools in `None or [third]` depend on a condition; those of every case are shown",
        "the toolsets are built by `toolsets or []`; they are only known when the code runs",
    ]


@pytest.mark.parametrize(
    "change",
    [
        "tools.remove(first)",
        "tools.pop()",
        "tools.clear()",
        "tools.reverse()",
        "tools.sort(key=str)",
        "tools[0] = second",
        "tools[1:] = []",
        "del tools[0]",
        "tools += [second]",
        "tools.extend(other_tools())",
        "tools.append(*more)",
        "def later():\n    tools.append(second)",
        "class Setup:\n    def run(self):\n        tools.clear()",
    ],
)
def test_a_list_changed_in_a_way_that_cannot_be_followed_is_not_read(tmp_path: Path, change: str) -> None:
    found = one_agent(tmp_path, HEADER + f"tools = [first]\n{change}\nbuilt = Agent('test', tools=tools)\n")
    assert found.tools == []
    assert found.warnings == ["the tools are built by `tools`; they are only known when the code runs"]


def test_a_change_to_another_variable_of_the_same_name_does_not_count(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        source(
            """
            tools = [first]


            def unrelated():
                tools = [second]
                tools.clear()
                return tools


            built = Agent("test", tools=tools)
            """
        ),
    )
    assert [tool.name for tool in found.tools] == ["first"]
    assert found.warnings == []


def test_a_list_built_up_inside_a_function_is_read_through_the_call(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        source(
            """
            def make_tools():
                tools = [first]
                tools.append(second)
                return tools


            def make_tools_somehow():
                tools = [first]
                tools.sort()
                return tools


            followed = Agent("test", tools=make_tools())
            opaque = Agent("test", tools=make_tools_somehow())
            """
        ),
    )
    assert [tool.name for tool in agent(found, "followed").tools] == ["first", "second"]
    assert agent(found, "opaque").tools == []
    assert agent(found, "opaque").warnings == [
        "the tools are built by `make_tools_somehow()`; they are only known when the code runs"
    ]


def test_a_list_changed_in_the_file_it_is_imported_from_is_read_as_changed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "registry.py").write_text(
        textwrap.dedent(
            """
            def first(x: int) -> int:
                return x

            def second(x: int) -> int:
                return x

            TOOLS = [first]
            TOOLS.append(second)
            SHUFFLED = [first, second]
            SHUFFLED.reverse()
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "from pydantic_ai import Agent\nfrom registry import TOOLS, SHUFFLED as mixed\n"
        "plain = Agent('test', tools=TOOLS)\nother = Agent('test', tools=mixed)\n",
        encoding="utf-8",
    )
    found = analyze(tmp_path / "app.py")
    assert [tool.name for tool in agent(found, "plain").tools] == ["first", "second"]
    assert agent(found, "other").tools == []
    assert agent(found, "other").warnings == ["the tools are built by `mixed`; they are only known when the code runs"]


def test_a_list_kept_on_self_and_changed_in_a_method_is_not_read(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        source(
            """
            class Fixed:
                def __init__(self):
                    self.tools = [first]
                    self.agent = Agent("test", name="fixed", tools=self.tools)


            class Growing:
                def __init__(self):
                    self.tools = [first]
                    self.agent = Agent("test", name="growing", tools=self.tools)

                def register(self, tool):
                    self.tools.append(tool)
            """
        ),
    )
    assert [tool.name for tool in agent(found, "fixed").tools] == ["first"]
    assert agent(found, "growing").tools == []
    assert agent(found, "growing").warnings == [
        "the tools are built by `self.tools`; they are only known when the code runs"
    ]


def test_settings_changed_after_they_are_written_are_shown_as_an_expression(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        source(
            """
            fixed = {"temperature": 0.2}
            grown = {"temperature": 0.2}
            grown["max_tokens"] = 100
            updated = {"temperature": 0.2}
            updated.update(top_p=0.5)
            budgets = {"tools": 1}
            budgets.setdefault("output", 3)

            a = Agent("test", model_settings=fixed)
            b = Agent("test", model_settings=grown)
            c = Agent("test", model_settings=updated, retries=budgets)
            """
        ),
    )
    assert agent(found, "a").settings.model_settings == {"temperature": 0.2}
    assert agent(found, "a").settings.expressions == {}
    assert agent(found, "b").settings.model_settings is None
    assert agent(found, "b").settings.expressions == {"model_settings": "grown"}
    c = agent(found, "c")
    assert (c.settings.model_settings, c.settings.tool_retries, c.settings.output_retries) == (None, None, None)
    assert c.settings.expressions == {
        "model_settings": "updated",
        "tool_retries": "budgets",
        "output_retries": "budgets",
    }


def test_instructions_listed_and_added_to_are_all_read(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        source(
            """
            parts = ["Be brief."]
            parts.append("Be kind.")
            if os.getenv("STRICT"):
                parts.append("Never guess.")
            built = Agent("test", instructions=parts)
            """
        ),
    )
    assert [instruction.text for instruction in found.instructions] == ["Be brief.", "Be kind.", "Never guess."]
    assert found.warnings == [
        "some of the instructions in `parts` depend on a condition; those of every case are shown"
    ]
