"""Command-line targets: ``pkg.module[:attr]`` and ``path/to/file.py[:attr]`` (spec section 4.6).

Resolving a target imports or executes user code to find its agents and graphs,
but never runs an agent. Each target resolves to something ``Session.register``
accepts.
"""

from __future__ import annotations

import contextlib
import importlib
import runpy
import sys
from collections.abc import Generator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_graph import Graph

from ._session import Target

FILE_RUN_NAME = "__pydantic_ai_viz__"
"""``__name__`` given to a file target, so its ``if __name__ == "__main__":`` block does not run."""


class TargetError(Exception):
    """A target that does not name an agent, a graph, or a place that has some."""


def namespace_module(name: str, namespace: Mapping[str, Any]) -> ModuleType:
    """The agents and graphs of ``namespace`` as a module, the form ``Session.register`` labels by variable name."""
    module = ModuleType(name)
    vars(module).update({key: value for key, value in namespace.items() if isinstance(value, Agent | Graph)})
    return module


@contextlib.contextmanager
def script_environment(path: Path, argv: list[str] | None = None) -> Generator[None]:
    """Run a block the way ``python path`` would see the world: its directory importable, ``sys.argv`` its own."""
    saved_path, saved_argv = sys.path[:], sys.argv[:]
    sys.path.insert(0, str(path.resolve().parent))
    if argv is not None:
        sys.argv[:] = argv
    try:
        yield
    finally:
        sys.path[:], sys.argv[:] = saved_path, saved_argv


def _split(spec: str) -> tuple[str, str | None, bool]:
    """``(location, attr, is_file)``. A Windows drive colon is not an attribute separator."""
    if spec.lower().endswith(".py"):
        return spec, None, True
    location, colon, attr = spec.rpartition(":")
    if not colon:
        return spec, None, False
    return location, attr, location.lower().endswith(".py")


def _load_file(location: str) -> dict[str, Any]:
    path = Path(location)
    if not path.is_file():
        raise TargetError(f"file not found: {location}")
    with script_environment(path):
        return runpy.run_path(str(path), run_name=FILE_RUN_NAME)


def _load_module(location: str) -> dict[str, Any]:
    if not location or not all(part.isidentifier() for part in location.split(".")):
        raise TargetError(f"not a module name or a .py file: {location!r}")
    try:
        return vars(importlib.import_module(location))
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if location == missing or location.startswith(missing + "."):
            raise TargetError(
                f"cannot import module {location!r} (is it installed, or run from the wrong directory?)"
            ) from None
        raise  # the module exists; one of its own imports is missing, which is the user's error to see


def resolve_target(spec: str) -> Target:
    """Turn one command-line target into an agent, a graph, or a module of them.

    Raises:
        TargetError: If the file, module or attribute does not exist, or holds no agent or graph.
    """
    location, attr, is_file = _split(spec)
    namespace = _load_file(location) if is_file else _load_module(location)
    if attr is None:
        if not any(isinstance(value, Agent | Graph) for value in namespace.values()):
            raise TargetError(f"no agents or graphs found at the top level of {location}")
        return namespace_module(Path(location).stem if is_file else location, namespace)
    if attr not in namespace:
        raise TargetError(f"{location} has no attribute {attr!r}")
    value = namespace[attr]
    if not isinstance(value, Agent | Graph):
        raise TargetError(f"{spec} is a {type(value).__name__}, not a pydantic_ai.Agent or a pydantic_graph.Graph")
    if getattr(value, "name", None):  # pyright: ignore[reportUnknownArgumentType]
        return cast("Target", value)
    # Unnamed: register it through a one-variable module, so it is labelled with its variable name.
    return namespace_module(attr, {attr: value})


def resolve_targets(specs: list[str]) -> list[Target]:
    """Resolve every target, in order, with the working directory importable as ``python -m`` has it."""
    saved_path = sys.path[:]
    if "" not in sys.path:
        sys.path.insert(0, "")
    try:
        return [resolve_target(spec) for spec in specs]
    finally:
        sys.path[:] = saved_path
