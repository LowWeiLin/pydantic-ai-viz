"""Tools that Pydantic AI itself defines, read from the source of the installed Pydantic AI.

``duckduckgo_search_tool()`` returns a ``Tool`` written in ``pydantic_ai/common_tools/duckduckgo.py``.
That file is read and parsed by the same analyser that reads the project, never imported, and only
files of the installed ``pydantic_ai`` package are read this way.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pydantic_ai
import pytest
from static_support import analyze_source, one_agent, properties, tool

from pydantic_ai_viz._analyze import _installed
from pydantic_ai_viz._analyze._installed import InstalledPydanticAI
from pydantic_ai_viz._analyze._resolve import Project, Resolver
from pydantic_ai_viz._compat import pydantic_ai_version

VERSION = pydantic_ai_version()

COMMON_TOOLS = """
import os
import pydantic_ai.common_tools.exa as exa
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.common_tools.tavily import tavily_search_tool

search = Agent(
    "test",
    tools=[
        duckduckgo_search_tool(),
        tavily_search_tool(os.getenv("TAVILY_API_KEY")),
        exa.exa_search_tool("key", num_results=3),
    ],
)
"""


def test_a_tool_of_common_tools_has_the_name_description_and_parameters_its_source_gives(tmp_path: Path) -> None:
    found = one_agent(tmp_path, COMMON_TOOLS)
    search = tool(found, "duckduckgo_search")
    assert search.description == "Searches DuckDuckGo for the given query and returns the results."
    assert search.parameters_schema == {
        "additionalProperties": False,
        "properties": {"query": {"description": "The query to search for.", "type": "string"}},
        "required": ["query"],
        "type": "object",
    }
    assert (search.is_async, search.takes_ctx, search.origin) == (True, False, "code")
    assert search.function == "pydantic_ai.common_tools.duckduckgo.DuckDuckGoSearchTool.__call__"
    # Where the project uses it, and which installed version it was read from.
    assert search.location is not None
    assert (search.location.file, search.location.line) == ("module.py", 11)
    assert search.library_version == VERSION

    exa = tool(found, "exa_search")
    assert exa.library_version == VERSION
    assert "query" in properties(exa.parameters_schema)


def test_a_tool_whose_function_is_chosen_at_run_time_is_named_and_its_parameters_are_left_open(
    tmp_path: Path,
) -> None:
    # ``tavily_search_tool`` hands Tool a function it may first wrap in ``partial``: which one is decided at run time.
    found = one_agent(tmp_path, COMMON_TOOLS)
    tavily = tool(found, "tavily_search")
    assert tavily.description == "Searches Tavily for the given query and returns the results."
    assert (tavily.parameters_schema, tavily.library_version) == (None, VERSION)
    assert tavily.function == "pydantic_ai.common_tools.tavily.tavily_search_tool"
    assert found.warnings == [
        "the tool `tavily_search` is made by `pydantic_ai.common_tools.tavily.tavily_search_tool`; its name and "
        f"description are read from the installed Pydantic AI {VERSION}, and its parameters are only known when "
        "the code runs"
    ]


def test_reading_the_installed_source_imports_nothing_and_shows_none_of_it(tmp_path: Path) -> None:
    for name in [name for name in sys.modules if name.startswith("pydantic_ai.common_tools")]:
        del sys.modules[name]
    found = analyze_source(tmp_path, COMMON_TOOLS)
    assert not [name for name in sys.modules if name.startswith("pydantic_ai.common_tools.")]
    # Read, but not part of the project: no file of it is listed, and no agent of it is shown.
    assert found.files == ["module.py"]
    assert [blueprint.label for blueprint in found.agents] == ["search"]
    assert found.notices == []


def test_only_files_of_the_installed_pydantic_ai_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[Path] = []
    original = _installed.read_module

    def recording(path: Path, rel: str, *, max_bytes: int):
        read.append(path)
        return original(path, rel, max_bytes=max_bytes)

    monkeypatch.setattr(_installed, "read_module", recording)
    analyze_source(tmp_path, COMMON_TOOLS)
    roots = [Path(root).resolve() for root in pydantic_ai.__path__]
    assert read
    assert all(any(path.resolve().is_relative_to(root) for root in roots) for path in read)
    assert {path.name for path in read} == {"duckduckgo.py", "tavily.py", "exa.py"}


def test_a_function_of_pydantic_ai_that_its_source_does_not_have_is_not_guessed(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.common_tools.nowhere import made_up_tool
        from pydantic_ai.common_tools.duckduckgo import DuckDuckGoSearchTool

        search = Agent("test", tools=[made_up_tool(), DuckDuckGoSearchTool(client)])
        """,
    )
    assert found.tools == []
    assert found.warnings == [
        "a tool is built by `made_up_tool()`; it cannot be described without running the code",
        "a tool is built by `DuckDuckGoSearchTool(client)`; it cannot be described without running the code",
    ]


@pytest.mark.parametrize(
    "dotted",
    ["os", "pydantic", "pydantic_ai..tools", "pydantic_ai.common_tools.missing", "pydantic_ai.tools-x", ""],
)
def test_the_installed_reader_finds_nothing_outside_pydantic_ai(dotted: str) -> None:
    assert InstalledPydanticAI(max_bytes=2_000_000).module(dotted) is None


def test_the_installed_reader_follows_a_re_export_of_pydantic_ai_to_its_definition() -> None:
    installed = InstalledPydanticAI(max_bytes=2_000_000)
    resolver = Resolver(Project(root=Path(".")))
    found = installed.function("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", resolver)
    assert found is not None
    assert found.qualified_name == "pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool"
    # ``pydantic_ai/__init__.py`` has ``from .format_prompt import format_as_xml``.
    exported = installed.function("pydantic_ai.format_as_xml", resolver)
    assert exported is not None
    assert exported.qualified_name == "pydantic_ai.format_prompt.format_as_xml"
    module = installed.module("pydantic_ai.common_tools.duckduckgo")
    assert module is not None
    assert module.installed
    assert module.rel == "pydantic_ai/common_tools/duckduckgo.py"
    # A class is not a function, and what a file imports from another package stays outside.
    assert installed.function("pydantic_ai.common_tools.duckduckgo.DuckDuckGoSearchTool", resolver) is None
    assert installed.function("pydantic_ai.common_tools.duckduckgo.DDGS", resolver) is None
