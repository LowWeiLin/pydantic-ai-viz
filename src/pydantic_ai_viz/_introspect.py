"""Introspection of live objects: ``Agent`` -> ``AgentBlueprint``, ``Graph`` -> ``GraphBlueprint``.

The blueprints made here have provenance ``"object"``. Reading source code without
importing it is the job of ``_analyze``, whose blueprints have provenance ``"code"``.

Nothing here runs a model, resolves a dynamic prompt, builds a ``RunContext`` or
enters a toolset, so it is safe for agents built with ``defer_model_check=True``
and for agents whose tools have side effects.

Identity is the caller's job. A blueprint ``id`` is opaque and only has to be
stable within one session, so the functions take ids as arguments:

* ``blueprint()`` (public, pure) numbers the root ``a1`` and its delegates ``a2``,
  ``a3``, ... in discovery order;
* a ``Session`` keeps its own counters, walks ``agent_closure`` and passes a
  ``delegate_id`` callback that maps an agent object to the id it allocated.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import inspect
import re
import types
from collections.abc import Callable
from enum import Enum
from pathlib import PurePath
from typing import Annotated, Any, Literal, TypeAlias, Union, cast, get_args, get_origin
from warnings import catch_warnings, simplefilter

from pydantic_ai import Agent, NativeOutput, PromptedOutput, TextOutput, ToolOutput
from pydantic_ai.agent import WrapperAgent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.toolsets import (
    CombinedToolset,
    ExternalToolset,
    FunctionToolset,
    PrefixedToolset,
    RenamedToolset,
    WrapperToolset,
)
from pydantic_core import to_jsonable_python
from pydantic_graph import Decision, EndNode, Fork, Graph, Join, StartNode, Step

from . import _compat
from ._capabilities import category_of, is_secret, package_of, without_credentials
from ._events import CaptureEvents
from ._models import (
    AgentBlueprint,
    AgentSettings,
    CapabilityCategory,
    CapabilityInfo,
    Delegate,
    GraphBlueprint,
    GraphEdge,
    GraphNode,
    HookInfo,
    Instruction,
    ModelInfo,
    OutputInfo,
    Tool,
    Toolset,
)
from ._normalize import clip
from ._skills import is_skills, skill_catalog

AnyAgent: TypeAlias = "Agent[Any, Any]"
AnyWrapper: TypeAlias = "WrapperAgent[Any, Any]"
AnyGraph: TypeAlias = "Graph[Any, Any, Any, Any]"
ToolKind: TypeAlias = Literal["function", "output", "external", "unapproved"]
ToolsetKind: TypeAlias = Literal["agent", "function", "mcp", "dynamic", "other"]

AGENT_TOOLSET_ID = "<agent>"
_TOOL_KINDS: frozenset[str] = frozenset({"function", "output", "external", "unapproved"})
_UNQUALIFIED_MODULES = frozenset({"builtins", "typing", "types", "collections.abc"})

# ------------------------------------------------------------------- naming


def qualified_name(obj: object) -> str | None:
    """``module.qualname`` of a function or class; the type's name for other callables."""
    if isinstance(obj, functools.partial):
        return qualified_name(cast("functools.partial[Any]", obj).func)
    name = getattr(obj, "__qualname__", None)
    if not isinstance(name, str):
        obj = type(obj)
        name = obj.__qualname__
    module = getattr(obj, "__module__", None)
    if not isinstance(module, str) or module == "builtins":
        return name
    return f"{module}.{name}"


def type_label(tp: Any, *, qualified: bool) -> str:
    """Readable name of a type expression: ``str``, ``A | B``, ``list[pkg.A]``."""
    if tp is None or tp is type(None):
        return "None"
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Union or origin is types.UnionType:
        return " | ".join(type_label(arg, qualified=qualified) for arg in args)
    if origin is Annotated:
        return type_label(args[0], qualified=qualified)
    if origin is Literal:
        return f"Literal[{', '.join(repr(arg) for arg in args)}]"
    if origin is not None:
        head = type_label(origin, qualified=qualified)
        return f"{head}[{', '.join(type_label(arg, qualified=qualified) for arg in args)}]" if args else head
    name = getattr(tp, "__qualname__" if qualified else "__name__", None)
    if not isinstance(name, str):
        return repr(tp)
    module = getattr(tp, "__module__", None)
    if qualified and isinstance(module, str) and module not in _UNQUALIFIED_MODULES:
        return f"{module}.{name}"
    return name


def _optional_type_label(tp: Any) -> str | None:
    """Qualified type name, or ``None`` for "no type" (``object``, ``None``, ``NoneType``)."""
    if tp is None or tp is object or tp is type(None):
        return None
    try:
        return type_label(tp, qualified=True)
    except Exception:
        return repr(tp)


def _ordinal(blueprint_id: str) -> str:
    match = re.search(r"(\d+)$", blueprint_id)
    return match.group(1) if match else blueprint_id


def _json(value: Any) -> Any:
    return to_jsonable_python(value, fallback=repr)


# ------------------------------------------------------------------- agents


class _ToolsetWalk:
    """Flattens an agent's toolset tree into wire toolsets and tools."""

    def __init__(self, warnings: list[str]) -> None:
        self.warnings = warnings
        self.toolsets: list[Toolset] = []
        self.tools: list[Tool] = []
        self.functions: list[tuple[str, Any]] = []
        """``(tool name, python function)`` for the delegation heuristic."""
        self.owned: dict[int, list[str]] = {}
        """``id`` of a capability object -> the ids of the toolsets it gives the agent."""

    def visit(self, toolset: Any, wrappers: tuple[str, ...] = (), rename: Callable[[str], str] | None = None) -> None:
        try:
            self._visit(toolset, wrappers, rename or (lambda name: name))
        except Exception as exc:
            _compat.add_warning(
                self.warnings,
                f"toolset {type(toolset).__name__} could not be inspected ({type(exc).__name__}: {exc})",
            )
            self._add_toolset(toolset, "other", wrappers, opaque=True)

    def _visit(self, toolset: Any, wrappers: tuple[str, ...], rename: Callable[[str], str]) -> None:
        if isinstance(toolset, CombinedToolset):
            for child in cast("CombinedToolset[Any]", toolset).toolsets:
                self.visit(child, wrappers, rename)
        elif isinstance(toolset, WrapperToolset):
            wrapper = cast("WrapperToolset[Any]", toolset)
            start = len(self.toolsets)
            self.visit(wrapper.wrapped, (*wrappers, type(wrapper).__name__), _compose(rename, _renamer(wrapper)))
            if type(wrapper).__name__ == _compat.CAPABILITY_OWNED_TOOLSET:
                owner = _safe_getattr(wrapper, _compat.CAPABILITY_OWNED_OWNER)
                if owner is not None:
                    self.owned.setdefault(id(owner), []).extend(t.id for t in self.toolsets[start:])
        elif isinstance(toolset, FunctionToolset):
            self._function_toolset(cast("FunctionToolset[Any]", toolset), wrappers, rename)
        elif isinstance(toolset, ExternalToolset):
            external = cast("ExternalToolset[Any]", toolset)
            entry = self._add_toolset(external, "other", wrappers, opaque=False)
            for tool_def in external.tool_defs:
                self.tools.append(_tool(tool_def, entry.id, rename, kind="external"))
        elif _is_mcp(toolset):
            self._add_toolset(toolset, "mcp", wrappers, opaque=True)
        elif type(toolset).__name__ == "DynamicToolset":
            self._add_toolset(toolset, "dynamic", wrappers, opaque=True)
        else:
            self._add_toolset(toolset, "other", wrappers, opaque=True)

    def _function_toolset(
        self, toolset: FunctionToolset[Any], wrappers: tuple[str, ...], rename: Callable[[str], str]
    ) -> None:
        is_agent = toolset.id == AGENT_TOOLSET_ID
        tools = toolset.tools
        if is_agent and not tools:
            return  # every agent has this toolset; an empty one is noise on the canvas
        entry = self._add_toolset(toolset, "agent" if is_agent else "function", wrappers, opaque=False)
        for name, tool in tools.items():
            function_schema: Any = getattr(tool, "function_schema", None)
            is_async: Any = getattr(function_schema, "is_async", None)
            self.tools.append(
                _tool(
                    tool.tool_def,
                    entry.id,
                    rename,
                    name=name,
                    takes_ctx=tool.takes_ctx,
                    is_async=is_async if isinstance(is_async, bool) else None,
                    max_retries=tool.max_retries,
                    function=qualified_name(tool.function),
                )
            )
            self.functions.append((rename(name), tool.function))

    def _add_toolset(self, toolset: Any, kind: ToolsetKind, wrappers: tuple[str, ...], *, opaque: bool) -> Toolset:
        toolset_id: Any = _safe_getattr(toolset, "id")
        label: Any = "Agent tools" if kind == "agent" else _safe_getattr(toolset, "label")
        entry = Toolset(
            id=f"ts{len(self.toolsets)}",
            toolset_id=toolset_id if isinstance(toolset_id, str) else None,
            label=label if isinstance(label, str) and label else type(toolset).__name__,
            kind=kind,
            wrappers=list(wrappers),
            opaque=opaque,
            expression=None,
            location=None,
        )
        self.toolsets.append(entry)
        return entry


def _safe_getattr(obj: object, attr: str) -> Any:
    try:
        return getattr(obj, attr, None)
    except Exception:
        return None


def _compose(outer: Callable[[str], str], inner: Callable[[str], str] | None) -> Callable[[str], str]:
    if inner is None:
        return outer
    return lambda name: outer(inner(name))


def _renamer(wrapper: WrapperToolset[Any]) -> Callable[[str], str] | None:
    """How a wrapper changes the tool names of the toolset it wraps, if it does."""
    if isinstance(wrapper, PrefixedToolset):
        prefix = wrapper.prefix
        return lambda name: f"{prefix}_{name}"
    if isinstance(wrapper, RenamedToolset):
        new_names = {old: new for new, old in wrapper.name_map.items()}
        return lambda name: new_names.get(name, name)
    return None


def _is_mcp(toolset: object) -> bool:
    cls = type(toolset)
    return cls.__module__.startswith("pydantic_ai.mcp") or "MCP" in cls.__name__


def _tool(
    tool_def: Any,
    toolset: str,
    rename: Callable[[str], str],
    *,
    name: str | None = None,
    kind: ToolKind | None = None,
    takes_ctx: bool | None = None,
    is_async: bool | None = None,
    max_retries: int | None = None,
    function: str | None = None,
) -> Tool:
    raw_kind: Any = getattr(tool_def, "kind", None)
    resolved: ToolKind = kind or (cast("ToolKind", raw_kind) if raw_kind in _TOOL_KINDS else "function")
    return Tool(
        name=rename(name if name is not None else tool_def.name),
        toolset=toolset,
        description=tool_def.description,
        parameters_schema=_json(tool_def.parameters_json_schema),
        return_schema=_json(getattr(tool_def, "return_schema", None)),
        kind=resolved,
        takes_ctx=takes_ctx,
        is_async=is_async,
        max_retries=max_retries,
        function=function,
        origin="object",
        location=None,
        library_version=None,
    )


def _walk_toolsets(agent: AnyAgent, warnings: list[str]) -> _ToolsetWalk:
    walk = _ToolsetWalk(warnings)
    try:
        toolsets: list[Any] = list(agent.toolsets)
    except Exception as exc:
        _compat.add_warning(warnings, f"toolsets unavailable ({type(exc).__name__}: {exc})")
        return walk
    for toolset in toolsets:
        walk.visit(toolset)
    return walk


def _referenced_agents(function: Any) -> list[AnyAgent]:
    """Agents a tool function refers to through its globals or its closure.

    A heuristic: it misses agents reached through deps, attributes or helpers.
    Run-time span parentage is the authoritative source and upgrades these edges.
    """
    try:
        return _scan_for_agents(function)
    except Exception:
        return []  # exotic callables are simply not scanned


def _scan_for_agents(function: Any) -> list[AnyAgent]:
    function = inspect.unwrap(function)
    function = getattr(function, "__func__", function)  # bound method -> function
    candidates: list[Any] = []
    try:
        closure = inspect.getclosurevars(function)
        candidates.extend(closure.nonlocals.values())
        candidates.extend(closure.globals.values())
    except (TypeError, ValueError):
        # Not a plain function, or a closure cell that is not filled in yet.
        code: Any = getattr(function, "__code__", None)
        namespace: Any = getattr(function, "__globals__", None)
        if code is not None and isinstance(namespace, dict):
            scope = cast("dict[str, Any]", namespace)
            candidates.extend(scope[name] for name in code.co_names if name in scope)
    found: list[AnyAgent] = []
    for candidate in candidates:
        if isinstance(candidate, Agent) and not any(candidate is seen for seen in found):
            found.append(cast("AnyAgent", candidate))
    return found


def _delegates_of(walk: _ToolsetWalk) -> list[tuple[str, AnyAgent]]:
    pairs: list[tuple[str, AnyAgent]] = []
    for tool_name, function in walk.functions:
        pairs.extend((tool_name, agent) for agent in _referenced_agents(function))
    return pairs


def _capability_delegates(agent: AnyAgent) -> list[tuple[str, AnyAgent]]:
    """``(tool name, sub-agent)`` for each agent a harness ``SubAgents`` capability of ``agent`` delegates to."""
    try:
        capabilities: list[Any] = list(agent.root_capability.capabilities)
    except Exception:
        return []
    pairs: list[tuple[str, AnyAgent]] = []
    for capability in capabilities:
        cls = type(cast("object", capability))
        if cls.__name__ != "SubAgents" or not cls.__module__.startswith("pydantic_ai_harness.sub_agents"):
            continue
        tool_name: Any = _safe_getattr(capability, "tool_name")
        held: Any = _safe_getattr(capability, "agents")
        for sub in cast("list[Any]", held) if isinstance(held, list | tuple) else []:
            target: Any = _safe_getattr(sub, "agent")
            if isinstance(target, Agent):
                name = tool_name if isinstance(tool_name, str) else _compat.SUB_AGENTS_TOOL_NAME
                pairs.append((name, cast("AnyAgent", target)))
    return pairs


_DURABLE_MODULE = "pydantic_ai.durable_exec."
_MAX_WRAPPED = 4


def durable_wrapper(value: object) -> tuple[AnyAgent, str] | None:
    """The agent inside a durable-execution wrapper (``TemporalAgent`` and the like) and the wrapper's class name.

    Found by name and module, so the optional ``temporalio``, ``dbos`` and ``prefect`` are never imported.
    """
    name: str | None = None
    current: object = value
    for _ in range(_MAX_WRAPPED):
        if not isinstance(current, WrapperAgent):
            break
        wrapper = cast("AnyWrapper", current)
        if name is None and type(wrapper).__module__.startswith(_DURABLE_MODULE):
            name = type(wrapper).__name__
        current = cast("object", wrapper.wrapped)
    if name is not None and isinstance(current, Agent):
        return cast("AnyAgent", current), name
    return None


def find_delegates(agent: AnyAgent) -> list[tuple[str, AnyAgent]]:
    """``(tool name, sub-agent)`` pairs: those the delegation heuristic finds, then the agents ``SubAgents`` holds."""
    return [*_delegates_of(_walk_toolsets(agent, [])), *_capability_delegates(agent)]


def agent_closure(root: AnyAgent) -> list[AnyAgent]:
    """``root`` followed by every agent reachable through static delegation.

    Breadth-first, in discovery order, each agent once (cycle-safe).
    """
    ordered: list[AnyAgent] = [root]
    index = 0
    while index < len(ordered):
        for _, delegate in find_delegates(ordered[index]):
            if not any(delegate is seen for seen in ordered):
                ordered.append(delegate)
        index += 1
    return ordered


def _model_info(agent: AnyAgent) -> ModelInfo | None:
    model: Any = agent.model
    if model is None:
        return None
    if isinstance(model, str):
        provider, separator, name = model.partition(":")
        if not separator:
            provider, name = None, model
        return ModelInfo(
            id=model,
            name=name,
            provider=provider,
            offline=provider in _compat.OFFLINE_PROVIDERS,
            expression=None,
            model_class=None,
            env=None,
        )
    name_attr: Any = _safe_getattr(model, "model_name")
    provider_attr: Any = _safe_getattr(model, "system")
    id_attr: Any = _safe_getattr(model, "model_id")
    provider = provider_attr if isinstance(provider_attr, str) else None
    return ModelInfo(
        id=id_attr if isinstance(id_attr, str) else None,
        name=name_attr if isinstance(name_attr, str) else type(model).__name__,
        provider=provider,
        offline=provider in _compat.OFFLINE_PROVIDERS,
        expression=None,
        model_class=type(model).__name__,
        env=None,
    )


def _prompt(kind: Literal["instructions", "system_prompt"], value: Any) -> Instruction:
    if isinstance(value, str):
        return Instruction(kind=kind, dynamic=False, text=value, function=None, expression=None, location=None)
    # A function, or a template that is rendered against the run's deps.
    return Instruction(
        kind=kind, dynamic=True, text=None, function=qualified_name(value), expression=None, location=None
    )


def _instructions(agent: AnyAgent, warnings: list[str]) -> list[Instruction]:
    found: list[Instruction] = []
    for item in _compat.get_private(agent, "_instructions", [], warnings):
        found.append(_prompt("instructions", getattr(item, "instruction", item)))
    for text in _compat.get_private(agent, "_system_prompts", (), warnings):
        found.append(_prompt("system_prompt", text))
    for runner in _compat.get_private(agent, "_system_prompt_functions", [], warnings):
        found.append(_prompt("system_prompt", getattr(runner, "function", runner)))
    return found


def _output_label(spec: Any) -> str:
    if isinstance(spec, list | tuple):
        return " | ".join(_output_label(item) for item in cast("list[Any]", spec))
    if isinstance(spec, ToolOutput):
        return _output_label(cast("ToolOutput[Any]", spec).output)
    if isinstance(spec, NativeOutput | PromptedOutput):
        return _output_label(cast("NativeOutput[Any]", spec).outputs)
    if isinstance(spec, TextOutput):
        return _output_label(cast("TextOutput[Any]", spec).output_function)
    if inspect.isroutine(spec):
        return str(getattr(spec, "__name__", repr(spec)))
    return type_label(spec, qualified=False)


def _output(agent: AnyAgent, warnings: list[str]) -> OutputInfo:
    json_schema: Any = None
    try:
        json_schema = _json(agent.output_json_schema())
    except Exception as exc:
        _compat.add_warning(warnings, f"output schema unavailable ({type(exc).__name__}: {exc})")

    type_name: str | None = None
    try:
        type_name = _output_label(agent.output_type)
    except Exception as exc:
        _compat.add_warning(warnings, f"output type unavailable ({type(exc).__name__}: {exc})")

    schema: Any = _compat.get_private(agent, "_output_schema", None, warnings)
    mode: Any = _safe_getattr(schema, "mode")
    allows_text: Any = _safe_getattr(schema, "allows_text")
    if not isinstance(mode, str):
        # Public fallback; observed runs fill in the rest (derivation rule 2).
        is_text = json_schema == {"type": "string"}
        mode, allows_text = ("text", True) if is_text else ("unknown", None)

    tool_names: list[str] = []
    toolset: Any = _compat.get_private(agent, "_output_toolset", None, warnings)
    if toolset is not None:
        tool_defs: Any = _compat.get_private(
            toolset, _compat.OUTPUT_TOOLSET_TOOL_DEFS, [], warnings, "output tool names"
        )
        tool_names = [str(tool_def.name) for tool_def in tool_defs]

    validators: Any = _compat.get_private(agent, "_output_validators", [], warnings)
    return OutputInfo(
        mode=mode,
        allows_text=allows_text if isinstance(allows_text, bool) else None,
        type_name=type_name,
        json_schema=json_schema,
        tool_names=tool_names,
        validators=[name for v in validators if (name := qualified_name(_safe_getattr(v, "function"))) is not None],
        location=None,
    )


def _capabilities(agent: AnyAgent, walk: _ToolsetWalk, warnings: list[str]) -> list[CapabilityInfo]:
    try:
        capabilities: list[Any] = list(agent.root_capability.capabilities)
    except Exception as exc:
        _compat.add_warning(warnings, f"capabilities unavailable ({type(exc).__name__}: {exc})")
        return []
    found: list[CapabilityInfo] = []
    stacks = {_harness_stack(capability) for capability in capabilities}
    within: str | None = None  # the stack whose marker the capabilities since then follow, each one it puts there
    left: tuple[str, ...] = ()  # the member classes it may still put there, in its order
    entry: CapabilityInfo | None = None  # the entry of the last stack, which stands for its helpers
    for capability in capabilities:
        cls = type(cast("object", capability))
        if (cls.__name__.startswith("_") and cls.__module__.startswith(_compat.HARNESS_STACK_MODULES)) or (
            _stack_helper_of(capability) in stacks - {None}  # the same, built as a plain Capability
        ):
            # A helper of a harness stack, which the stack's entry stands for, with the toolsets it gives.
            if entry is not None:
                entry.toolsets.extend(t for t in _owned_toolsets(capability, walk, 0) if t not in entry.toolsets)
            continue
        stack = _harness_stack(capability)
        if stack is not None:
            within, left = stack, _compat.HARNESS_STACK_MEMBERS.get(stack, ())
            entry = CapabilityInfo(
                name=stack,
                builtin=False,
                category="harness",
                package="pydantic_ai_harness",
                deferred=False,
                details={},
                expressions={},
                toolsets=[],
                native_tools=[],
                capability_id=None,
                stack=None,
                skills=[],
                hooks=[],
            )
            found.append(entry)
            continue
        if within is not None:
            if package_of(cls.__module__) is not None and cls.__name__ in left and _stack_built(capability, within):
                left = left[left.index(cls.__name__) + 1 :]
            else:
                within = None  # the first capability the stack does not put there: the agent was given it otherwise
        if isinstance(capability, CaptureEvents):
            continue  # the viewer's own, which records events and is no part of what the agent is
        try:
            found.append(_capability(capability, walk, warnings, capabilities, within))
        except Exception as exc:
            name = type(capability).__name__
            _compat.add_warning(warnings, f"capability {name} could not be read ({type(exc).__name__}: {exc})")
            found.append(
                CapabilityInfo(
                    name=name,
                    builtin=name in _compat.BUILTIN_CAPABILITIES,
                    category="custom",
                    package=package_of(type(capability).__module__),
                    deferred=False,
                    details={},
                    expressions={},
                    toolsets=[],
                    native_tools=[],
                    capability_id=None,
                    stack=within,
                    skills=[],
                    hooks=[],
                )
            )
    return found


def _harness_stack(capability: Any) -> str | None:
    """The harness stack whose marker this capability is: what the agent keeps of a ``Coder`` or ``Researcher``."""
    cls = type(cast("object", capability))
    if cls.__name__ != _compat.HARNESS_STACK_MARKER or package_of(cls.__module__) != "pydantic_ai_harness":
        return None
    owner: Any = _safe_getattr(capability, _compat.HARNESS_STACK_OWNER)
    return owner if isinstance(owner, str) and owner in _compat.HARNESS_STACKS else None


def _stack_built(capability: Any, stack: str) -> bool:
    """Whether the stack could have built this member as it is: it states nothing beyond the fields the stack sets."""
    cls = type(cast("object", capability))
    fields = _compat.HARNESS_STACK_MEMBER_FIELDS.get((stack, cls.__name__))
    if fields is None or not dataclasses.is_dataclass(capability):
        return True

    def stated(c: Any) -> dict[str, Any]:
        return {f.name: getattr(c, f.name) for f in dataclasses.fields(c) if f.init and f.compare and f.name != "id"}

    try:
        return bool(stated(capability) == stated(cls(**{name: getattr(capability, name) for name in fields})))
    except Exception:
        return True  # cannot tell: as the stack's


def _stack_helper_of(capability: Any) -> str | None:
    """The harness stack that built this plain ``Capability`` as a helper, going by its id."""
    cls = type(cast("object", capability))
    if cls.__name__ != "Capability" or package_of(cls.__module__) != "pydantic_ai":
        return None
    capability_id: Any = _safe_getattr(capability, "id")
    return _compat.HARNESS_STACK_HELPER_IDS.get(capability_id) if isinstance(capability_id, str) else None


def _capability(
    capability: Any, walk: _ToolsetWalk, warnings: list[str], capabilities: list[Any], stack: str | None
) -> CapabilityInfo:
    """One capability; ``capabilities`` are all the agent's, ``stack`` is the harness stack that put it there."""
    cls = type(cast("object", capability))
    name = cls.__name__
    category: CapabilityCategory = "custom"
    for base in cls.__mro__:  # a class of the project derived from one of Pydantic AI's is in that one's group
        found = category_of(base.__module__, base.__name__)
        if found is not None:
            category = found
            break
    native_tools: list[str] = []
    try:
        for tool in capability.get_native_tools():
            kind = _safe_getattr(tool, "kind")
            if isinstance(kind, str) and kind not in native_tools:
                native_tools.append(kind)
    except Exception as exc:
        _compat.add_warning(warnings, f"capability {name} could not be read ({type(exc).__name__}: {exc})")
        native_tools = []
    capability_id: Any = _safe_getattr(capability, "id")
    return CapabilityInfo(
        name=name,
        builtin=name in _compat.BUILTIN_CAPABILITIES,
        category=category,
        package=package_of(cls.__module__),
        deferred=_safe_getattr(capability, "defer_loading") is True,
        details=_capability_details(capability),
        expressions={},
        toolsets=list(dict.fromkeys(_owned_toolsets(capability, walk, 0))),
        native_tools=native_tools,
        capability_id=capability_id if isinstance(capability_id, str) else None,
        stack=stack,
        skills=skill_catalog(capability, capabilities, warnings) if is_skills(capability) else [],
        hooks=_hooks(capability, warnings),
    )


def _hooks(capability: Any, warnings: list[str]) -> list[HookInfo]:
    """The functions a ``Hooks`` capability registers, by hook point in the order it registered the first of each."""
    if not any(
        base.__name__ == "Hooks" and base.__module__ == _compat.HOOKS_MODULE for base in type(capability).__mro__
    ):
        return []
    registry: Any = _compat.get_private(capability, _compat.HOOKS_REGISTRY, {}, warnings, "the hooks of `Hooks`")
    found: list[HookInfo] = []
    for point, entries in cast("dict[str, list[Any]]", registry if isinstance(registry, dict) else {}).items():
        for entry in entries:
            tools: Any = _safe_getattr(entry, "tools")
            timeout: Any = _safe_getattr(entry, "timeout")
            found.append(
                HookInfo(
                    point=point,
                    function=qualified_name(_safe_getattr(entry, "func")),
                    tools=sorted(str(name) for name in cast("frozenset[Any]", tools))
                    if isinstance(tools, set | frozenset)
                    else None,
                    timeout=float(timeout)
                    if isinstance(timeout, int | float) and not isinstance(timeout, bool)
                    else None,
                    location=None,
                )
            )
    return found


def _owned_toolsets(capability: Any, walk: _ToolsetWalk, depth: int) -> list[str]:
    """The toolsets a capability gives the agent, with those of the capabilities it holds."""
    found = list(walk.owned.get(id(capability), []))
    if depth >= 4:
        return found
    children: Any = _safe_getattr(capability, "capabilities")
    held: list[Any] = list(cast("list[Any]", children)) if isinstance(children, list | tuple) else []
    wrapped = _safe_getattr(capability, "wrapped")
    if wrapped is not None:
        held.append(wrapped)
    for child in held:
        found.extend(_owned_toolsets(child, walk, depth + 1))
    return found


def _capability_details(capability: Any) -> dict[str, Any]:
    """What a capability was given, by field: public fields whose value differs from the default and can be shown.

    For a class of Pydantic AI or the harness, a value the class fills in itself (``Shell().denied_commands``) is a
    default too: an instance given only the parameters the class requires holds it as well.
    """
    if not dataclasses.is_dataclass(capability):
        return {}
    parameters: dict[str, inspect.Parameter] = {}
    with contextlib.suppress(TypeError, ValueError):  # a class with its own ``__init__`` keeps its defaults there
        parameters = dict(inspect.signature(type(capability).__init__).parameters)
    reference, required = _reference(capability)
    details: dict[str, Any] = {}
    for field in dataclasses.fields(capability):
        if field.name.startswith("_") or field.name in ("id", "defer_loading") or is_secret(field.name):
            continue
        value = _safe_getattr(capability, field.name)
        default = field.default
        if default is dataclasses.MISSING and field.name in parameters:
            default = parameters[field.name].default
        if default is not dataclasses.MISSING and default is not inspect.Parameter.empty:
            try:
                if value == default:
                    continue
            except Exception:
                continue
        converted = _detail_value(value)
        if converted is None or converted == [] or converted == {}:
            continue
        if reference is not None and field.name not in required:
            with contextlib.suppress(Exception):
                if _detail_value(_safe_getattr(reference, field.name)) == converted:
                    continue
        details[field.name] = converted
    if any(
        base.__name__ == "Capability" and package_of(base.__module__) == "pydantic_ai"
        for base in type(capability).__mro__
    ):
        held: Any = _safe_getattr(capability, _compat.CAPABILITY_INSTRUCTIONS)
        items: list[Any] = list(cast("list[Any]", held)) if isinstance(held, list | tuple) else []
        given = [_detail_value(_safe_getattr(item, "instruction")) for item in items]
        if given and None not in given:
            details["instructions"] = given[0] if len(given) == 1 else given
    return details


def _reference(capability: Any) -> tuple[Any, frozenset[str]]:
    """An instance of the class of ``capability`` given only the parameters it requires, with the values
    ``capability`` holds, and the names of those parameters.

    ``None`` for a class of neither Pydantic AI nor the harness, or one that cannot be made so.
    """
    cls = type(cast("object", capability))
    if package_of(cls.__module__) is None:
        return None, frozenset()
    try:
        required: dict[str, Any] = {}
        for name, parameter in inspect.signature(cls).parameters.items():
            if parameter.default is not inspect.Parameter.empty or parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                return None, frozenset()
            required[name] = getattr(capability, name)
        with catch_warnings():
            simplefilter("ignore")
            return cls(**required), frozenset(required)
    except Exception:
        return None, frozenset()


def _detail_value(value: Any) -> Any | None:
    """``value`` as JSON that says what it is, or ``None`` when it cannot be shown."""
    if isinstance(value, bool | int | float):
        return clip(value)
    if isinstance(value, str):
        return clip(without_credentials(value))
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, Enum):
        return _detail_value(value.value)
    if isinstance(value, AbstractCapability):
        return type(cast("AbstractCapability[Any]", value)).__name__
    if isinstance(value, Agent):
        name: Any = cast("AnyAgent", value).name
        return name if isinstance(name, str) else None
    cls = type(cast("object", value))
    if cls.__module__.startswith("pydantic_ai.workspaces"):
        return cls.__name__  # where something is read from, without the path it holds
    if cls.__name__ == "SubAgent" and package_of(cls.__module__) == "pydantic_ai_harness":
        fields = {key: _detail_value(_safe_getattr(value, key)) for key in ("agent", "name", "description", "models")}
        return {key: item for key, item in fields.items() if item is not None}
    if isinstance(value, Model):
        model = cast("Model[Any]", value)
        model_id: Any = _safe_getattr(model, "model_id")
        return model_id if isinstance(model_id, str) else _safe_getattr(model, "model_name")
    if inspect.isroutine(value) or isinstance(value, functools.partial):
        return qualified_name(cast("Callable[..., Any]", value))
    if isinstance(value, set | frozenset):  # sorted: a set has no order of its own
        items = [_detail_value(item) for item in cast("set[Any]", value)]
        return None if any(item is None for item in items) else sorted(items, key=str)
    if isinstance(value, list | tuple):
        items = [_detail_value(item) for item in cast("list[Any]", value)]
        return None if any(item is None for item in items) else items
    if isinstance(value, dict) and all(isinstance(key, str) for key in cast("dict[Any, Any]", value)):
        mapping = cast("dict[str, Any]", value)
        shown = {
            key: v for key, item in mapping.items() if not is_secret(key) and (v := _detail_value(item)) is not None
        }
        return shown or None
    return None


def _native_tools(agent: AnyAgent, warnings: list[str]) -> list[str]:
    names: list[str] = []
    for tool in _compat.get_private(agent, "_cap_native_tools", [], warnings):
        kind: Any = _safe_getattr(tool, "kind")
        if not isinstance(kind, str):  # a function standing in for the native tool of a capability loaded on demand
            defaults: Any = _safe_getattr(tool, "__kwdefaults__")
            held: Any = (
                cast("dict[str, Any]", defaults).get(_compat.DEFERRED_NATIVE_TOOL_ARG)
                if isinstance(defaults, dict)
                else None
            )
            kind = _safe_getattr(held, "kind")
        if isinstance(kind, str):
            names.append(kind)
    return names


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _run_settings(agent: AnyAgent, warnings: list[str]) -> dict[str, Any]:
    """``end_strategy``, ``tool_retries``, ``output_retries`` and ``tool_timeout`` of an agent."""
    timeout: Any = _compat.get_private(agent, "_tool_timeout", None, warnings)
    end_strategy: Any = _safe_getattr(agent, "end_strategy")
    return {
        "end_strategy": end_strategy if isinstance(end_strategy, str) else None,
        "tool_retries": _int_or_none(_compat.get_private(agent, "_max_tool_retries", None, warnings)),
        "output_retries": _int_or_none(_compat.get_private(agent, "_max_output_retries", None, warnings)),
        "tool_timeout": float(timeout) if isinstance(timeout, int | float) and not isinstance(timeout, bool) else None,
    }


@functools.cache
def _default_settings(agent_class: type[AnyAgent]) -> dict[str, Any]:
    """The settings of an agent of ``agent_class`` given none; ``{}`` when they cannot be read.

    The class is passed in, so that static analysis of this file sees no agent made here.
    """
    try:
        return _run_settings(agent_class(), [])
    except Exception:
        return {}


def _settings(agent: AnyAgent, warnings: list[str]) -> AgentSettings:
    raw: Any = agent.model_settings
    model_settings: dict[str, Any] | None = None
    if isinstance(raw, dict):
        model_settings = {str(key): _json(value) for key, value in cast("dict[Any, Any]", raw).items()}
    elif raw is not None:
        _compat.add_warning(warnings, "model settings are computed per run; see a run's model requests")
    values = _run_settings(agent, warnings)
    defaults = _default_settings(Agent)
    return AgentSettings(
        model_settings=model_settings,
        end_strategy=values["end_strategy"],
        tool_retries=values["tool_retries"],
        output_retries=values["output_retries"],
        tool_timeout=values["tool_timeout"],
        expressions={},
        defaults=[key for key, value in values.items() if value is not None and defaults.get(key) == value],
    )


def introspect_agent(
    agent: AnyAgent,
    *,
    id: str,
    label: str | None = None,
    delegate_id: Callable[[AnyAgent], str | None],
    wrapper: str | None = None,
) -> AgentBlueprint:
    """Build the static blueprint of ``agent``.

    Args:
        agent: The agent to describe. It is not run and not modified.
        id: Blueprint id allocated by the caller (``"a1"``, ``"a2"``, ...).
        label: Fallback display name (for example the module-level variable name)
            used while ``agent.name`` is ``None``.
        delegate_id: Maps a sub-agent found by the delegation heuristic to its
            blueprint id. Returning ``None`` drops that edge.
        wrapper: Class name of the durable-execution wrapper around ``agent``, if it has one.
    """
    warnings: list[str] = []
    walk = _walk_toolsets(agent, warnings)

    delegates: list[Delegate] = []
    for tool_name, sub_agent in [*_delegates_of(walk), *_capability_delegates(agent)]:
        agent_id = delegate_id(sub_agent)
        if agent_id is not None:
            delegates.append(Delegate(tool_name=tool_name, agent_id=agent_id, evidence="object"))

    name: Any = agent.name
    description: Any = agent.description
    return AgentBlueprint(
        id=id,
        name=name if isinstance(name, str) else None,
        label=(name if isinstance(name, str) and name else None) or label or f"agent-{_ordinal(id)}",
        description=None if description is None else str(description),
        source="object",
        location=None,
        model=_model_info(agent),
        deps_type=_optional_type_label(agent.deps_type),
        instructions=_instructions(agent, warnings),
        toolsets=walk.toolsets,
        tools=walk.tools,
        native_tools=_native_tools(agent, warnings),
        output=_output(agent, warnings),
        capabilities=_capabilities(agent, walk, warnings),
        settings=_settings(agent, warnings),
        delegates=delegates,
        wrapper=wrapper,
        warnings=warnings,
    )


# ------------------------------------------------------------------- graphs


def _node_kind(node: object) -> str:
    if isinstance(node, StartNode):
        return "start"
    if isinstance(node, EndNode):
        return "end"
    if isinstance(node, Step):
        return "step"
    if isinstance(node, Join):
        return "join"
    if isinstance(node, Fork):
        return "map" if cast("Fork[Any, Any]", node).is_map else "broadcast"
    if isinstance(node, Decision):
        return "decision"
    return type(node).__name__.lower()


def _path_edges(source: str, path: Any) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    label: str | None = None
    for item in path.items:
        destination: Any = getattr(item, "destination_id", None)
        if destination is not None:
            edges.append(GraphEdge(source=source, target=str(destination), label=label))
        elif type(item).__name__ == "LabelMarker":
            label = str(item.label)
    return edges


def walk_graph(graph: AnyGraph) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes and edges from the public ``Graph`` fields only.

    Mirrors ``pydantic_graph.graph_builder.build_mermaid_graph``: plain edges come
    from ``edges_by_source``, decision edges from ``Decision.branches[i].path``, and
    edges are listed in node order.
    """
    nodes: list[GraphNode] = []
    by_source: dict[str, list[GraphEdge]] = {}
    graph_nodes = cast("dict[str, Any]", graph.nodes)
    for node_id, node in graph_nodes.items():
        kind = _node_kind(node)
        label: Any = getattr(node, "label", None) if kind == "step" else None
        note: Any = getattr(node, "note", None) if kind == "decision" else None
        nodes.append(
            GraphNode(
                id=str(node_id),
                kind=kind,
                label=label if isinstance(label, str) else None,
                note=note if isinstance(note, str) else None,
                location=None,
            )
        )
    for source, paths in cast("dict[str, list[Any]]", graph.edges_by_source).items():
        for path in paths:
            by_source.setdefault(str(source), []).extend(_path_edges(str(source), path))
    for node_id, node in graph_nodes.items():
        if isinstance(node, Decision):
            for branch in cast("Decision[Any, Any, Any]", node).branches:
                by_source.setdefault(str(node_id), []).extend(_path_edges(str(node_id), branch.path))
    return nodes, [edge for node in nodes for edge in by_source.get(node.id, [])]


def _graph_structure(graph: AnyGraph, warnings: list[str]) -> tuple[list[GraphNode], list[GraphEdge]]:
    build = _compat.load_build_mermaid_graph()
    if build is None:
        _compat.add_warning(
            warnings, "build_mermaid_graph unavailable (pydantic-graph internals changed); used the public graph fields"
        )
    else:
        try:
            mermaid: Any = build(graph.nodes, graph.edges_by_source)
            nodes = [
                GraphNode(id=str(n.id), kind=str(n.kind), label=n.label, note=n.note, location=None)
                for n in mermaid.nodes
            ]
            edges = [GraphEdge(source=str(e.start_id), target=str(e.end_id), label=e.label) for e in mermaid.edges]
            return nodes, edges
        except Exception as exc:
            _compat.add_warning(
                warnings, f"build_mermaid_graph failed ({type(exc).__name__}: {exc}); used the public graph fields"
            )
    try:
        return walk_graph(graph)
    except Exception as exc:
        _compat.add_warning(warnings, f"graph structure unavailable ({type(exc).__name__}: {exc})")
        return [], []


def introspect_graph(graph: AnyGraph, *, id: str, label: str | None = None) -> GraphBlueprint:
    """Build the static blueprint of a pydantic-graph ``Graph``.

    Args:
        graph: The graph to describe. It is not run.
        id: Blueprint id allocated by the caller (``"g1"``, ``"g2"``, ...).
        label: Fallback display name used when ``graph.name`` is ``None``.
    """
    warnings: list[str] = []
    nodes, edges = _graph_structure(graph, warnings)
    mermaid: str | None = None
    try:
        mermaid = str(graph.render(direction="LR"))
    except Exception as exc:
        _compat.add_warning(warnings, f"mermaid diagram unavailable ({type(exc).__name__}: {exc})")
    name: Any = graph.name
    return GraphBlueprint(
        id=id,
        name=name if isinstance(name, str) else None,
        label=(name if isinstance(name, str) and name else None) or label or f"graph-{_ordinal(id)}",
        source="object",
        location=None,
        state_type=_optional_type_label(graph.state_type),
        input_type=_optional_type_label(graph.input_type),
        output_type=_optional_type_label(graph.output_type),
        nodes=nodes,
        edges=edges,
        mermaid=mermaid,
        warnings=warnings,
    )


# -------------------------------------------------------------------- public


def blueprint(target: AnyAgent | AnyGraph | AnyWrapper) -> AgentBlueprint | GraphBlueprint:
    """Describe an agent or a graph without running it.

    Pure: no model call, no server, no session, nothing global is touched. The
    result is a pydantic model; `.model_dump(mode="json")` gives the wire form.

    The blueprint's `id` is `"a1"` (agent) or `"g1"` (graph). Sub-agents found
    by reading the tools' code are referenced from `delegates` as `"a2"`,
    `"a3"`, ... in discovery order; a tool that calls its own agent points at
    `"a1"`.

    Args:
        target: A `pydantic_ai.Agent` (or a durable-execution wrapper around one, such as `TemporalAgent`)
            or a `pydantic_graph.Graph`.

    Returns:
        An `AgentBlueprint` or a `GraphBlueprint`. Anything that could not be
        read from the installed pydantic-ai version is listed in `warnings`.

    Raises:
        TypeError: If `target` is neither an agent nor a graph.
    """
    wrapped = durable_wrapper(target)
    if wrapped is not None:
        target = wrapped[0]
    if isinstance(target, Agent):
        agents = agent_closure(target)

        def delegate_id(agent: AnyAgent) -> str | None:
            for index, known in enumerate(agents):
                if known is agent:
                    return f"a{index + 1}"
            return None

        return introspect_agent(
            target, id="a1", delegate_id=delegate_id, wrapper=None if wrapped is None else wrapped[1]
        )
    if isinstance(target, Graph):  # pyright: ignore[reportUnnecessaryIsInstance]
        return introspect_graph(target, id="g1")
    raise TypeError(f"blueprint() expects a pydantic_ai.Agent or a pydantic_graph.Graph, got {type(target).__name__}")
