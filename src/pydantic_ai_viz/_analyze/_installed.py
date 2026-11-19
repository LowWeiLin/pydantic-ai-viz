"""Pydantic AI's own source, as installed, read for the things Pydantic AI itself defines.

A project that calls one of the tool factories of ``pydantic_ai.common_tools`` gets a tool
whose name, description and parameters are written in Pydantic AI, not in the project. That
source is on disk: it is the ``pydantic_ai`` package this tool depends on. Its files are read and
parsed exactly like the project's, by the same analyser, and never imported (the modules of
``pydantic_ai.common_tools`` import optional packages that may not be installed).

Only files of the installed ``pydantic_ai`` package are read, found from where the package is,
and only those that define the name asked for, or re-export it: a name such a file imports
stays an ``External``, as it would in a project file. No other installed package is ever opened.
What is read describes the installed version, which may not be the one the project uses;
``version`` says which it is.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pydantic_ai

from .. import _compat
from ._resolve import Definition, External, Resolver
from ._source import Module, SkippedFile, read_module

PACKAGE = "pydantic_ai"
_MAX_HOPS = 4
"""Through how many re-exports (``from .tools import Tool``) a name of Pydantic AI is followed."""


class InstalledPydanticAI:
    """The source files of the installed ``pydantic_ai`` package, each parsed when first asked for."""

    def __init__(self, *, max_bytes: int) -> None:
        self.roots = [Path(path) for path in pydantic_ai.__path__]
        self.version = _compat.pydantic_ai_version()
        self._max_bytes = max_bytes
        self._modules: dict[str, Module | None] = {}

    def module(self, dotted: str) -> Module | None:
        """The parsed file of the module ``dotted`` of Pydantic AI (``pydantic_ai.common_tools.exa``)."""
        if dotted in self._modules:
            return self._modules[dotted]
        parts = dotted.split(".")
        found: Module | None = None
        if parts[0] == PACKAGE and all(part.isidentifier() for part in parts):
            for root in self.roots:
                found = self._read(root, parts[1:])
                if found is not None:
                    break
        self._modules[dotted] = found
        return found

    def _read(self, root: Path, parts: list[str]) -> Module | None:
        candidates = [root.joinpath(*parts[:-1], f"{parts[-1]}.py")] if parts else []
        candidates.append(root.joinpath(*parts, "__init__.py"))
        for path in candidates:
            if not _plain_file_below(path, root):
                continue
            rel = "/".join([PACKAGE, *path.relative_to(root).parts])
            result = read_module(path, rel, max_bytes=self._max_bytes)
            if isinstance(result, SkippedFile):
                return None
            result.installed = True
            return result
        return None

    def function(self, name: str, resolver: Resolver) -> Definition | None:
        """The ``def`` of the function of Pydantic AI that the dotted ``name`` stands for, if its source has one."""
        for _ in range(_MAX_HOPS):
            dotted, _, attribute = name.rpartition(".")
            module = self.module(dotted)
            if module is None:
                return None
            found = resolver.member(module, attribute)
            if isinstance(found, Definition):
                return None if isinstance(found.node, ast.ClassDef) else found
            if not isinstance(found, External) or found.name == name or not found.name.startswith(f"{PACKAGE}."):
                return None
            name = found.name  # re-exported from another module of the package
        return None


def _plain_file_below(path: Path, root: Path) -> bool:
    """Whether ``path`` is a file that is inside ``root`` once every link on the way is followed."""
    try:
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
