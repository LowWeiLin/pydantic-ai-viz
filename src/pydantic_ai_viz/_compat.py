"""Guarded access to pydantic-ai and pydantic-graph internals.

Everything that is private or version-sensitive in the libraries we visualise is
named in this module, so one file shows what can break on an upgrade. Access never
raises: a missing piece yields a default plus a human-readable warning that ends up
in the blueprint's ``warnings`` list. ``tests/test_compat.py`` asserts that every
name below still exists in the installed release.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pydantic_ai import Agent

AGENT_PRIVATE_ATTRS: dict[str, str] = {
    "_instructions": "instructions",
    "_system_prompts": "static system prompts",
    "_system_prompt_functions": "dynamic system prompts",
    "_max_tool_retries": "tool retries",
    "_max_output_retries": "output retries",
    "_tool_timeout": "tool timeout",
    "_output_schema": "output mode",
    "_output_toolset": "output tool names",
    "_cap_native_tools": "native tools",
}
"""Private ``Agent`` attributes we read, mapped to what the user loses without them."""

OUTPUT_TOOLSET_TOOL_DEFS = "_tool_defs"
"""Private attribute of the agent's output toolset holding its ``ToolDefinition`` list."""

MERMAID_MODULE = "pydantic_graph.graph_builder"
MERMAID_FUNCTION = "build_mermaid_graph"
"""Importable, but not in ``pydantic_graph.graph_builder.__all__``."""

AGENT_INSTRUMENT_DEFAULT = "_instrument_default"
"""Private ``Agent`` class attribute holding what ``Agent.instrument_all`` was last given."""

BUILTIN_CAPABILITIES = frozenset({"ToolSearch", "PendingMessageDrainCapability", "Instrumentation"})
"""Capabilities pydantic-ai injects on its own; the viewer hides them behind a disclosure."""

OFFLINE_PROVIDERS = frozenset({"test", "function"})
"""Model providers that never touch the network."""

_MISSING: Any = object()


def add_warning(warnings: list[str], message: str) -> None:
    """Append ``message`` unless it is already there."""
    if message not in warnings:
        warnings.append(message)


def get_private(obj: object, attr: str, default: Any, warnings: list[str], label: str | None = None) -> Any:
    """Read ``obj.<attr>``, or return ``default`` and record a warning.

    ``label`` names what is lost; for ``Agent`` attributes it defaults to the entry
    in ``AGENT_PRIVATE_ATTRS``.
    """
    try:
        value = getattr(obj, attr, _MISSING)
    except Exception:
        value = _MISSING
    if value is _MISSING:
        what = label or AGENT_PRIVATE_ATTRS.get(attr) or attr.lstrip("_").replace("_", " ")
        add_warning(warnings, f"{what} unavailable (pydantic-ai internals changed)")
        return default
    return value


def load_build_mermaid_graph() -> Callable[..., Any] | None:
    """Return ``pydantic_graph.graph_builder.build_mermaid_graph`` if it still exists."""
    try:
        function = getattr(import_module(MERMAID_MODULE), MERMAID_FUNCTION, None)
    except Exception:
        return None
    return function if callable(function) else None


def instrumentation_default() -> Any:
    """What ``Agent.instrument_all`` was last called with, or ``None`` if that cannot be read.

    A session reads it to put the previous setting back when it closes.
    """
    return getattr(Agent, AGENT_INSTRUMENT_DEFAULT, None)


def pydantic_ai_version() -> str:
    """Installed pydantic-ai version, or ``"unknown"``."""
    for distribution in ("pydantic-ai-slim", "pydantic-ai"):
        try:
            return version(distribution)
        except PackageNotFoundError:
            continue
    return "unknown"
