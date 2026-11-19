"""Checks on the analyser's own source: it has no special case for a library or for a project.

The analyser may know the public API of Pydantic AI, pydantic and typing. Anything
else it reads as plain Python. These tests fail when its source names another
library's function (``os.getenv``, ``textwrap.dedent``) or something that only one
of the real-world projects of ``tests/realworld/manifest.toml`` has.
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
    # A name that Pydantic AI uses itself, such as the tool name "duckduckgo_search" of one of its tool
    # factories, is knowledge of Pydantic AI and says nothing about a project.
    library: set[str] = set()
    for package in (pydantic_ai, pydantic_graph):
        for root in package.__path__:
            for path in Path(root).rglob("*.py"):
                library.update(_WORD.findall(path.read_text(encoding="utf-8", errors="replace").lower()))
    return {name for name in names if len(name) >= 4 and name.lower() not in library}


def test_the_analyser_names_nothing_that_belongs_to_one_of_the_pinned_projects() -> None:
    names = _identifiers_of_the_pinned_projects()
    assert {"ottomator-agents", "code_puppy", "marvin", "support_agent", "use_github_agent"} <= names
    offenders: list[str] = []
    for path in sorted(ANALYZER.glob("*.py")):
        words = set(_WORD.findall(path.read_text(encoding="utf-8").lower()))
        offenders.extend(f"{path.name}: {name}" for name in sorted(names) if name.lower() in words)
    assert offenders == []


def test_the_analyser_names_no_library_but_pydantic_ai_pydantic_and_the_language() -> None:
    # The packages whose names the analyser may match. A name of any other one, such as "os.getenv" or
    # "textwrap.dedent", would be a special case for that library.
    allowed = {
        *("pydantic_ai", "pydantic_ai_slim", "pydantic_graph", "pydantic_evals", "pydantic_ai_harness"),
        *("pydantic", "pydantic_core"),
        *("typing", "typing_extensions", "builtins", "types", "collections", "dataclasses", "enum"),
        *("datetime", "uuid", "decimal", "pathlib"),  # types whose JSON schema pydantic defines
        "ast",  # in the analyser's own type hints: ``cast("ast.Name", node)``
    }
    packages = set(sys.stdlib_module_names) | set(importlib.metadata.packages_distributions())
    dotted = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
    named: set[str] = set()
    for path in ANALYZER.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            # A dotted name in a string, compared with what a name resolves to: "os.environ".
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and dotted.fullmatch(node.value.strip("."))
            ):
                named.add(node.value.partition(".")[0])
            # ``target.comes_from("enum", "Enum")``: a package named on its own.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "comes_from":
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    named.add(first.value)
    assert {"builtins", "typing", "pydantic_ai"} <= named  # the check sees what it is meant to see
    assert {name for name in named if name in packages} - allowed == set()
