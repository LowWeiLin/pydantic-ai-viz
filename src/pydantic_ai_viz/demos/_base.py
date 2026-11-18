"""The interface every bundled demo implements."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Demo:
    """One offline, deterministic scenario that the CLI can list, serve and record."""

    name: str
    """Short identifier used on the command line, e.g. `support`."""
    title: str
    """Human-readable title, e.g. `Support triage`."""
    description: str
    """One ASCII sentence shown by `pydantic-ai-viz demo --list`."""
    targets: tuple[Any, ...]
    """Agents and graphs to register with a session, root first."""
    run: Callable[[float], Awaitable[object]]
    """`async run(speed)`: play the scenario once and return its final output."""
