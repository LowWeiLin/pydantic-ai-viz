"""Turning a target given by the user into source files to analyse.

A target is a path to a ``.py`` file, optionally followed by ``:name`` to pick
one agent or graph in it. Locating a target only looks at the file system; it
never imports anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class TargetError(ValueError):
    """A target that does not name something that can be shown."""


@dataclass(frozen=True, slots=True)
class Source:
    """One file to analyse, and the name selected in it, if any."""

    path: Path
    select: str | None
    spec: str
    """The target as the user wrote it, for messages."""


def split_target(spec: str) -> tuple[str, str | None]:
    """``(location, name)``. A Windows drive colon is not a name separator."""
    if spec.lower().endswith(".py"):
        return spec, None
    location, colon, name = spec.rpartition(":")
    if colon and location.lower().endswith(".py"):
        return location, name
    return spec, None


def locate(target: str | os.PathLike[str]) -> Source:
    """Find the file a target names.

    Raises:
        TargetError: If the target is not an existing ``.py`` file.
    """
    spec = os.fspath(target)
    location, select = split_target(spec)
    path = Path(location)
    if select is not None and not select.isidentifier():
        raise TargetError(f"not a name: {select!r} in {spec}")
    if path.is_dir():
        raise TargetError(f"{location} is a directory; pass the path of a .py file")
    if not location.lower().endswith(".py"):
        raise TargetError(
            f"cannot read {spec!r} without importing it: pass the path of a .py file, "
            "or use --import to import a module by name (this runs its code)"
        )
    if not path.is_file():
        raise TargetError(f"file not found: {location}")
    return Source(path=path.resolve(), select=select, spec=spec)
