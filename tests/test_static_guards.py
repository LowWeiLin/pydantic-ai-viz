"""Checks on the analyser's own source: it has no special case for a library or for a project.

The analyser may know the public API of Pydantic AI, pydantic and typing. Anything
else it reads as plain Python, with one explicit allow-list of standard-library
features that it evaluates or represents (``ALLOWED_STANDARD_LIBRARY``), and one more
source it may read: the installed ``pydantic_ai`` package, for what Pydantic AI itself
defines. These tests fail when its source names another library's function (``os.path.join``,
``textwrap.indent``), reads another installed package, or names something that only one of
the real-world projects of ``tests/realworld/manifest.toml`` has.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

import pydantic_ai
import pydantic_graph

ANALYZER = Path(__file__).resolve().parent.parent / "src" / "pydantic_ai_viz" / "_analyze"
MANIFEST = Path(__file__).resolve().parent / "realworld" / "manifest.toml"
_WORD = re.compile(r"[\w-]+")
"""A word of source text: what a name of a project is matched against, whole. Hyphens join, as in repo names."""

ALLOWED_PACKAGES = {
    *("pydantic_ai", "pydantic_graph", "pydantic_evals", "pydantic_ai_harness"),
    *("pydantic", "pydantic_core"),
    *("typing", "typing_extensions", "builtins", "types", "collections", "dataclasses", "enum"),
    *("datetime", "uuid", "decimal", "pathlib"),  # types whose JSON schema pydantic defines
    "ast",  # in the analyser's own type hints: ``cast("ast.Name", node)``
}
"""The packages whose names the analyser may match, as a whole."""
ALLOWED_STANDARD_LIBRARY = {
    "os.getenv",  # an environment variable: its name and the source's default, never a value
    "os.environ",  # ``os.environ["X"]``
    "os.environ.get",
    "textwrap.dedent",  # pure functions of the text they are given, evaluated on a literal
    "inspect.cleandoc",
}
"""The only names of other packages the analyser may match: the allow-list of the "how view works" page."""


def _analyser_trees() -> list[tuple[str, ast.Module]]:
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(ANALYZER.glob("*.py"))]


def _identifiers_of_the_pinned_projects() -> set[str]:
    """Names that belong to one of the real-world projects: the repositories and what each entry expects."""
    document = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    names: set[str] = set()
    for entry in document["project"]:
        owner, _, repository = entry["repo"].partition("/")
        names.update({owner, repository})
        expect = entry.get("expect", {})
        for key in ("agents", "tools", "graphs"):
            names.update(expect.get(key, []))
        for edge in expect.get("delegations", []):
            names.update(part.strip() for part in edge.split("->"))
    # A word that Pydantic AI uses itself, such as "agent", is knowledge of Pydantic AI and says nothing about a
    # project. What ``pydantic_ai.common_tools`` defines is not: see the next test.
    library: set[str] = set()
    for package in (pydantic_ai, pydantic_graph):
        for root in package.__path__:
            for path in Path(root).rglob("*.py"):
                library.update(_WORD.findall(path.read_text(encoding="utf-8", errors="replace").lower()))
    return {name for name in names if len(name) >= 4 and name.lower() not in library}


def _analyser_words() -> dict[str, set[str]]:
    return {path.name: set(_WORD.findall(path.read_text(encoding="utf-8").lower())) for path in ANALYZER.glob("*.py")}


def test_the_analyser_names_nothing_that_belongs_to_one_of_the_pinned_projects() -> None:
    names = _identifiers_of_the_pinned_projects()
    assert {"ottomator-agents", "code_puppy", "marvin", "support_agent", "use_github_agent"} <= names
    offenders = [
        f"{file}: {name}"
        for file, words in sorted(_analyser_words().items())
        for name in sorted(names)
        if name in words
    ]
    assert offenders == []


def test_the_analyser_names_none_of_the_tools_of_pydantic_ai_common_tools() -> None:
    # They are read from the installed source of Pydantic AI, as a project's own tools are: never listed.
    factories: set[str] = set()
    tool_names: set[str] = set()
    elsewhere: set[str] = set()
    for root in pydantic_ai.__path__:
        for path in Path(root).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.parent.name != "common_tools":
                elsewhere.update(_WORD.findall(text.lower()))
                continue
            tree = ast.parse(text)
            factories.update(
                node.name.lower()
                for node in tree.body
                if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_")
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "name" and isinstance(node.value, ast.Constant):
                    tool_names.add(str(node.value.value).lower())
    assert {"duckduckgo_search_tool", "tavily_search_tool"} <= factories
    assert {"duckduckgo_search", "tavily_search"} <= tool_names
    # ``web_fetch`` is also a kind of native tool, which Pydantic AI defines outside ``common_tools``.
    names = factories | (tool_names - elsewhere)
    offenders = sorted(f"{file}: {name}" for file, words in _analyser_words().items() for name in names & words)
    assert offenders == []


def _named_in_the_analyser() -> tuple[set[str], set[str]]:
    """The dotted names in its strings (``"os.environ"``), and the packages given to ``comes_from``."""
    dotted = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
    names: set[str] = set()
    packages: set[str] = set()
    for _, tree in _analyser_trees():
        for node in ast.walk(tree):
            # A dotted name in a string, compared with what a name resolves to: "os.environ".
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip(".")
                if dotted.fullmatch(text):
                    names.add(text)
            # ``target.comes_from("enum", "Enum")``: a package named on its own.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "comes_from":
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    packages.add(first.value)
    return names, packages


def test_the_analyser_names_no_library_but_pydantic_ai_pydantic_the_language_and_the_allow_list() -> None:
    packages = set(sys.stdlib_module_names) | set(importlib.metadata.packages_distributions())
    names, given = _named_in_the_analyser()
    roots = {name.partition(".")[0] for name in names}
    assert {"builtins", "typing", "pydantic_ai", "os", "textwrap", "inspect"} <= roots | given  # it sees them
    outside = {name for name in names if name.partition(".")[0] in packages - ALLOWED_PACKAGES}
    assert outside == ALLOWED_STANDARD_LIBRARY  # each of them is used, and nothing else is
    assert {package for package in given if package in packages} - ALLOWED_PACKAGES == set()


def test_the_allow_list_is_how_the_analyser_names_the_standard_library() -> None:
    # The names of the allow-list are those the evaluator matches, all in one place.
    from pydantic_ai_viz._analyze import _values

    assert {*_values.PURE_TEXT_FUNCTIONS, *_values.ENVIRONMENT_GETTERS, _values.ENVIRONMENT} == (
        ALLOWED_STANDARD_LIBRARY
    )


def test_the_only_installed_package_the_analyser_reads_is_pydantic_ai() -> None:
    # Where a package is on disk is asked of ``pydantic_ai`` alone, and nothing else is located or imported.
    located: list[str] = []
    for name, tree in _analyser_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("__path__", "__file__", "__spec__"):
                located.append(f"{name}: {ast.unparse(node)}")
            if isinstance(node, ast.Name | ast.Attribute):
                word = node.id if isinstance(node, ast.Name) else node.attr
                assert word not in {"find_spec", "getsitepackages", "getusersitepackages", "__import__"}, name
    assert located == ["_installed.py: pydantic_ai.__path__"]

    stdlib = set(sys.stdlib_module_names)
    imported: set[str] = set()
    for _, tree in _analyser_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imported.add(node.module)
    # ``pydantic_ai`` itself, never one of its submodules: those of ``pydantic_ai.common_tools`` need extras.
    assert {name for name in imported if name.partition(".")[0] not in stdlib | {"__future__"}} == {"pydantic_ai"}
