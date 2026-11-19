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

import functools
import inspect
import re
import types
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeAlias, Union, cast, get_args, get_origin

from pydantic_ai import Agent, NativeOutput, PromptedOutput, TextOutput, ToolOutput
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
from ._models import (
    AgentBlueprint,
    AgentSettings,
    CapabilityInfo,
    Delegate,
    GraphBlueprint,
    GraphEdge,
    GraphNode,
    Instruction,
    ModelInfo,
    OutputInfo,
    Tool,
    Toolset,
)

AnyAgent: TypeAlias = "Agent[Any, Any]"
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
            self.visit(wrapper.wrapped, (*wrappers, type(wrapper).__name__), _compose(rename, _renamer(wrapper)))
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


def find_delegates(agent: AnyAgent) -> list[tuple[str, AnyAgent]]:
    """``(tool name, sub-agent)`` pairs found by the static delegation heuristic."""
    return _delegates_of(_walk_toolsets(agent, []))


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

    return OutputInfo(
        mode=mode,
        allows_text=allows_text if isinstance(allows_text, bool) else None,
        type_name=type_name,
        json_schema=json_schema,
        tool_names=tool_names,
        location=None,
    )


def _capabilities(agent: AnyAgent, warnings: list[str]) -> list[CapabilityInfo]:
    try:
        capabilities: list[Any] = list(agent.root_capability.capabilities)
    except Exception as exc:
        _compat.add_warning(warnings, f"capabilities unavailable ({type(exc).__name__}: {exc})")
        return []
    return [
        CapabilityInfo(
            name=type(capability).__name__, builtin=type(capability).__name__ in _compat.BUILTIN_CAPABILITIES
        )
        for capability in capabilities
    ]


def _native_tools(agent: AnyAgent, warnings: list[str]) -> list[str]:
    names: list[str] = []
    for tool in _compat.get_private(agent, "_cap_native_tools", [], warnings):
        kind: Any = _safe_getattr(tool, "kind")
        names.append(kind if isinstance(kind, str) else (qualified_name(tool) or type(tool).__name__))
    return names


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _settings(agent: AnyAgent, warnings: list[str]) -> AgentSettings:
    raw: Any = agent.model_settings
    model_settings: dict[str, Any] | None = None
    if isinstance(raw, dict):
        model_settings = {str(key): _json(value) for key, value in cast("dict[Any, Any]", raw).items()}
    elif raw is not None:
        _compat.add_warning(warnings, "model settings are computed per run; see a run's model requests")
    timeout: Any = _compat.get_private(agent, "_tool_timeout", None, warnings)
    end_strategy: Any = _safe_getattr(agent, "end_strategy")
    return AgentSettings(
        model_settings=model_settings,
        end_strategy=end_strategy if isinstance(end_strategy, str) else None,
        tool_retries=_int_or_none(_compat.get_private(agent, "_max_tool_retries", None, warnings)),
        output_retries=_int_or_none(_compat.get_private(agent, "_max_output_retries", None, warnings)),
        tool_timeout=float(timeout) if isinstance(timeout, int | float) and not isinstance(timeout, bool) else None,
        expressions={},
    )


def introspect_agent(
    agent: AnyAgent,
    *,
    id: str,
    label: str | None = None,
    delegate_id: Callable[[AnyAgent], str | None],
) -> AgentBlueprint:
    """Build the static blueprint of ``agent``.

    Args:
        agent: The agent to describe. It is not run and not modified.
        id: Blueprint id allocated by the caller (``"a1"``, ``"a2"``, ...).
        label: Fallback display name (for example the module-level variable name)
            used while ``agent.name`` is ``None``.
        delegate_id: Maps a sub-agent found by the delegation heuristic to its
            blueprint id. Returning ``None`` drops that edge.
    """
    warnings: list[str] = []
    walk = _walk_toolsets(agent, warnings)

    delegates: list[Delegate] = []
    for tool_name, sub_agent in _delegates_of(walk):
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
        capabilities=_capabilities(agent, warnings),
        settings=_settings(agent, warnings),
        delegates=delegates,
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


def blueprint(target: AnyAgent | AnyGraph) -> AgentBlueprint | GraphBlueprint:
    """Describe an agent or a graph without running it.

    Pure: no model call, no server, no session, nothing global is touched. The
    result is a pydantic model; `.model_dump(mode="json")` gives the wire form.

    The blueprint's `id` is `"a1"` (agent) or `"g1"` (graph). Sub-agents found
    by reading the tools' code are referenced from `delegates` as `"a2"`,
    `"a3"`, ... in discovery order; a tool that calls its own agent points at
    `"a1"`.

    Args:
        target: A `pydantic_ai.Agent` or a `pydantic_graph.Graph`.

    Returns:
        An `AgentBlueprint` or a `GraphBlueprint`. Anything that could not be
        read from the installed pydantic-ai version is listed in `warnings`.

    Raises:
        TypeError: If `target` is neither an agent nor a graph.
    """
    if isinstance(target, Agent):
        agents = agent_closure(target)

        def delegate_id(agent: AnyAgent) -> str | None:
            for index, known in enumerate(agents):
                if known is agent:
                    return f"a{index + 1}"
            return None

        return introspect_agent(target, id="a1", delegate_id=delegate_id)
    if isinstance(target, Graph):  # pyright: ignore[reportUnnecessaryIsInstance]
        return introspect_graph(target, id="g1")
    raise TypeError(f"blueprint() expects a pydantic_ai.Agent or a pydantic_graph.Graph, got {type(target).__name__}")
