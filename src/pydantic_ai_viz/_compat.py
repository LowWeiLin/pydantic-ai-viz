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

CAPABILITY_OWNED_TOOLSET = "CapabilityOwnedToolset"
"""Class name of the private wrapper (``pydantic_ai.toolsets._capability_owned``) that Pydantic AI puts
around a toolset a capability gives the agent."""
CAPABILITY_OWNED_OWNER = "capability"
"""Its attribute holding the capability that gave the toolset."""

HARNESS_STACK_MARKER = "RequireWorkspace"
"""Class name of the private capability (``pydantic_ai_harness._workspace``) that the harness stacks put among the
capabilities they hold. The stack itself does not survive the agent's flattening of its capabilities; this does."""
HARNESS_STACK_OWNER = "owner"
"""Its field naming the stack that put it there."""
HARNESS_STACKS = frozenset({"Coder", "Researcher"})
"""The stacks that put one there: ``Researcher`` only when its overflow store is the workspace's, as by default."""
HARNESS_STACK_MODULES = ("pydantic_ai_harness.coder", "pydantic_ai_harness.researcher")
"""Packages of the stacks, whose private helper capabilities a blueprint leaves out: the stack's entry stands for
them."""
HARNESS_STACK_HELPER_IDS = {"researcher_instructions": "Researcher"}
"""``id`` of a plain ``pydantic_ai.capabilities.Capability`` a stack builds as a helper -> that stack. A blueprint
leaves it out when the stack's marker is there too."""

HARNESS_STACK_MEMBERS: dict[str, tuple[str, ...]] = {
    "Coder": ("FileSystem", "Shell", "RepoContext", "SubAgents", "ClearToolResults", "RepairToolArguments"),
    "Researcher": ("WebSearch", "WebFetch", "SubAgents", "ToolOutputLimits"),
}
"""Classes of the public capabilities each stack puts after its marker, each at most once and in this order. The
first capability after the marker that does not follow it (another class, a class again, or one out of order, as
the agent moves a capability merged with a later one of the same id to that one's place) is one the agent was given
otherwise, and so are those after it."""

HARNESS_SKILLS_MODULE = "pydantic_ai_harness.skills"
"""Package of the harness ``Skills`` capability, whose skills a blueprint lists."""
SKILLS_SOURCES = "_sources"
"""Private field of ``Skills`` holding each configuration it serves; each has a ``workspace`` (``None`` for the run's
workspace)."""

HOOKS_MODULE = "pydantic_ai.capabilities.hooks"
"""Module of ``pydantic_ai.capabilities.Hooks``, whose registered functions a blueprint lists."""
HOOKS_REGISTRY = "_registry"
"""Private field of ``Hooks``: hook point -> the entries registered for it, each with a ``func`` and a ``timeout``,
and a ``tools`` (a ``frozenset`` or ``None``) for a tool hook."""
HOOK_KWARGS: dict[str, str] = {
    "before_run": "before_run",
    "after_run": "after_run",
    "run": "wrap_run",
    "run_error": "on_run_error",
    "before_node_run": "before_node_run",
    "after_node_run": "after_node_run",
    "node_run": "wrap_node_run",
    "node_run_error": "on_node_run_error",
    "run_event_stream": "wrap_run_event_stream",
    "event": "on_event",
    "before_model_request": "before_model_request",
    "prepare_model_request": "prepare_model_request",
    "after_model_request": "after_model_request",
    "model_request": "wrap_model_request",
    "model_request_error": "on_model_request_error",
    "prepare_tools": "prepare_tools",
    "prepare_output_tools": "prepare_output_tools",
    "before_tool_validate": "before_tool_validate",
    "after_tool_validate": "after_tool_validate",
    "tool_validate": "wrap_tool_validate",
    "tool_validate_error": "on_tool_validate_error",
    "before_tool_execute": "before_tool_execute",
    "after_tool_execute": "after_tool_execute",
    "tool_execute": "wrap_tool_execute",
    "tool_execute_error": "on_tool_execute_error",
    "before_output_validate": "before_output_validate",
    "after_output_validate": "after_output_validate",
    "output_validate": "wrap_output_validate",
    "output_validate_error": "on_output_validate_error",
    "before_output_process": "before_output_process",
    "after_output_process": "after_output_process",
    "output_process": "wrap_output_process",
    "output_process_error": "on_output_process_error",
    "deferred_tool_calls": "handle_deferred_tool_calls",
}
"""Keyword arguments of ``Hooks(...)`` -> the hook point each registers its function for, in the order ``Hooks``
registers them. The decorators of ``hooks.on`` have the same names and register for the same points."""

SUB_AGENTS_TOOL_NAME = "delegate_task"
"""The default ``tool_name`` of the harness ``SubAgents``: the tool the model delegates a task with."""

CAPABILITY_INSTRUCTIONS = "_instructions"
"""Private field of ``pydantic_ai.capabilities.Capability`` holding what ``instructions=`` gave it, as
``SourcedInstruction``s with an ``instruction`` each."""

LOCAL_STRATEGY_METHOD = "_resolve_local_strategy"
"""Method of ``WebSearch`` and ``WebFetch`` that makes the tool ``local=True`` stands for. Static analysis reads
it from the installed source to learn which tool that is."""

DEFERRED_NATIVE_TOOL_ARG = "native_tool"
"""Keyword default of the function ``CombinedCapability.get_native_tools`` puts in place of the native tool of a
capability loaded on demand: it holds that native tool."""

MERMAID_MODULE = "pydantic_graph.graph_builder"
MERMAID_FUNCTION = "build_mermaid_graph"
"""Importable, but not in ``pydantic_graph.graph_builder.__all__``."""

AGENT_INSTRUMENT_DEFAULT = "_instrument_default"
"""Private ``Agent`` class attribute holding what ``Agent.instrument_all`` was last given."""

UNNAMED_AGENT_SPAN_NAME = "agent"
"""What Pydantic AI puts in ``gen_ai.agent.name`` for an agent that has no name."""

BUILTIN_CAPABILITIES = frozenset({"ToolSearch", "PendingMessageDrainCapability", "Instrumentation"})
"""Capabilities pydantic-ai injects on its own; the viewer hides them behind a disclosure."""

OFFLINE_PROVIDERS = frozenset({"test", "function"})
"""Model providers that never touch the network."""


def _event_classes() -> tuple[type, ...]:
    try:
        from pydantic_ai import CapabilityEvent, CustomEvent
    except ImportError:
        return ()
    return (CapabilityEvent, CustomEvent)


EVENT_CLASSES: tuple[type, ...] = _event_classes()
"""``CapabilityEvent`` and ``CustomEvent``, which ``CaptureEvents`` records; ``()`` on a release without them."""

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
