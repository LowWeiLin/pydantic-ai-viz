"""Agent blueprints from source: ``Agent(...)`` constructions and everything hung on them.

An agent is every call of Pydantic AI's ``Agent`` found in the analysed files. Its
tools, instruction functions and toolsets are found by tracing the receiver of a
decorator or of a registration call (``@agent.tool``, ``toolset.add_function(f)``)
back to the construction it belongs to.

What the source spells out is reported as a value. What it computes is reported
as the text that computes it, with a warning that says why it is not a value.
Nothing is filled in from the installed Pydantic AI: a setting the source does
not mention stays ``None`` here, although the library has a default for it.
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from .. import _compat
from .._capabilities import category_of, is_secret, package_of, without_credentials
from .._models import (
    AgentBlueprint,
    AgentSettings,
    CapabilityInfo,
    Delegate,
    EnvironmentValue,
    HookInfo,
    Instruction,
    ModelInfo,
    OutputInfo,
    SkillInfo,
    SourceLocation,
    Tool,
    Toolset,
)
from .._normalize import clip
from ._docstring import Style, describe
from ._installed import InstalledPydanticAI
from ._resolve import END_OF_MODULE, Definition, Expression, External, Project, Resolved, Resolver, receiver_class
from ._schema import NOT_FROM_THE_MODEL, SchemaBuilder, typing_construct
from ._skills import contained, library, read_catalog, shown
from ._source import CallSite, Module, Mutation, Scope, SkippedFile, clip_expression, parse_module, unwrap
from ._spec import FORMATS, SPEC_METHODS, load_spec, registry, spec_source
from ._values import EnvironmentRead, NotLiteral, environment, format_template, fstring_template, literal, string

ToolKind: TypeAlias = Literal["function", "output", "external", "unapproved"]
PromptKind: TypeAlias = Literal["instructions", "system_prompt"]

AGENT_TOOLSET_ID = "<agent>"
RUN_METHODS = frozenset({"run", "run_sync", "run_stream", "run_stream_sync", "run_stream_events", "iter"})
"""Methods of ``Agent`` that start a run; calling one inside a tool is delegation."""

_WRAPPER_METHODS: dict[str, str] = {
    "prefixed": "PrefixedToolset",
    "filtered": "FilteredToolset",
    "renamed": "RenamedToolset",
    "prepared": "PreparedToolset",
    "approval_required": "ApprovalRequiredToolset",
    "defer_loading": "DeferredLoadingToolset",
    "with_metadata": "SetMetadataToolset",
    "include_return_schemas": "IncludeReturnSchemasToolset",
}
_WRAPPER_CLASSES = frozenset(_WRAPPER_METHODS.values())
_OUTPUT_MODES: dict[str, str] = {
    "ToolOutput": "tool",
    "NativeOutput": "native",
    "PromptedOutput": "prompted",
    "TextOutput": "text",
}
_OUTPUT_WRAPS: dict[str, str] = {
    "ToolOutput": "type_",
    "NativeOutput": "outputs",
    "PromptedOutput": "outputs",
    "TextOutput": "output_function",
}
"""The keyword each output marker takes what it wraps as, when that is not passed first."""
_OFFLINE_MODELS: dict[str, str] = {"TestModel": "test", "FunctionModel": "function"}
_NATIVE_TOOLS: dict[str, str] = {
    "WebSearchTool": "web_search",
    "WebFetchTool": "web_fetch",
    "UrlContextTool": "url_context",
    "CodeExecutionTool": "code_execution",
    "ImageGenerationTool": "image_generation",
    "FileSearchTool": "file_search",
    "MemoryTool": "memory",
    "MCPServerTool": "mcp_server",
}
"""Classes that ``builtin_tools=[...]`` took in earlier Pydantic AI releases -> the kind of native tool each is."""
_CAPABILITY_NATIVE_TOOLS: dict[str, str] = {
    "WebSearch": "web_search",
    "WebFetch": "web_fetch",
    "XSearch": "x_search",
    "ImageGeneration": "image_generation",
    "ToolSearch": "tool_search",
}
"""Core capabilities that give a provider-side tool unless they are given ``native=False``."""
_LOCAL_FALLBACKS: dict[str, str] = {"WebSearch": "web_search", "WebFetch": "web_fetch"}
"""Core capabilities that give a toolset when given ``local=``: the id it has unless ``id=`` names another."""
_HARNESS_TOOLSETS: dict[str, tuple[str, str | None, bool, tuple[str, ...]]] = {
    "FileSystem": ("FileSystemToolset", "file_system", True, ()),
    "Shell": ("ShellToolset", "shell", True, ()),
    "Memory": ("MemoryToolset", "memory", True, ()),
    "Planning": ("PlanningToolset", "planning", False, ()),
    "ToolOutputLimits": ("FunctionToolset", "tool_output_limits", True, ()),
    "PydanticAIDocs": ("PydanticAIDocsToolset", None, False, ()),
    "PruneToolResults": ("FunctionToolset", "prune_tool_results", True, ()),
    "CapabilityCreation": ("CapabilityCreationToolset", "capability_creation", True, ()),
    "SubAgents": ("SubAgentToolset", "sub_agents", True, ()),
    "AskUser": ("AskUserToolset", "ask_user", True, ()),
    "Advisor": ("FunctionToolset", "advisor", True, ("PreparedToolset",)),
    "Skills": ("FunctionToolset", "skills", False, ()),
}
"""Harness capabilities that give a toolset when they are made: its class, its id, whether ``id=`` renames it, and
the wrappers inside the ``CapabilityOwnedToolset`` around it. Which tools it holds depends on what the capability is
given, so they are not listed. ``SubAgents`` gives it only when it is given agents, ``Skills`` only with
``read_files=True``, and ``Advisor`` not with ``local=False``."""
_HARNESS_POSITIONAL: dict[str, tuple[str, ...]] = {
    "Skills": ("directories",),
    "SubAgents": ("agents", "models", "agent_folders"),
    "Advisor": ("model",),
    "CapabilityCreation": ("directory", "guidance"),
    "RepoContext": ("workspace_dir", "home_dir", "filenames"),
    "Peers": ("network",),
    "Memory": ("store", "store_resolver", "agent_name", "namespace"),
}
"""Positional parameters of harness capabilities, as their ``__init__`` names them: the first ones, of those taken
by position."""
_NATIVE_ONLY_FIELDS: dict[str, frozenset[str]] = {
    "WebSearch": frozenset({"allowed_domains", "blocked_domains", "max_uses"}),
    "WebFetch": frozenset({"max_uses"}),
}
"""Parameters of ``WebSearch`` and ``WebFetch`` that only the provider-side tool can honour: given one, the
capability gives no local tool."""
_POSITIONAL: dict[str, tuple[str, ...]] = {
    "Thinking": ("effort",),
    "MCP": ("url",),
    "LocalWorkspace": ("working_dir",),
    "WindowsWorkspace": ("working_dir",),
    "Caching": ("retention",),
    "FileUnderstanding": ("fallback_subagent_model",),
    "PrefixTools": ("wrapped", "prefix"),
    "NativeTool": ("tool",),
    "Toolset": ("toolset",),
    "ToolSearch": ("strategy", "max_results", "tool_description", "parameter_description"),
    "SelectModel": ("selector",),
    "ResolveModelId": ("resolver",),
    "ProcessHistory": ("processor",),
    "ProcessEventStream": ("handler",),
    "PrepareTools": ("prepare_func",),
    "PrepareOutputTools": ("prepare_func",),
    "HandleDeferredToolCalls": ("handler",),
    "IncludeToolReturnSchemas": ("tools",),
    "ReinjectSystemPrompt": ("replace_existing",),
    "Instrumentation": ("settings",),
    "UseThreadExecutor": ("executor",),
    "CombinedCapability": ("capabilities",),
    "WrapperCapability": ("wrapped",),
}
"""Positional parameters of Pydantic AI's capability classes, as their ``__init__`` names them."""
_MAX_LIST_ITEMS = 500
_MAX_VERBATIM_CHARS = 20_000
"""How much of the source of a computed instruction is kept, as written."""
_MAX_HANDED_ON = 4
"""Through how many functions an agent is followed that each hands it on to the next as an argument."""
_TOOL_RECEIVERS = ("Agent", "FunctionToolset", "AbstractToolset")
_MAX_WRAPPERS = 4
_MAX_EXPANSION_DEPTH = 6
_MAX_DEPS_PATH = 3
"""How long ``ctx.deps.a.b`` may be, ``deps`` included, for the agent it holds to be looked for."""
_CONSTRUCTED = ("Agent", "FunctionToolset")
"""The classes of Pydantic AI whose constructions the analysis starts from."""
_MAX_INSTANCES = 16
"""Up to how many calls of a function that makes an agent are each shown as an agent of their own."""


def _inside(scope: Scope, outer: Scope) -> bool:
    """Whether ``scope`` is ``outer`` or lies in it."""
    current: Scope | None = scope
    while current is not None:
        if current is outer:
            return True
        current = current.parent
    return False


def _lambda_name(scope: Scope) -> str:
    """``module.qualname`` of a lambda written in ``scope``, as ``__qualname__`` gives it live."""
    inside = (
        f"{scope.qualname}.<locals>." if scope.kind == "function" else f"{scope.qualname}." if scope.qualname else ""
    )
    return f"{scope.module.name}.{inside}<lambda>"


def _output_mode(kinds: list[str]) -> str:
    """The output mode Pydantic AI picks for outputs of these kinds, by the rule it builds its output schema with.

    A ``NativeOutput`` or ``PromptedOutput`` marker gives its mode. Otherwise text (``str``,
    ``TextOutput``) next to anything the model has to answer with a tool makes it ``tool``, as does
    a ``ToolOutput``; text alone is ``text``; other types are ``auto``. ``None`` takes a tool of its
    own only beside other outputs that do.
    """
    for marked in ("native", "prompted"):
        if marked in kinds:
            return marked
    tools = "tool" in kinds or "auto" in kinds
    if "text" in kinds:
        return "tool" if tools else "text"
    if "tool" in kinds:
        return "tool"
    return "auto" if "auto" in kinds else "unknown"


def _class_around(scope: Scope) -> ast.ClassDef | None:
    """The class a scope lies in, if it lies in one."""
    current: Scope | None = scope
    while current is not None:
        if isinstance(current.node, ast.ClassDef):
            return current.node
        current = current.parent
    return None


def _detail(value: Any) -> Any:
    """A literal argument of a capability as it is shown: URLs without credentials, secrets left out, long text cut."""
    if isinstance(value, str):
        return clip(without_credentials(value))
    if isinstance(value, list):
        return [_detail(item) for item in cast("list[Any]", value)]
    if isinstance(value, dict):
        return {key: _detail(item) for key, item in cast("dict[str, Any]", value).items() if not is_secret(key)}
    return value


def _is_combined(callee: Resolved) -> bool:
    """Whether a capability is made by Pydantic AI's ``CombinedCapability``."""
    return isinstance(callee, External) and callee.comes_from("pydantic_ai", "CombinedCapability")


def _is_capture_events(callee: Resolved) -> bool:
    """Whether a capability is ``pydantic_ai_viz.CaptureEvents``.

    It is the class itself when the analysed code sits inside this package (the bundled demos), whose import is
    followed into its source.
    """
    if isinstance(callee, Definition):
        return callee.module.name == "pydantic_ai_viz._events" and callee.node.name == "CaptureEvents"
    return isinstance(callee, External) and callee.comes_from("pydantic_ai_viz", "CaptureEvents")


def spec_method(callee: Resolved) -> str | None:
    """``from_spec`` or ``from_file`` when a call is that class method of Pydantic AI's ``Agent``."""
    if isinstance(callee, External) and callee.last in SPEC_METHODS:
        return callee.last if External(callee.name.rpartition(".")[0]).is_library("Agent") else None
    return None


def _union(groups: Iterable[list[str]]) -> list[str]:
    """The items of ``groups`` in order, each once."""
    return list(dict.fromkeys(item for group in groups for item in group))


def brief(text: str) -> str:
    """Source text short enough to quote inside a warning."""
    return clip_expression(text, 80)


def location(module: Module, node: ast.AST) -> SourceLocation:
    line: int = getattr(node, "lineno", 1)
    return SourceLocation(file=module.rel, line=max(line, 1))


@dataclass(slots=True, eq=False)
class _ToolSpec:
    """A tool as registered: which function, and what the registration overrides."""

    name: str
    function: Definition | None
    where: SourceLocation
    takes_ctx: bool | None = None
    description: str | None = None
    max_retries: int | None = None
    kind: ToolKind = "function"
    external: str | None = None
    """Dotted name of a function that is defined outside the analysed files."""
    docstring_format: Style | None = None
    """The docstring convention the registration names; ``None`` when it is inferred."""
    docstring_format_expression: str | None = None
    """The source text of a ``docstring_format=`` that is computed when the code runs."""
    unread: str | None = None
    """The source text of the function of a ``Tool(...)`` that cannot be followed: its name is known, its
    parameters are not."""
    made_by: str | None = None
    """Dotted name of the function of Pydantic AI that makes the tool, read from the installed source."""
    library_version: str | None = None
    """The version of the installed Pydantic AI the tool was read from, when Pydantic AI defines it."""


@dataclass(slots=True, eq=False)
class _ToolsetSite:
    """A ``FunctionToolset(...)`` construction, or a class derived from it, and the tools registered on it."""

    site: CallSite | None
    """The construction; ``None`` for a class, whose methods register the tools on ``self``."""
    where: SourceLocation
    tools: list[_ToolSpec] = field(default_factory=list[_ToolSpec])
    used: bool = False
    """Whether an agent was found that is given this toolset."""
    notes: list[str] = field(default_factory=list[str])
    """Warnings about registrations that could not be read, for every agent that uses the toolset."""


@dataclass(slots=True, eq=False)
class _HandedIn:
    """A tool registered on a parameter: ``def register(agent): @agent.tool ...``."""

    function: Scope
    """The body of the function whose parameter the tool is registered on."""
    parameter: str
    spec: _ToolSpec
    depth: int = 0


@dataclass(slots=True, eq=False)
class _AgentSite:
    """An ``Agent(...)`` construction and what decorators and calls add to it."""

    site: CallSite
    via: CallSite | None = None
    """The call of the function that makes this agent, when the function's own ``Agent(...)`` is
    one agent per call of it: each call gives the parameters its own arguments."""
    tools: list[_ToolSpec] = field(default_factory=list[_ToolSpec])
    instructions: list[Instruction] = field(default_factory=list[Instruction])
    system_prompts: list[Instruction] = field(default_factory=list[Instruction])
    output_validators: list[str] = field(default_factory=list[str])
    toolset_functions: list[Definition] = field(default_factory=list[Definition])
    notes: list[str] = field(default_factory=list[str])
    """Warnings about registrations that could not be read."""

    @property
    def module(self) -> Module:
        return self.site.scope.module

    @property
    def key(self) -> tuple[int, int]:
        """What tells this agent from the others: its construction, and the call that makes it."""
        return id(self.site.node), 0 if self.via is None else id(self.via.node)


@dataclass(slots=True)
class _ToolsetEntry:
    """One toolset of an agent, before it is given its id."""

    kind: Literal["agent", "function", "mcp", "dynamic", "other"]
    label: str
    opaque: bool
    toolset_id: str | None = None
    wrappers: list[str] = field(default_factory=list[str])
    expression: str | None = None
    where: SourceLocation | None = None
    tools: list[_ToolSpec] = field(default_factory=list[_ToolSpec])
    prefixes: list[str] = field(default_factory=list[str])
    """Prefixes applied by ``.prefixed(...)`` wrappers, innermost first."""


class AgentExtractor:
    """Finds every agent in a project and describes it."""

    def __init__(
        self,
        project: Project,
        resolver: Resolver,
        installed: InstalledPydanticAI | None = None,
        *,
        max_file_bytes: int = 2_000_000,
    ) -> None:
        self.project = project
        self.resolver = resolver
        self.installed = installed
        """Pydantic AI's own source, for the tools it defines; ``None`` reads none of it."""
        self.max_file_bytes = max_file_bytes
        """The largest ``SKILL.md`` that is read."""
        self.agents: dict[tuple[int, int], _AgentSite] = {}
        """``_AgentSite.key`` -> the agent, in discovery order."""
        self.instances: dict[int, list[_AgentSite]] = {}
        """``id(ast.Call)`` of an ``Agent(...)`` -> the agents it makes: one, or one per call of its function."""
        self.runs_parameter: dict[tuple[int, str], bool] = {}
        """``(id(function body), parameter)`` -> whether the function runs that parameter as an agent."""
        self._alias: dict[tuple[int, int], _AgentSite] = {}
        """Filled by ``blueprints``: an agent whose blueprint is the same as another's -> the one it is shown as."""
        self.toolsets: dict[int, _ToolsetSite] = {}
        """``id(ast.Call)`` of a ``FunctionToolset(...)``, or ``id(ast.ClassDef)`` of a class derived from it."""
        self.conditional = False
        """Set by ``items`` when an entry it returned depends on a condition."""
        self.added_unread: list[str] = []
        """Filled by ``items``: statements that add entries to a list it returned which it could not read."""
        self.calls_of: dict[int, list[CallSite]] = {}
        """``id`` of a function definition -> the calls of that function."""
        self.handed_in: list[_HandedIn] = []
        """Tools registered on a parameter, until the calls that pass an agent for it are looked at."""
        self.unattached: list[_ToolSpec] = []
        """Tools registered on a parameter for which no call passes an agent that the source shows."""
        self.durable: dict[tuple[int, int], str] = {}
        """``_AgentSite.key`` -> the class of the durable-execution wrapper the source gives it."""

    # -------------------------------------------------------------- driver

    def find(self) -> None:
        """Locate the agents and toolsets, then attach what decorators and calls register on them."""
        constructions: list[tuple[CallSite, str | None]] = []
        for module in self.project.modules:
            for site in sorted(module.calls, key=lambda item: (item.node.lineno, item.node.col_offset)):
                callee = self.resolver.callee(site.node, site.scope)
                made, note = self._constructs(site, callee)
                if made == "Agent":
                    constructions.append((site, note))
                elif made == "FunctionToolset":
                    toolset = _ToolsetSite(site, location(module, site.node))
                    toolset.notes.extend([note] if note else [])
                    self.toolsets[id(site.node)] = toolset
                elif isinstance(callee, Definition) and not isinstance(callee.node, ast.ClassDef):
                    self.calls_of.setdefault(id(callee.node), []).append(site)
            for body in module.class_scopes.values():
                # ``class SkillsToolset(FunctionToolset)``: its methods register tools with ``@self.tool``.
                defined = body.node
                if not isinstance(defined, ast.ClassDef) or body.parent is None:
                    continue
                for base in defined.bases[:_MAX_WRAPPERS]:
                    written = base.value if isinstance(base, ast.Subscript) else base
                    parent = self.resolver.resolve(written, body.parent, defined.lineno)
                    if isinstance(parent, External) and parent.is_library("FunctionToolset"):
                        self.toolsets[id(defined)] = _ToolsetSite(None, location(module, defined))
        for site, note in constructions:
            for agent in self._made_by(site):
                agent.notes.extend([note] if note else [])
                self.agents[agent.key] = agent
                self.instances.setdefault(id(site.node), []).append(agent)
        for module in self.project.modules:
            self._registrations(module)
        self._attach_handed_in()
        self._find_durable()

    def _find_durable(self) -> None:
        """Note the agents that ``TemporalAgent(agent)``, ``DBOSAgent(agent)`` and ``PrefectAgent(agent)`` wrap."""
        for module in self.project.modules:
            for site in module.calls:
                callee = self.resolver.callee(site.node, site.scope)
                if not (
                    isinstance(callee, External)
                    and callee.name.startswith("pydantic_ai.durable_exec.")
                    and callee.last.endswith("Agent")
                ):
                    continue
                wrapped = site.node.args[0] if site.node.args else None
                if wrapped is None:
                    wrapped = next((item.value for item in site.node.keywords if item.arg == "wrapped"), None)
                if wrapped is None or isinstance(wrapped, ast.Starred):
                    continue
                for agent in self.agents_at(wrapped, site.scope, site.node.lineno):
                    self.durable.setdefault(agent.key, callee.last)

    def _constructs(self, site: CallSite, callee: Resolved) -> tuple[str | None, str | None]:
        """Which of ``Agent`` and ``FunctionToolset`` a call makes, if either; and a warning if the run decides it.

        ``try: from pydantic_ai import Agent`` with ``except ImportError: Agent = None``: the
        name is Pydantic AI's class in one case and not in another. The call is read as the
        class, said to be so in some cases only, whichever value stands closer above it.
        """
        made = callee.last if isinstance(callee, External) and callee.is_library(*_CONSTRUCTED) else None
        if spec_method(callee) is not None:
            return "Agent", None
        function = site.node.func.value if isinstance(site.node.func, ast.Subscript) else site.node.func
        if not isinstance(function, ast.Name):
            return made, None
        alternatives = self.resolver.alternatives(function.id, site.scope, site.node.lineno)
        kinds = [
            value.last if isinstance(value, External) and value.is_library(*_CONSTRUCTED) else None
            for _, value in alternatives
        ]
        found = next((kind for kind in kinds if kind is not None), None)
        if found is None or all(kind == found for kind in kinds):
            return made, None
        lines = [str(binding.line) for binding, _ in alternatives]
        listed = ", ".join(lines[:-1]) + f" and {lines[-1]}"
        return found, (
            f"`{function.id}` is Pydantic AI's `{found}` in some cases and not in others (it is given a value at "
            f"lines {listed}); which one it is is decided when the code runs"
        )

    def _made_by(self, site: CallSite) -> list[_AgentSite]:
        """The agents an ``Agent(...)`` makes: one per call of the function it stands in, or one.

        ``def make(name): return Agent(..., name=name)`` called as ``researcher = make("researcher")``
        and ``writer = make("writer")`` makes two agents, each with its own arguments. A function
        with a decorator is left whole: the decorator may hand out one agent for every call.
        Agents of one function whose blueprints turn out the same are shown as one, by ``blueprints``.
        """
        function = site.scope.node
        if site.scope.kind != "function" or not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            return [_AgentSite(site)]
        calls = [
            call
            for call in self.calls_of.get(id(function), ())
            if not _inside(call.scope, site.scope)  # a call of the function from its own body is no new caller
        ]
        if function.decorator_list or not calls:
            return [_AgentSite(site)]
        if len(calls) > _MAX_INSTANCES:
            agent = _AgentSite(site)
            agent.notes.append(
                f"`{function.name}`, which makes this agent, is called in {len(calls)} places; "
                "it is shown once, with what each of them adds to it"
            )
            return [agent]
        return [_AgentSite(site, via=call) for call in calls]

    def parameters(self, agent: _AgentSite) -> dict[int, Expression]:
        """What each parameter of the function that makes ``agent`` stands for in the call that makes it.

        That is the argument the call gives it or, when the call leaves it out, its default.
        A parameter the call may set through ``*args`` or ``**kwargs`` stands for nothing known.
        """
        via, body = agent.via, agent.site.scope
        function = body.node
        if via is None or not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            return {}
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        with_default = positional[len(positional) - len(arguments.defaults) :]
        defaults = {item.arg: default for item, default in zip(with_default, arguments.defaults, strict=True)}
        defaults.update(
            (item.arg, default)
            for item, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
            if default is not None
        )
        spread = any(keyword.arg is None for keyword in via.node.keywords) or any(
            isinstance(argument, ast.Starred) for argument in via.node.args
        )
        found: dict[int, Expression] = {}
        for parameter in [*positional[1 if body.receiver is not None else 0 :], *arguments.kwonlyargs]:
            bindings = body.bindings.get(parameter.arg, [])
            if len(bindings) != 1:
                continue  # given another value in the body too: which one holds is up to the run
            argument = self.argument_for(via.node, body, parameter.arg)
            if argument is not None:
                found[id(bindings[0])] = Expression(argument, via.scope, via.node.lineno)
            elif not spread and parameter.arg in defaults and body.parent is not None:
                found[id(bindings[0])] = Expression(defaults[parameter.arg], body.parent, function.lineno)
        return found

    def notices(self) -> list[str]:
        """What was found that belongs to no agent the source shows. Complete once ``blueprints`` has run."""
        return [*self._unattached_notice(), *self._unused_notice()]

    def _unused_notice(self) -> list[str]:
        unused = [site for site in self.toolsets.values() if site.tools and not site.used]
        if not unused:
            return []
        tools = sum(len(site.tools) for site in unused)
        first = f"{unused[0].where.file}:{unused[0].where.line}"
        counted = "1 tool" if tools == 1 else f"{tools} tools"
        if len(unused) == 1:
            return [
                f"1 function toolset with {counted} (at {first}) is given to no agent that the source shows, "
                "so what it holds is shown on none"
            ]
        return [
            f"{len(unused)} function toolsets with {counted} (for example the one at {first}) are given to no "
            "agent that the source shows, so what they hold is shown on none"
        ]

    def _unattached_notice(self) -> list[str]:
        if not self.unattached:
            return []
        first = self.unattached[0]
        where = f"`{first.name}` at {first.where.file}:{first.where.line}"
        handed = "registered on an agent or toolset that a function is handed as an argument"
        if len(self.unattached) == 1:
            return [
                f"1 tool is {handed} ({where}); which agent gets it is decided when the code runs, "
                "so it is shown on none"
            ]
        return [
            f"{len(self.unattached)} tools are {handed} (for example {where}); which agent gets them is decided "
            "when the code runs, so they are shown on none"
        ]

    @cached_property
    def _deps_given(self) -> dict[tuple[int, int], list[Expression]]:
        """``_AgentSite.key`` -> the ``deps=`` values that the source's runs of the agent are given."""
        given: dict[tuple[int, int], list[Expression]] = {}
        for module in self.project.modules:
            for site in module.calls:
                call = site.node
                if not isinstance(call.func, ast.Attribute) or call.func.attr not in RUN_METHODS:
                    continue
                deps = next((keyword.value for keyword in call.keywords if keyword.arg == "deps"), None)
                if deps is None:
                    continue
                for agent in self.agents_at(call.func.value, site.scope, call.lineno):
                    given.setdefault(agent.key, []).append(Expression(deps, site.scope, call.lineno))
        return given

    def agents_in_deps(self, agent: _AgentSite, path: list[str]) -> list[_AgentSite]:
        """The agents that runs of ``agent`` are given as their dependencies, or as a field of them.

        ``path`` is the attributes after ``ctx.deps``: ``[]`` for ``deps=other_agent``,
        ``["searcher"]`` for ``deps=Deps(searcher=other_agent)``.
        """
        found: list[_AgentSite] = []
        for deps in self._deps_given.get(agent.key, [])[:_MAX_LIST_ITEMS]:
            node: ast.expr | None = deps.node
            scope, line = deps.scope, deps.line
            for attribute in path:
                target = self.resolver.resolve(node, scope, line) if node is not None else None
                if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
                    node = None
                    break
                node = next((keyword.value for keyword in target.node.keywords if keyword.arg == attribute), None)
                scope, line = target.scope, target.line
            given = None if node is None else self.agent_at(node, scope, line)
            if given is not None and given not in found:
                found.append(given)
        return found

    def blueprints(self) -> list[AgentBlueprint]:
        ids = {key: f"a{index}" for index, key in enumerate(self.agents, start=1)}
        _ = self._deps_given  # read before any parameter is given the argument of one call
        built: dict[tuple[int, int], AgentBlueprint] = {}
        for agent in self.agents.values():
            with self.resolver.standing_for(self.parameters(agent)):
                built[agent.key] = _AgentBuilder(self, agent, ids).build()
        several = self._fold_alike(built)
        # What is left is numbered again, and every edge to an agent shown as another one follows it.
        kept = [key for key in self.agents if key not in self._alias]
        final = {ids[key]: f"a{index}" for index, key in enumerate(kept, start=1)}
        for key, agent in self._alias.items():
            final[ids[key]] = final[ids[agent.key]]
        blueprints: list[AgentBlueprint] = []
        for key in kept:
            blueprint, new = built[key], final[ids[key]]
            label = blueprint.label
            if key in several:
                label = blueprint.name or self.agents[key].site.bound_to or f"agent-{new[1:]}"
            elif label == f"agent-{blueprint.id[1:]}":
                label = f"agent-{new[1:]}"
            delegates: list[Delegate] = []
            for delegate in blueprint.delegates:
                moved = delegate.model_copy(update={"agent_id": final[delegate.agent_id]})
                if moved not in delegates:
                    delegates.append(moved)
            blueprints.append(blueprint.model_copy(update={"id": new, "label": label, "delegates": delegates}))
        self.agents = {key: self.agents[key] for key in kept}
        return blueprints

    def _fold_alike(self, built: dict[tuple[int, int], AgentBlueprint]) -> set[tuple[int, int]]:
        """Show the agents of one function whose blueprints are the same as one; the keys of those standing for several.

        Tests that each call ``make_agent()``, or calls that differ only in arguments the agent
        does not depend on, make agents that nothing in the source tells apart.
        """
        several: set[tuple[int, int]] = set()
        for made in self.instances.values():
            if len(made) < 2:
                continue
            first_of: dict[str, _AgentSite] = {}
            for agent in made:
                shown = built[agent.key].model_dump_json(exclude={"id", "label"})
                first = first_of.setdefault(shown, agent)
                if first is not agent:
                    self._alias[agent.key] = first
                    several.add(first.key)
        return several

    def _instances(self, call: ast.Call, through: list[ast.Call], prefer: CallSite | None = None) -> list[_AgentSite]:
        """The agents an ``Agent(...)`` call makes that a value reached through the calls ``through`` is.

        A value that went through one of the calls that make an agent is that agent. One that went
        through none (``agent`` inside the function that makes it) is any of them, or, given ``prefer``,
        the one made by that call: an agent made alongside another one is made by the same call.
        """
        made = self.instances.get(id(call), [])
        if len(made) <= 1:
            return made
        passed = {id(item) for item in through}
        chosen = [agent for agent in made if agent.via is not None and id(agent.via.node) in passed]
        if not chosen and prefer is not None:
            chosen = [agent for agent in made if agent.via is prefer]
        # Once blueprints are made, an agent shown as another one is that one.
        return list(dict.fromkeys(self._alias.get(agent.key, agent) for agent in chosen or made))

    def agents_at(
        self, node: ast.expr, scope: Scope, line: int | None = None, *, prefer: CallSite | None = None
    ) -> list[_AgentSite]:
        """The agents an expression stands for: one construction, or each agent a function makes there."""
        target = self.resolver.resolve(node, scope, line)
        through = list(self.resolver.through)
        for _ in range(_MAX_WRAPPERS):
            if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
                return []
            call = target.node
            found = self._instances(call, through, prefer)
            if found:
                return found
            # ``TemporalAgent(agent)``, ``DBOSAgent(agent)``: Pydantic AI's wrappers run the agent they are given.
            callee = self.resolver.callee(call, target.scope, target.line)
            if not (isinstance(callee, External) and callee.is_library_class() and callee.last.endswith("Agent")):
                return []
            wrapped = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
            wrapped = wrapped or next((item.value for item in call.keywords if item.arg == "wrapped"), None)
            if wrapped is None:
                return []
            target = self.resolver.resolve(wrapped, target.scope, target.line)
            through.extend(self.resolver.through)
        return []

    def agent_at(self, node: ast.expr, scope: Scope, line: int | None = None) -> _AgentSite | None:
        """The agent an expression stands for, if it can be traced to one agent."""
        found = self.agents_at(node, scope, line)
        return found[0] if len(found) == 1 else None

    def agent_id(self, node: ast.expr, scope: Scope, line: int | None = None) -> str | None:
        """Blueprint id of the agent an expression stands for."""
        found = self.agent_at(node, scope, line)
        if found is None:
            return None
        return f"a{list(self.agents).index(found.key) + 1}"

    def toolset_made_by(self, call: ast.Call, scope: Scope, line: int) -> tuple[_ToolsetSite, str | None] | None:
        """The function toolset a call constructs, with the name of its class when that is one of the project's."""
        found = self.toolsets.get(id(call))
        if found is not None:
            return found, None
        callee = self.resolver.callee(call, scope, line)
        if isinstance(callee, Definition) and isinstance(callee.node, ast.ClassDef):
            found = self.toolsets.get(id(callee.node))
            if found is not None:
                return found, callee.node.name
        return None

    def _receivers(self, node: ast.expr, scope: Scope, line: int) -> list[_AgentSite | _ToolsetSite]:
        """What a registration on ``node`` adds to: a toolset, an agent, or each agent a function makes."""
        if isinstance(node, ast.Name):
            # ``@self.tool`` in a method of a class derived from ``FunctionToolset``.
            owner = receiver_class(node.id, scope)
            if owner is not None and id(owner.node) in self.toolsets:
                return [self.toolsets[id(owner.node)]]
        target = self.resolver.resolve(node, scope, line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            agents: list[_AgentSite | _ToolsetSite] = [*self._instances(target.node, self.resolver.through)]
            toolset = self.toolsets.get(id(target.node))
            return agents or ([toolset] if toolset is not None else [])
        return []

    # ------------------------------------------------------- registrations

    def _registrations(self, module: Module) -> None:
        """Apply ``@x.tool`` decorators and ``x.tool(f)`` statements in the order they appear."""
        events: list[tuple[int, ast.expr, Scope, Definition | None]] = []
        for decorated in module.decorated:
            definition = Definition(decorated.node, decorated.scope)
            for decorator in decorated.node.decorator_list:
                events.append((decorated.node.lineno, decorator, decorated.scope, definition))
        for site in module.calls:
            if site.statement:
                events.append((site.node.lineno, site.node, site.scope, None))
        for line, expression, scope, definition in sorted(events, key=lambda event: event[0]):
            if definition is None:
                # A registration call inside ``for tool in (a, b):`` is one registration per item.
                for _ in self.resolver.each(expression, scope, line):
                    self._register(expression, scope, line, None)
            else:
                self._register(expression, scope, line, definition)

    def _register(self, expression: ast.expr, scope: Scope, line: int, decorated: Definition | None) -> None:
        call = expression if isinstance(expression, ast.Call) else None
        method = call.func if call is not None else expression
        if decorated is None and call is None:
            return
        held_in, held_at = scope, line  # where the receiver is evaluated: here, unless the method is held in a name
        if isinstance(method, ast.Name):
            # ``register = agent.tool_plain`` and then ``@register``: the method the name holds.
            target = self.resolver.resolve(method, scope, line)
            if self.resolver.reassigned() or not isinstance(target, Expression):
                return
            method, held_in, held_at = target.node, target.scope, target.line
        if not isinstance(method, ast.Attribute):
            return
        receivers = self._receivers(method.value, held_in, held_at)
        if not receivers:
            if decorated is not None and not (call is not None and call.args):
                self._on_a_parameter(method, call, held_in, held_at, decorated)
            return
        for receiver in receivers:
            self._register_on(receiver, method, call, scope, line, decorated, expression)

    def _register_on(
        self,
        receiver: _AgentSite | _ToolsetSite,
        method: ast.Attribute,
        call: ast.Call | None,
        scope: Scope,
        line: int,
        decorated: Definition | None,
        written: ast.expr,
    ) -> None:
        """Register on one agent or toolset; ``written`` is the decorator or the call as the source has it."""
        keywords = {} if call is None else {item.arg: item.value for item in call.keywords if item.arg is not None}
        function = decorated
        if function is None and call is not None:
            # The call form: ``agent.tool(fetch)``, ``toolset.add_function(fetch, retries=2)``.
            if not call.args or isinstance(call.args[0], ast.Starred):
                return
            if method.attr == "add_tool":
                spec = self.tool_of(call.args[0], scope, line)
                if spec is not None and isinstance(receiver, _ToolsetSite):
                    receiver.tools.append(spec)
                elif spec is None:
                    self._unread(receiver, call, scope)
                return
            if method.attr == "output_validator" and isinstance(call.args[0], ast.Lambda):
                if isinstance(receiver, _AgentSite):
                    receiver.output_validators.append(_lambda_name(scope))
                return
            target = self.resolver.resolve(call.args[0], scope, line)
            if not isinstance(target, Definition) or isinstance(target.node, ast.ClassDef):
                if method.attr in ("tool", "tool_plain", "add_function"):
                    self._unread(receiver, call, scope)
                return
            function = target
        elif call is not None and call.args:
            return  # ``@agent.tool(something)`` is not a registration form
        if function is None or isinstance(function.node, ast.ClassDef):
            return
        where = location(function.module, function.node)
        name = method.attr
        if name in ("tool", "tool_plain", "add_function"):
            takes_ctx: bool | None = {"tool": True, "tool_plain": False}.get(name)
            if name == "add_function":
                takes_ctx = self._bool(keywords.get("takes_ctx"), scope, line)
                if takes_ctx is None:
                    takes_ctx = self._takes_ctx(function)
            spec = _ToolSpec(function.node.name, function, where, takes_ctx=takes_ctx)
            self._overrides(spec, keywords, scope, line)
            receiver.tools.append(spec)
            self._note_subclass(receiver, scope, f"the tool `{spec.name}`")
            if decorated is not None:
                self._note_applied_first(receiver, spec.name, decorated, written)
        elif isinstance(receiver, _AgentSite):
            if name == "instructions" or name == "system_prompt":
                what = "instructions" if name == "instructions" else "system prompt"
                self._note_subclass(receiver, scope, f"the {what} function `{function.node.name}`")
                prompt = Instruction(
                    kind=name,
                    dynamic=True,
                    text=None,
                    function=function.qualified_name,
                    expression=None,
                    location=where,
                )
                (receiver.instructions if name == "instructions" else receiver.system_prompts).append(prompt)
            elif name == "output_validator":
                receiver.output_validators.append(function.qualified_name)
            elif name == "toolset":
                receiver.toolset_functions.append(function)

    def _note_applied_first(
        self, receiver: _AgentSite | _ToolsetSite, name: str, decorated: Definition, registration: ast.expr
    ) -> None:
        """Say so when a decorator below the registration has already replaced the function it registers.

        Its parameters are then those of what that decorator returns, which the ``def`` may not
        show; whether they are is not read from the decorator's code. A decorator that is itself a
        registration returns the function as it was.
        """
        node = decorated.node
        assert not isinstance(node, ast.ClassDef)
        below = next((index for index, item in enumerate(node.decorator_list) if item is registration), None)
        if below is None:
            return
        for decorator in node.decorator_list[below + 1 :]:
            method = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(method, ast.Attribute) and self._receivers(method.value, decorated.scope, node.lineno):
                continue
            text = decorated.module.text(decorator)
            _compat.add_warning(
                receiver.notes,
                f"the tool `{name}` is registered after `@{text}` is applied to it; its parameters are read "
                f"from the `def`, which `@{text}` may change",
            )

    @staticmethod
    def _note_subclass(receiver: _AgentSite | _ToolsetSite, scope: Scope, what: str) -> None:
        """Say so when something is added to an agent by a class other than the one that makes the agent.

        ``self.agent = Agent(...)`` in a base class and ``@self.agent.tool`` in a class
        derived from it: one construction in the source, and at run time one agent
        per class, of which only some have the tool.
        """
        if not isinstance(receiver, _AgentSite):
            return
        adding, making = _class_around(scope), _class_around(receiver.site.scope)
        if adding is None or making is None or adding is making:
            return
        _compat.add_warning(
            receiver.notes,
            f"{what} is added by the class `{adding.name}`, which is derived from `{making.name}` where "
            f"this agent is made; only an agent made through `{adding.name}` has it",
        )

    def _on_a_parameter(
        self, method: ast.Attribute, call: ast.Call | None, scope: Scope, line: int, function: Definition
    ) -> None:
        """Note ``@agent.tool`` where ``agent`` is a parameter: whose tool it is depends on who calls."""
        if method.attr not in ("tool", "tool_plain") or not isinstance(method.value, ast.Name):
            return
        if isinstance(function.node, ast.ClassDef):
            return
        binding = self.resolver.lookup(method.value.id, scope, line)
        if binding is None or binding.kind != "opaque" or binding.scope.kind != "function":
            return
        owner = binding.scope.node
        if not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef) or binding.line != owner.lineno:
            return
        parameter = next(
            (
                item
                for item in (*owner.args.posonlyargs, *owner.args.args, *owner.args.kwonlyargs)
                if item.arg == binding.name
            ),
            None,
        )
        if parameter is None:
            return
        # Other libraries have a ``.tool`` decorator too. This is Pydantic AI's when the parameter is
        # annotated as an agent or a toolset, when the function takes a ``RunContext``, or when the
        # decorator is ``tool_plain``, which only Pydantic AI has.
        takes_ctx = method.attr == "tool"
        if takes_ctx and not self._takes_ctx(function) and not self._is_tool_receiver(parameter, binding.scope):
            return
        spec = _ToolSpec(function.node.name, function, location(function.module, function.node), takes_ctx=takes_ctx)
        keywords = {} if call is None else {item.arg: item.value for item in call.keywords if item.arg is not None}
        self._overrides(spec, keywords, scope, line)
        self.handed_in.append(_HandedIn(binding.scope, binding.name, spec))

    def _is_tool_receiver(self, parameter: ast.arg, body: Scope) -> bool:
        """Whether a parameter is annotated as an ``Agent`` or a toolset of Pydantic AI."""
        annotation = parameter.annotation
        if annotation is None:
            return False
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return annotation.value.replace(" ", "").split("[")[0].rpartition(".")[2] in _TOOL_RECEIVERS
        if isinstance(annotation, ast.Subscript):
            annotation = annotation.value
        target = self.resolver.resolve(annotation, body.parent or body, annotation.lineno)
        return isinstance(target, External) and target.is_library(*_TOOL_RECEIVERS)

    def _attach_handed_in(self) -> None:
        """Give each tool registered on a parameter to the agents that calls of the function pass for it."""
        pending = list(self.handed_in)
        for index, handed in enumerate(pending):
            if index > _MAX_LIST_ITEMS * _MAX_HANDED_ON:
                break
            owner = handed.function.node
            assert isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef)
            attached = False
            for site in self.calls_of.get(id(owner), ()):
                argument = self.argument_for(site.node, handed.function, handed.parameter)
                if argument is None:
                    continue
                receivers = self._receivers(argument, site.scope, site.node.lineno)
                for receiver in receivers:
                    receiver.tools.append(handed.spec)
                    attached = True
                if not receivers and isinstance(argument, ast.Name) and handed.depth < _MAX_HANDED_ON:
                    # The caller was handed the agent itself: ``def register_all(agent): register_one(agent)``.
                    outer = self.resolver.lookup(argument.id, site.scope, site.node.lineno)
                    if outer is not None and outer.kind == "opaque" and outer.scope.kind == "function":
                        passed_on = _HandedIn(outer.scope, outer.name, handed.spec, handed.depth + 1)
                        if self.calls_of.get(id(outer.scope.node)):
                            pending.append(passed_on)
                            attached = True  # decided by the calls of the outer function
            if not attached and handed.spec not in self.unattached:
                self.unattached.append(handed.spec)
        # A tool that one caller placed and another could not is placed: it is shown.
        placed = {id(spec) for site in (*self.agents.values(), *self.toolsets.values()) for spec in site.tools}
        self.unattached = [spec for spec in self.unattached if id(spec) not in placed]

    def argument_for(self, call: ast.Call, function: Scope, parameter: str) -> ast.expr | None:
        """The argument a call passes for a parameter of the function it calls."""
        for keyword in call.keywords:
            if keyword.arg == parameter:
                return keyword.value
        node = function.node
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        positional = [item.arg for item in (*node.args.posonlyargs, *node.args.args)]
        if parameter not in positional:
            return None
        index = positional.index(parameter) - (1 if function.receiver is not None else 0)
        if (
            index < 0
            or index >= len(call.args)
            or any(isinstance(item, ast.Starred) for item in call.args[: index + 1])
        ):
            return None
        return call.args[index]

    @staticmethod
    def _unread(receiver: _AgentSite | _ToolsetSite, call: ast.Call, scope: Scope) -> None:
        """Note a registration whose function cannot be traced: the tool exists, and is not shown."""
        text = brief(scope.module.text(call))
        _compat.add_warning(
            receiver.notes, f"a tool is added by `{text}`; which function that is is only known when the code runs"
        )

    def _overrides(self, spec: _ToolSpec, keywords: dict[str, ast.expr], scope: Scope, line: int) -> None:
        """Apply the keyword arguments of a registration that change how the tool is presented."""
        name = keywords.get("name")
        if name is not None:
            spec.name = string(self.resolver, name, scope, line) or spec.name
        description = keywords.get("description")
        if description is not None:
            spec.description = string(self.resolver, description, scope, line)
        for key in ("retries", "max_retries"):
            if key in keywords:
                value = self._literal(keywords[key], scope, line)
                if isinstance(value, int) and not isinstance(value, bool):
                    spec.max_retries = value
        if self._bool(keywords.get("requires_approval"), scope, line):
            spec.kind = "unapproved"
        written = keywords.get("docstring_format")
        if written is not None:
            style = string(self.resolver, written, scope, line)
            if style in ("google", "numpy", "sphinx"):
                spec.docstring_format = style
            elif style != "auto":
                spec.docstring_format_expression = scope.module.text(written)

    def _literal(self, node: ast.expr, scope: Scope, line: int) -> Any:
        try:
            return literal(self.resolver, node, scope, line)
        except NotLiteral:
            return None

    def _bool(self, node: ast.expr | None, scope: Scope, line: int) -> bool | None:
        value = None if node is None else self._literal(node, scope, line)
        return value if isinstance(value, bool) else None

    def _takes_ctx(self, function: Definition) -> bool:
        """Whether a function's first parameter is annotated ``RunContext``, as Pydantic AI checks it."""
        node = function.node
        assert not isinstance(node, ast.ClassDef)
        parameters = [*node.args.posonlyargs, *node.args.args][self.receiver_count(function) :]
        if not parameters or parameters[0].annotation is None:
            return False
        annotation = parameters[0].annotation
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return annotation.value.replace(" ", "").split("[")[0].rpartition(".")[2] == "RunContext"
        if isinstance(annotation, ast.Subscript):
            annotation = annotation.value
        target = self.resolver.resolve(annotation, function.scope, node.lineno)
        return isinstance(target, External) and target.is_library("RunContext")

    @staticmethod
    def receiver_count(function: Definition) -> int:
        """1 when the function is a method whose first parameter is ``self`` or ``cls``, else 0."""
        return 1 if function.body.receiver is not None else 0

    def tool_of(self, node: ast.expr, scope: Scope, line: int, depth: int = 0) -> _ToolSpec | None:
        """A tool from an entry of ``tools=[...]``: a function, or a ``Tool(function, ...)``."""
        target: Resolved = self.resolver.resolve(node, scope, line)
        if isinstance(target, Definition) and not isinstance(target.node, ast.ClassDef):
            where = location(target.module, target.node)
            return _ToolSpec(target.node.name, target, where, takes_ctx=self._takes_ctx(target))
        if isinstance(target, External):
            return _ToolSpec(target.last, None, location(scope.module, node), external=target.name)
        if isinstance(target, Expression) and isinstance(target.node, ast.Attribute):
            return self._method_tool(target)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call) and depth < 2:
            call = target.node
            callee = self.resolver.callee(call, target.scope, target.line)
            if isinstance(callee, External) and callee.is_library("Tool"):
                keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
                inner = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
                inner = inner or keywords.get("function")
                if inner is None:
                    return None
                self.resolver.resolve(inner, target.scope, target.line)
                # A function assigned in more than one place: which one the tool runs is decided at run time.
                spec = None if self.resolver.reassigned() else self.tool_of(inner, target.scope, target.line, depth + 1)
                if spec is None:
                    named = keywords.get("name")
                    name = None if named is None else string(self.resolver, named, target.scope, target.line)
                    if name is None:
                        return None
                    # The tool is named in the source; what it takes is only known when the code runs.
                    where = location(target.module, call)
                    spec = _ToolSpec(name, None, where, unread=target.module.text(inner))
                takes_ctx = self._bool(keywords.get("takes_ctx"), target.scope, target.line)
                if takes_ctx is not None:
                    spec.takes_ctx = takes_ctx
                self._overrides(spec, keywords, target.scope, target.line)
                return spec
            if isinstance(callee, External) and depth == 0:
                return self.installed_tool(callee, location(target.module, call))
        return None

    def installed_tool(self, callee: External, where: SourceLocation) -> _ToolSpec | None:
        """A tool that a function of Pydantic AI makes, such as a factory of ``common_tools``, read from its source.

        The function's one ``return`` is read like a ``Tool(...)`` in the project, in the file of
        Pydantic AI that defines it. The tool must be named there; its location is the call.
        """
        if self.installed is None or not callee.name.startswith("pydantic_ai.") or callee.last[:1].isupper():
            return None
        function = self.installed.function(callee.name, self.resolver)
        if function is None or len(function.body.returns) != 1:
            return None
        returned = function.body.returns[0]
        target = self.resolver.resolve(returned, function.body, END_OF_MODULE)
        if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
            return None
        made = self.resolver.callee(target.node, target.scope, target.line)
        named = next((item.value for item in target.node.keywords if item.arg == "name"), None)
        if not isinstance(made, External) or not made.is_library("Tool") or named is None:
            return None
        if string(self.resolver, named, target.scope, target.line) is None:
            return None  # named by an argument of the call: not something to read off the page
        spec = self.tool_of(returned, function.body, END_OF_MODULE, 1)
        if spec is None:
            return None
        spec.where = where
        spec.made_by = callee.name
        spec.library_version = self.installed.version
        return spec

    def installed_class(self, name: str) -> Definition | None:
        """The ``class`` of Pydantic AI that the dotted ``name`` stands for, if its installed source has one."""
        for _ in range(4):  # through re-exports, as ``InstalledPydanticAI.function`` follows them
            dotted, _, attribute = name.rpartition(".")
            module = None if self.installed is None else self.installed.module(dotted)
            found = None if module is None else self.resolver.member(module, attribute)
            if isinstance(found, Definition):
                return found if isinstance(found.node, ast.ClassDef) else None
            if not isinstance(found, External) or found.name == name:
                return None
            name = found.name
        return None

    def _method_tool(self, target: Expression) -> _ToolSpec | None:
        """A tool that is a method of an object made in the analysed source: ``db.query`` with ``db = Database()``."""
        attribute = target.node
        assert isinstance(attribute, ast.Attribute)
        owner = self.resolver.resolve(attribute.value, target.scope, target.line)
        if not isinstance(owner, Expression) or not isinstance(owner.node, ast.Call):
            return None
        made = self.resolver.callee(owner.node, owner.scope, owner.line)
        if not isinstance(made, Definition) or not isinstance(made.node, ast.ClassDef):
            return None
        found = made.body.bindings.get(attribute.attr)
        method = found[-1] if found else None
        if (
            method is None
            or method.kind != "def"
            or not isinstance(method.node, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            return None
        function = Definition(method.node, made.body)
        return _ToolSpec(
            method.node.name, function, location(made.module, method.node), takes_ctx=self._takes_ctx(function)
        )

    def items(
        self, node: ast.expr, scope: Scope, line: int, depth: int = 0, *, until: tuple[Scope, int] | None = None
    ) -> list[Expression] | None:
        """The entries of a list written in source; ``None`` when what the list holds cannot be read.

        ``*name`` entries are expanded, and so are entries added afterwards with
        ``append``, ``extend`` and ``insert``. Where an entry depends on a condition
        (``[a] if flag else []``, an ``append`` inside an ``if``), it is included and
        ``conditional`` is set, for the caller to say so. Where ``extend`` adds
        entries that cannot be read, the others are returned and the statement is
        noted in ``added_unread``. A list that is changed in any other way is not
        read at all. ``until`` is where the list is used, when
        that is further down than ``line``: what is added up to there counts too.
        """
        if depth > _MAX_EXPANSION_DEPTH:
            return None
        written = unwrap(node)
        if isinstance(written, ast.IfExp | ast.BoolOp):
            return self._either(written, scope, line, depth)
        target = self.resolver.resolve(node, scope, line)
        changes = self.resolver.changed(until=until)
        if not isinstance(target, Expression):
            return None
        if self.resolver.reassigned():
            self.conditional = True  # the list found is the one assigned last above the use, maybe not the one used
        if isinstance(target.node, ast.IfExp | ast.BoolOp):
            found: list[Expression] | None = self._either(target.node, target.scope, target.line, depth)
        elif isinstance(target.node, ast.List | ast.Tuple | ast.Set) and len(target.node.elts) <= _MAX_LIST_ITEMS:
            found = []
            for item in target.node.elts:
                if isinstance(item, ast.Starred):
                    inner = self.items(item.value, target.scope, target.line, depth + 1)
                    if inner is None:
                        return None
                    found.extend(inner)
                else:
                    found.append(Expression(item, target.scope, target.line))
        else:
            return None
        if found is None:
            return None
        for change in changes:
            call = None if change.elsewhere else change.call
            plain = call is not None and not call.keywords and not any(isinstance(a, ast.Starred) for a in call.args)
            if call is not None and plain and change.method == "append" and len(call.args) == 1:
                found.append(Expression(call.args[0], change.scope, change.line))
            elif call is not None and plain and change.method == "insert" and len(call.args) == 2:
                found.append(Expression(call.args[1], change.scope, change.line))
            elif call is not None and plain and change.method == "extend" and len(call.args) == 1:
                more = self.items(call.args[0], change.scope, change.line, depth + 1)
                if more is None:
                    # More entries, which only the running code knows. Those that are written out still hold.
                    self.added_unread.append(clip_expression(change.scope.module.text(call), 80))
                    continue
                found.extend(more)
            else:
                return None  # removed from, sorted, replaced item by item: not something to read off the page
            if change.conditional:
                self.conditional = True
        return found if len(found) <= _MAX_LIST_ITEMS else None

    def _either(self, node: ast.IfExp | ast.BoolOp, scope: Scope, line: int, depth: int) -> list[Expression] | None:
        """The entries of ``[a] if flag else [b]`` or ``given or [a]``: those of every alternative."""
        if isinstance(node, ast.BoolOp) and not isinstance(node.op, ast.Or):
            return None
        if isinstance(node, ast.IfExp):
            try:
                holds = literal(self.resolver, node.test, scope, line)
            except NotLiteral:
                pass
            else:  # a condition the source spells out picks its case
                branch = node.body if holds else node.orelse
                if isinstance(branch, ast.Constant) and branch.value is None:
                    return []
                return self.items(branch, scope, line, depth + 1)
        branches = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.values
        found: list[Expression] = []
        for branch in branches:
            if isinstance(branch, ast.Constant) and branch.value is None:
                continue
            entries = self.items(branch, scope, line, depth + 1)
            if entries is None:
                return None
            found.extend(entries)
        self.conditional = True
        return found

    def take_conditional(self) -> bool:
        """Whether a list read since the last call had entries that depend on a condition."""
        conditional, self.conditional = self.conditional, False
        return conditional

    def take_added_unread(self) -> list[str]:
        """The statements that added unreadable entries to the lists read since the last call."""
        unread, self.added_unread = list(dict.fromkeys(self.added_unread)), []
        return unread


class _AgentBuilder:
    """Turns one ``_AgentSite`` into an ``AgentBlueprint``."""

    def __init__(self, extractor: AgentExtractor, agent: _AgentSite, ids: dict[tuple[int, int], str]) -> None:
        self.extractor = extractor
        self.resolver = extractor.resolver
        self.agent = agent
        self.ids = ids
        self.call = agent.site.node
        self.scope = agent.site.scope
        self.module = agent.module
        self.line = self.call.lineno
        self.warnings: list[str] = []
        for note in agent.notes:
            self.warn(note)
        self.arguments: dict[str, ast.expr] = {}
        self._spread_from: dict[int, tuple[Scope, int]] = {}
        """``id(node)`` of an argument taken out of a ``**dict`` -> the scope and line it is written at."""
        self._owned_toolsets: list[list[str]] = []
        """Filled by ``_toolsets``: for each capability entry, the ids of the toolsets it gives the agent."""
        self._spec: dict[str, ast.expr] = {}
        """``Agent.from_spec``/``from_file``: what the spec gives that the call's arguments add to, not replace."""
        self._spec_scope: Scope | None = None
        method = spec_method(self.resolver.callee(self.call, self.scope, self.line))
        if method is None and self.call.args and not isinstance(self.call.args[0], ast.Starred):
            self.arguments["model"] = self.call.args[0]
        spread: list[ast.expr] = []
        for keyword in self.call.keywords:
            if keyword.arg is None:
                spread.append(keyword.value)
            else:
                self.arguments[keyword.arg] = keyword.value
        for node in spread:
            self._spread(node)
        if any(isinstance(argument, ast.Starred) for argument in self.call.args):
            self.warn("arguments passed with `*` cannot be read from source")
        if method is not None:
            self._read_spec(method)

    def _read_spec(self, method: str) -> None:
        """Take the arguments ``Agent.from_spec`` makes of its spec, as it takes them.

        A keyword of the call replaces the field of the spec of the same name, unless it is ``None``;
        instructions, capabilities, model settings and retries of the call are added to those of the spec.
        """
        given = self.call.args[0] if self.call.args and not isinstance(self.call.args[0], ast.Starred) else None
        given = given or self.arguments.pop("spec" if method == "from_spec" else "path", None)
        if given is None:
            self.warn(f"the spec given to `Agent.{method}` cannot be read from source")
            return
        text = brief(self.text(given))
        if method == "from_file":
            found = self._spec_file(given)
            if found is None:
                return
            path, fmt = found
            spec, warning = load_spec(path, fmt)
            project = self.extractor.project
            rel, line = shown(path, project.root), 1
        else:
            try:
                spec = self.literal(given)
            except NotLiteral:
                spec = None
            warning = (
                None
                if isinstance(spec, dict)
                else f"the agent spec is built by `{text}`; it is read when the code runs"
            )
            path, rel, line = self.module.path, self.module.rel, self.line
        if warning is not None or not isinstance(spec, dict):
            self.warn(warning or f"the agent spec `{text}` cannot be read")
            return
        written, warnings = spec_source(cast("dict[str, Any]", spec), self._spec_classes(), line)
        for message in warnings:
            self.warn(message)
        module = parse_module(written.encode("utf-8"), path, rel)
        if isinstance(module, SkippedFile):
            self.warn(f"the agent spec `{rel}` is nested too deeply to read")
            return
        statement = module.scope.node.body[-1] if isinstance(module.scope.node, ast.Module) else None
        made = statement.value if isinstance(statement, ast.Expr) else None
        if not isinstance(made, ast.Call):
            return
        self._spec_scope = module.scope
        for keyword in made.keywords:
            if keyword.arg is None:
                continue
            self._spread_from[id(keyword.value)] = (module.scope, line)
            if keyword.arg in ("instructions", "capabilities", "model_settings", "retries"):
                self._spec[keyword.arg] = keyword.value
            else:
                current = self.arguments.get(keyword.arg)
                if current is None or (isinstance(current, ast.Constant) and current.value is None):
                    self.arguments[keyword.arg] = keyword.value

    def _spec_file(self, given: ast.expr) -> tuple[Path, str] | None:
        """The spec file ``Agent.from_file`` reads and its format, when it is in the analysed directories."""
        name = string(self.resolver, given, *self.where(given))
        if name is None:
            self.warn(f"the agent spec file is named by `{brief(self.text(given))}`; it is read when the code runs")
            return None
        fmt_node = self.arguments.pop("fmt", None)
        fmt = None if fmt_node is None else string(self.resolver, fmt_node, *self.where(fmt_node))
        fmt = fmt or FORMATS.get(Path(name).suffix.lower())
        if fmt not in ("json", "yaml"):
            self.warn(f"the format of the agent spec `{name}` cannot be told from its name")
            return None
        project = self.extractor.project
        bases = [base.resolve() for base in project.bases] or [project.root.resolve()]
        # A run reads it relative to its working directory: most often where the code is, or the project.
        candidates = [Path(name)] if Path(name).is_absolute() else [self.module.path.parent / name, project.root / name]
        outside = False
        for candidate in candidates:
            path = contained(candidate, bases)
            if path is not None and path.is_file():
                if path.stat().st_size > self.extractor.max_file_bytes:
                    self.warn(
                        f"the agent spec `{name}` is larger than the {self.extractor.max_file_bytes} byte limit of the "
                        "analysis, so it is not read"
                    )
                    return None
                return path, fmt
            outside = outside or (path is None and candidate.is_file())
        if outside:
            self.warn(f"the agent spec `{name}` is outside the analysed directories, so it is not read")
        else:
            self.warn(f"the agent spec `{name}` was not found in the analysed files")
        return None

    def _spec_classes(self) -> dict[str, str]:
        """The capability names a spec may use, as ``from_spec`` looks them up: its own, and those it is given."""
        classes = registry()
        node = self.arguments.pop("custom_capability_types", None)
        if node is None:
            return classes
        entries = self._listed(node, *self.where(node), "capability types")
        if entries is None:
            self.warn(f"the custom capability types are given by `{brief(self.text(node))}`, which is not read")
            return classes
        for entry in entries:
            found = self.resolver.resolve(entry.node, entry.scope, entry.line)
            if isinstance(found, External):
                classes[found.last] = found.name
            elif (
                isinstance(found, Definition)
                and isinstance(found.node, ast.ClassDef)
                and "." not in found.body.qualname
            ):
                classes[found.node.name] = f"{found.module.name}.{found.node.name}"
            else:
                self.warn(f"a custom capability type is `{brief(entry.module.text(entry.node))}`, which is not read")
        return classes

    def warn(self, message: str) -> None:
        _compat.add_warning(self.warnings, message)

    def where(self, node: ast.expr) -> tuple[Scope, int]:
        """The scope and the line an argument is evaluated at: the call's, unless it came out of a ``**dict``."""
        return self._spread_from.get(id(node), (self.scope, self.line))

    def text(self, node: ast.AST) -> str:
        scope, _ = self._spread_from.get(id(node), (self.scope, self.line))
        return scope.module.text(node)

    def literal(self, node: ast.expr) -> Any:
        """The value an argument spells out; ``NotLiteral`` propagates."""
        return literal(self.resolver, node, *self.where(node))

    def _spread(self, node: ast.expr) -> None:
        """Take the arguments out of ``**name``, where ``name`` is a dict that the source writes out.

        ``kwargs = {"model": model}``, then ``kwargs["tools"] = tools`` under some
        condition, then ``Agent(**kwargs)``: the keys are the arguments. A key that
        is only set under a condition is taken as set, and said to depend on it.
        """
        text = brief(self.module.text(node))
        target = self.resolver.resolve(node, self.scope, self.line)
        changes = self.resolver.changed()
        written = self._written_dict(target) if isinstance(target, Expression) else None
        if written is None or not isinstance(target, Expression):
            self.warn(f"arguments passed as `**{text}` cannot be read from source")
            return
        found: dict[str, tuple[ast.expr, Scope, int]] = {}
        unread = False
        for key, value in written:
            name = key if isinstance(key, str) else None
            if isinstance(key, ast.expr):
                name = string(self.resolver, key, target.scope, target.line)
            if name is None:
                unread = True  # a computed key, or another dict spread into this one
            else:
                found[name] = (value, target.scope, target.line)
        conditional: list[str] = []
        replaced: list[str] = []
        for change in changes:
            pairs = None if change.elsewhere else self._set_by(change)
            if pairs is None:
                unread = True
                continue
            for name, value in pairs:
                if change.method == "setdefault" and name in found:
                    continue
                if change.conditional and name in found and name not in conditional:
                    replaced.append(name)  # the value first given stays: it is the one given in every case
                    continue
                if change.conditional and name not in conditional:
                    conditional.append(name)
                elif name in conditional and not change.conditional:
                    conditional.remove(name)
                found.setdefault(name, (value, change.scope, change.line))
                if not change.conditional:
                    found[name] = (value, change.scope, change.line)
        for name, (value, scope, line) in found.items():
            if name not in self.arguments:  # one written out in the call itself comes first
                self.arguments[name] = value
                self._spread_from[id(value)] = (scope, line)
        replaced = list(dict.fromkeys(replaced))
        if conditional:
            names = " and ".join(f"`{name}`" for name in conditional)
            verb = "is" if len(conditional) == 1 else "are"
            self.warn(f"in `**{text}`, {names} {verb} only set under a condition, and shown as if it held")
        if replaced:
            names = " and ".join(f"`{name}`" for name in replaced)
            verb = "is" if len(replaced) == 1 else "are"
            self.warn(f"in `**{text}`, {names} {verb} replaced under a condition; what was given first is shown")
        if unread:
            self.warn(f"some of the arguments passed as `**{text}` cannot be read from source")

    def _written_dict(self, target: Expression) -> list[tuple[ast.expr | str | None, ast.expr]] | None:
        """The keys and values of ``{"k": v}`` or ``dict(k=v)``; a key is ``None`` for ``**other``."""
        node = target.node
        if isinstance(node, ast.Dict) and len(node.keys) <= _MAX_LIST_ITEMS:
            return list(zip(node.keys, node.values, strict=True))
        if isinstance(node, ast.Call) and not node.args and len(node.keywords) <= _MAX_LIST_ITEMS:
            callee = self.resolver.callee(node, target.scope, target.line)
            if isinstance(callee, External) and callee.name == "builtins.dict":
                return [(keyword.arg, keyword.value) for keyword in node.keywords]
        return None

    def _set_by(self, change: Mutation) -> list[tuple[str, ast.expr]] | None:
        """The keys a statement sets in a dict, with their values; ``None`` when that cannot be read."""
        if change.method == "[]":
            if change.key is None or change.value is None:
                return None  # ``del kwargs[key]``, ``kwargs[key] += 1``
            name = string(self.resolver, change.key, change.scope, change.line)
            return None if name is None else [(name, change.value)]
        call = change.call
        if call is None or any(isinstance(argument, ast.Starred) for argument in call.args):
            return None
        if change.method == "setdefault" and len(call.args) == 2 and not call.keywords:
            name = string(self.resolver, call.args[0], change.scope, change.line)
            return None if name is None else [(name, call.args[1])]
        if change.method != "update" or len(call.args) > 1:
            return None
        pairs: list[tuple[str, ast.expr]] = []
        if call.args:
            given = call.args[0]
            if not isinstance(given, ast.Dict) or len(given.keys) > _MAX_LIST_ITEMS:
                return None
            for key, value in zip(given.keys, given.values, strict=True):
                name = None if key is None else string(self.resolver, key, change.scope, change.line)
                if name is None:
                    return None
                pairs.append((name, value))
        for keyword in call.keywords:
            if keyword.arg is None:
                return None
            pairs.append((keyword.arg, keyword.value))
        return pairs

    def _listed(self, node: ast.expr, scope: Scope, line: int, what: str) -> list[Expression] | None:
        """The entries of a list argument, with a warning when some of them depend on a condition."""
        # A list that came out of a ``**dict`` is the same list when the call is made, further down.
        until = (self.scope, self.line) if id(node) in self._spread_from and scope is self.scope else None
        entries = self.extractor.items(node, scope, line, until=until)
        if self.extractor.take_conditional() and entries is not None:
            text = brief(scope.module.text(node))
            self.warn(f"some of the {what} in `{text}` depend on a condition; those of every case are shown")
        for statement in self.extractor.take_added_unread():
            if entries is not None:
                self.warn(f"more {what} are added by `{statement}`; they are only known when the code runs")
        return entries

    # --------------------------------------------------------------- build

    def build(self) -> AgentBlueprint:
        blueprint_id = self.ids[self.agent.key]
        name = self._text_argument("name")
        toolsets, tools, delegates = self._toolsets()
        via = self.agent.via
        bound_to = (via.bound_to if via is not None else None) or self.agent.site.bound_to
        built = AgentBlueprint(
            id=blueprint_id,
            name=name,
            label=name or bound_to or f"agent-{blueprint_id[1:]}",
            description=self._text_argument("description"),
            source="code",
            location=location(self.module, self.call),
            model=self._model(),
            deps_type=self._deps_type(),
            instructions=self._instructions(),
            toolsets=toolsets,
            tools=tools,
            native_tools=self._native_tools(),
            output=self._output(),
            capabilities=self._capabilities(),
            settings=self._settings(),
            delegates=[*delegates, *(d for d in self._sub_agent_delegates() if d not in delegates)],
            wrapper=self.extractor.durable.get(self.agent.key),
            warnings=self.warnings,
        )
        # The provider-side tools the capabilities give the agent, as a live agent lists them: after its own.
        for capability in built.capabilities:
            for kind in capability.native_tools:
                if kind not in built.native_tools:
                    built.native_tools.append(kind)
        return built

    def _text_argument(self, key: str) -> str | None:
        node = self.arguments.get(key)
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        value = string(self.resolver, node, *self.where(node))
        if value is None:
            shown = self._as_assigned_to(Expression(node, *self.where(node)))
            text = brief(shown.module.text(shown.node))
            self.warn(f"the {key} is computed by `{text}`, so it is only known when the code runs")
        return value or None

    def _model(self) -> ModelInfo | None:
        node = self.arguments.get("model")
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        value = string(self.resolver, node, *self.where(node))
        if value is not None:
            provider, separator, name = value.partition(":")
            if not separator:
                provider, name = None, value
            offline = provider in _compat.OFFLINE_PROVIDERS
            return ModelInfo(
                id=value, name=name, provider=provider, offline=offline, expression=None, model_class=None, env=None
            )
        text = self.text(node)
        name: str | None = None
        provider = None
        target = self.resolver.resolve(node, *self.where(node))
        if self.resolver.reassigned():
            target = None  # assigned in more than one place: which of the values it has is decided at run time
        if isinstance(unwrap(node), ast.Name | ast.Attribute) and isinstance(target, Expression):
            # ``Agent(model)``: what ``model`` was assigned says more than its name does.
            text = target.module.text(target.node)
        read = environment(self.resolver, node, *self.where(node))
        if read is not None:
            # ``os.getenv("MODEL", "openai:gpt-5.2")``: the environment decides; the default is only a default.
            self.warn(f"the model comes from {_environment_words(read)}")
            return ModelInfo(
                id=None, name=text, provider=None, offline=False, expression=text, model_class=None, env=_env(read)
            )
        if isinstance(target, Expression) and isinstance(target.node, ast.JoinedStr):
            # ``f"openai:{settings.model}"``: the provider is spelled out, the name is computed.
            written, separator, rest = fstring_template(target.node).partition(":")
            first = target.node.values[0] if target.node.values else None
            spelled_out = isinstance(first, ast.Constant) and str(first.value).startswith(f"{written}:")
            if separator and rest and spelled_out:
                self.warn(
                    f"the model name is computed by `{brief(text)}`, so it is only known when the code runs; "
                    f"the provider `{written}` is what the source gives"
                )
                offline = written in _compat.OFFLINE_PROVIDERS
                return ModelInfo(
                    id=None,
                    name=rest,
                    provider=written,
                    offline=offline,
                    expression=text,
                    model_class=None,
                    env=None,
                )
        model_class: str | None = None
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            callee = self.resolver.callee(target.node, target.scope, target.line)
            if isinstance(callee, External) and callee.is_library(*_OFFLINE_MODELS):
                provider = _OFFLINE_MODELS[callee.last]
                model_class = callee.last
            elif isinstance(callee, External) and callee.name.startswith("pydantic_ai.models."):
                # One of Pydantic AI's model classes: ``OpenAIChatModel("gpt-5.2", provider=...)`` names its model.
                model_class = callee.last if callee.last[:1].isupper() else None
                call = target.node
                spelled = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
                spelled = spelled or next((item.value for item in call.keywords if item.arg == "model_name"), None)
                if spelled is not None:
                    name = string(self.resolver, spelled, target.scope, target.line)
                read = (
                    None
                    if spelled is None or name is not None
                    else environment(self.resolver, spelled, target.scope, target.line)
                )
                if read is not None and model_class is not None:
                    # ``OpenAIChatModel(os.getenv("MODEL", "gpt-5.2"))``, perhaps from a helper function.
                    self.warn(f"the model name of `{model_class}` comes from {_environment_words(read)}")
                    return ModelInfo(
                        id=None,
                        name=text,
                        provider=None,
                        offline=False,
                        expression=text,
                        model_class=model_class,
                        env=_env(read),
                    )
        if provider is None and name is None:
            self.warn(f"the model is set by `{brief(text)}`, so it is only known when the code runs")
        return ModelInfo(
            id=None,
            name=name or text,
            provider=provider,
            offline=provider is not None,
            expression=text,
            model_class=model_class,
            env=None,
        )

    def _deps_type(self) -> str | None:
        node = self.arguments.get("deps_type")
        if node is None:
            return None
        target = self.resolver.resolve(node, *self.where(node))
        if isinstance(target, External):
            return (
                None if target.name in ("builtins.object", "builtins.None") else target.name.removeprefix("builtins.")
            )
        if isinstance(target, Definition):
            return target.qualified_name
        if isinstance(node, ast.Constant) and node.value is None:
            return None
        text = self.text(node)
        if not isinstance(node, ast.Subscript | ast.BinOp):  # ``dict[str, int]``, ``Deps | None``: a type as written
            self.warn(f"the deps type is set by `{brief(text)}`, so it is only known when the code runs")
        return text

    # -------------------------------------------------------- instructions

    def _instructions(self) -> list[Instruction]:
        """In the order Pydantic AI keeps them: instructions, then system prompts; arguments before decorators."""
        return [
            *self._prompts("instructions"),
            *self.agent.instructions,
            *self._prompts("system_prompt"),
            *self.agent.system_prompts,
        ]

    def _prompts(self, kind: PromptKind) -> list[Instruction]:
        found: list[Instruction] = []
        for node in (self._spec.get(kind), self.arguments.get(kind)):  # a spec's come first
            if node is None:
                continue
            what = "instructions" if kind == "instructions" else "system prompts"
            entries = self._listed(node, *self.where(node), what)
            if entries is None:
                entries = [Expression(node, *self.where(node))]
            prompts = (self._prompt(kind, entry) for entry in entries)
            found.extend(prompt for prompt in prompts if prompt is not None)
        return found

    def _prompt(self, kind: PromptKind, entry: Expression) -> Instruction | None:
        node = entry.node
        where = location(entry.module, node)
        if isinstance(node, ast.Constant) and node.value is None:
            return None
        value = string(self.resolver, node, entry.scope, entry.line)
        if value is not None:
            # A spec's text with ``{{name}}`` in it is a template, filled in from the deps of each run.
            dynamic = entry.scope is self._spec_scope and "{{" in value
            return Instruction(kind=kind, dynamic=dynamic, text=value, function=None, expression=None, location=where)
        target = self.resolver.resolve(node, entry.scope, entry.line)
        if isinstance(target, Definition) and not isinstance(target.node, ast.ClassDef):
            return Instruction(
                kind=kind,
                dynamic=True,
                text=None,
                function=target.qualified_name,
                expression=None,
                location=location(target.module, target.node),
            )
        what = "instructions" if kind == "instructions" else "system prompts"
        if isinstance(target, Expression):
            # ``f"Today is {today()}."`` or ``PROMPT.format(date=today())``: the text, placeholders left in.
            # It is shown whole, as the template it is, so it needs no warning.
            template = (
                fstring_template(target.node, self._reader(target))
                if isinstance(target.node, ast.JoinedStr)
                else format_template(self.resolver, target.node, target.scope, target.line)
            )
            if template is not None:
                return Instruction(
                    kind=kind, dynamic=True, text=template, function=None, expression=None, location=where
                )
        # The source as written, line breaks and all: a prompt built by a call is still readable in it.
        shown = self._as_assigned_to(entry)
        text = shown.module.raw_text(shown.node)
        if len(text) > _MAX_VERBATIM_CHARS:
            text = text[: _MAX_VERBATIM_CHARS - 3] + "..."
        if isinstance(target, External):
            self.warn(
                f"one of the {what} is `{brief(text)}` ({target.name}), which is defined outside the analysed "
                "source; whether it is text or a function is not known"
            )
        elif not isinstance(node, ast.Lambda):
            self.warn(f"one of the {what} is the expression `{brief(text)}`; its text is only known when the code runs")
        return Instruction(kind=kind, dynamic=True, text=None, function=None, expression=text, location=where)

    def _reader(self, context: Expression) -> Any:
        """What ``literal`` reads of an expression in ``context``; raises ``NotLiteral`` as it does."""

        def read(node: ast.expr) -> Any:
            return literal(self.resolver, node, context.scope, context.line)

        return read

    # -------------------------------------------------------------- output

    def _output(self) -> OutputInfo:
        validators = self.agent.output_validators
        # ``result_type`` is what ``output_type`` was called in early releases; code written then is read too.
        node = self.arguments.get("output_type", self.arguments.get("result_type"))
        if node is None:
            return OutputInfo(
                mode="text",
                allows_text=True,
                type_name="str",
                json_schema={"type": "string"},
                tool_names=[],
                validators=validators,
                location=None,
            )
        builder = SchemaBuilder(self.resolver)
        kinds: list[str] = []
        labels: list[str] = []
        schemas: list[dict[str, Any]] = []
        self._output_member(Expression(node, *self.where(node)), builder, kinds, labels, schemas, 0)
        mode = _output_mode(kinds)
        unique: list[dict[str, Any]] = []
        for schema in schemas:
            if schema not in unique:
                unique.append(schema)
        document: dict[str, Any] | None = None
        if unique:
            document = builder.document(unique[0] if len(unique) == 1 else {"anyOf": unique})
        if builder.unresolved:
            names = ", ".join(f"`{name}`" for name in builder.unresolved)
            self.warn(
                f"the output schema is incomplete: {names} could not be resolved from the analysed source"
                if document is not None and set(document) != {"x-python-type"}
                # Not "defined outside": a class of the project derived from another library's class lands here too.
                else f"the schema of the output type {names} cannot be read from the analysed source, "
                "so it is not shown"
            )
        for note in builder.notes:
            self.warn(f"in the output schema, {note}")
        if document is not None and set(document) == {"x-python-type"}:
            document = None
        where = builder.locations[0] if builder.locations else None
        return OutputInfo(
            mode=mode,
            # Pydantic AI reads a text answer in every mode but ``tool``, which needs ``str`` among the outputs.
            allows_text=None if mode == "unknown" else mode != "tool" or "text" in kinds,
            type_name=" | ".join(labels) or None,
            json_schema=document,
            tool_names=[],
            validators=validators,
            location=None if where is None else location(where.module, where.node),
        )

    def _output_member(
        self,
        entry: Expression,
        builder: SchemaBuilder,
        modes: list[str],
        labels: list[str],
        schemas: list[dict[str, Any]],
        depth: int,
    ) -> None:
        """Read one output spec: a type, a function, a marker such as ``ToolOutput(...)``, or a list of them."""
        node = entry.node
        target = self.resolver.resolve(node, entry.scope, entry.line)
        if depth < 4 and isinstance(target, Expression):
            inner = target.node
            if isinstance(inner, ast.List | ast.Tuple) and not any(
                isinstance(item, ast.Starred) for item in inner.elts
            ):
                for item in inner.elts[:_MAX_LIST_ITEMS]:
                    self._output_member(
                        Expression(item, target.scope, target.line), builder, modes, labels, schemas, depth + 1
                    )
                return
            if isinstance(inner, ast.Call):
                callee = self.resolver.callee(inner, target.scope, target.line)
                if isinstance(callee, External) and callee.is_library("StructuredDict"):
                    # A dict the model writes to the JSON Schema it is given, as ``output_schema`` of a spec gives.
                    given = inner.args[0] if inner.args and not isinstance(inner.args[0], ast.Starred) else None
                    given = next((k.value for k in inner.keywords if k.arg == "json_schema"), given)
                    try:
                        schema = None if given is None else literal(self.resolver, given, target.scope, target.line)
                    except NotLiteral:
                        schema = None
                    if isinstance(schema, dict):
                        labels.append("_StructuredDict")  # the name of the class it makes, as a live agent shows it
                        modes.append("auto")
                        schemas.append(cast("dict[str, Any]", schema))
                        return
                if isinstance(callee, External) and callee.is_library(*_OUTPUT_MODES):
                    wrapped = inner.args[0] if inner.args and not isinstance(inner.args[0], ast.Starred) else None
                    keyword = _OUTPUT_WRAPS[callee.last]
                    wrapped = next((k.value for k in inner.keywords if k.arg == keyword), wrapped)
                    if wrapped is not None and callee.last == "TextOutput":
                        # The model answers in text, and the function turns that text into the output.
                        function = self.resolver.resolve(wrapped, target.scope, target.line)
                        named = isinstance(function, Definition)
                        labels.append(function.node.name if named else target.module.text(wrapped))
                        modes.append("text")
                        schemas.append({"type": "string"})
                        return
                    if wrapped is not None:
                        before = len(modes)
                        self._output_member(
                            Expression(wrapped, target.scope, target.line), builder, modes, labels, schemas, depth + 1
                        )
                        modes[before:] = [_OUTPUT_MODES[callee.last]] * (len(modes) - before)
                        return
        if isinstance(target, Definition) and not isinstance(target.node, ast.ClassDef):
            # An output function: the model calls it like a tool, and its result is the output.
            labels.append(target.node.name)
            modes.append("auto")
            return
        if isinstance(target, External) and target.is_library(*NOT_FROM_THE_MODEL):
            # What a run ends with when a tool call is deferred. The model is never asked to write it.
            labels.append(target.last)
            return
        if isinstance(target, External) and target.name == "builtins.str":
            labels.append("str")
            modes.append("text")
            schemas.append({"type": "string"})
            return
        # A name for a type expression (``Answer = Person | Point``) is shown as what it stands for.
        shown = target if isinstance(target, Expression) and isinstance(node, ast.Name) else entry
        labels.append(shown.module.text(shown.node))
        modes.extend(self._union_kinds(entry, 0))
        schemas.append(builder.annotation(node, entry.scope, entry.line))

    def _union_kinds(self, entry: Expression, depth: int) -> list[str]:
        """What each member of an output type is to Pydantic AI, which reads a union as a list of its members.

        ``"text"`` for ``str``, ``"none"`` for ``None`` and ``"auto"`` for any other type.
        """
        node = entry.node
        target = self.resolver.resolve(node, entry.scope, entry.line)
        if isinstance(target, Expression) and target.node is not node and depth < 4:
            return self._union_kinds(target, depth + 1)
        if isinstance(node, ast.Constant) and node.value is None:
            return ["none"]
        if isinstance(target, External) and target.name in ("builtins.str", "types.NoneType"):
            return ["text" if target.name == "builtins.str" else "none"]
        members: list[ast.expr] = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            members = [node.left, node.right]
        elif isinstance(node, ast.Subscript):
            construct = typing_construct(self.resolver.resolve(node.value, entry.scope, entry.line))
            arguments = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if construct == "Union":
                members = arguments
            elif construct == "Optional":
                members = [*arguments[:1], ast.Constant(None)]
        if not members or depth >= 4:
            return ["auto"]
        return [
            kind
            for member in members[:_MAX_LIST_ITEMS]
            for kind in self._union_kinds(Expression(member, entry.scope, entry.line), depth + 1)
        ]

    # -------------------------------------------------------- capabilities

    @cached_property
    def _capability_entries(self) -> list[Expression]:
        """The entries of ``capabilities=[...]``, read once for the capabilities and for the toolsets they give.

        A spec's come first, then those the call adds to them.
        """
        found: list[Expression] = []
        for node in (self._spec.get("capabilities"), self.arguments.get("capabilities")):
            if node is None or (isinstance(node, ast.Constant) and node.value is None):
                continue
            entries = self._listed(node, *self.where(node), "capabilities")
            if entries is None:
                self.warn(
                    f"the capabilities are built by `{brief(self.text(node))}`; they are only known when the code runs"
                )
                continue
            found.extend(self._opened(entries, 0))
        # The viewer's own capability, which records events and is no part of what the agent is, as live.
        return [entry for entry in found if not _is_capture_events(self._built(entry)[2])]

    def _opened(self, entries: list[Expression], depth: int) -> list[Expression]:
        """The entries with each of Pydantic AI's ``CombinedCapability([...])`` replaced by what it holds.

        The agent does the same, except with one that is loaded on demand: that stays one entry.
        """
        found: list[Expression] = []
        for entry in entries:
            held: list[Expression] | None = None
            if depth < 4:
                target, call, callee, _ = self._built(entry)
                if call is not None and isinstance(target, Expression) and _is_combined(callee):
                    held = None if self._kept_whole(call, target) else self._held(call, target)
            if held is None:
                found.append(entry)
            else:
                found.extend(self._opened(held, depth + 1))
        return found

    def _held(self, call: ast.Call, context: Expression) -> list[Expression] | None:
        """What a ``CombinedCapability([...])`` is given to hold, or ``None`` when that is not spelled out."""
        node = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
        node = next((keyword.value for keyword in call.keywords if keyword.arg == "capabilities"), node)
        if node is None:
            return None
        held = self._listed(node, context.scope, context.line, "capabilities")
        if held is None:
            text = brief(context.module.text(node))
            self.warn(f"the capabilities are built by `{text}`; they are only known when the code runs")
        return held

    def _kept_whole(self, call: ast.Call, context: Expression) -> bool:
        """Whether the agent may keep a ``CombinedCapability`` as one entry, as it does one loaded on demand.

        A computed ``defer_loading=`` may be true, so that one is kept whole too, its expression shown.
        """
        for keyword in call.keywords:
            if keyword.arg == "defer_loading":
                try:
                    return literal(self.resolver, keyword.value, context.scope, context.line) is True
                except NotLiteral:
                    return True
        return False

    def _members(self, call: ast.Call, context: Expression, depth: int) -> list[Expression]:
        """What a ``CombinedCapability`` kept as one entry holds, opened as the agent opens it."""
        held = self._held(call, context) if depth < 4 else None
        return [] if held is None else self._opened(held, depth + 1)

    def _built(self, entry: Expression) -> tuple[Resolved, ast.Call | None, Resolved, str | None]:
        """What a capability entry is: what it resolves to, the call that makes it, the class called, its name.

        ``Thinking.from_spec(effort="high")`` builds a ``Thinking``, so it reads as ``Thinking(effort="high")``.
        """
        target = self.resolver.resolve(entry.node, entry.scope, entry.line)
        call: ast.Call | None = None
        callee: Resolved = target
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            call = target.node
            callee = self.resolver.callee(call, target.scope, target.line)
        if isinstance(callee, External) and callee.last == "from_spec":
            callee = External(callee.name.rpartition(".")[0])
            return target, call, callee, callee.last if callee.is_library_class() else self._class_name(entry)
        return target, call, callee, self._class_name(entry)

    def _member_native_tools(self, entry: Expression, depth: int) -> list[str]:
        """The kinds of provider-side tool a capability that a ``CombinedCapability`` holds gives the agent."""
        target, call, callee, _ = self._built(entry)
        if call is None or not isinstance(target, Expression):
            return []
        if _is_combined(callee):
            return _union(self._member_native_tools(m, depth + 1) for m in self._members(call, target, depth))
        native: bool | None = None
        given = next((keyword.value for keyword in call.keywords if keyword.arg == "native"), None)
        if given is not None:
            try:
                native = bool(literal(self.resolver, given, target.scope, target.line))
            except NotLiteral:
                native = False
        return self._capability_native_tools(self._library_base(callee), call, target, native)

    def _capabilities(self) -> list[CapabilityInfo]:
        found: list[CapabilityInfo] = []
        keys: list[tuple[str, str] | None] = []
        """For each capability, what tells it from others of its class that Pydantic AI would merge it with."""
        defaults: dict[tuple[str, str], dict[str, Any]] = {}
        """For each of those, the defaults its class declares."""
        for index, entry in enumerate(self._capability_entries):
            target, call, callee, name = self._built(entry)
            if name is None and entry.scope is self._spec_scope and isinstance(callee, External):
                name = callee.last  # a spec names its capabilities, known to Pydantic AI or not
            if name is None:
                name = entry.module.text(entry.node)
                built = brief(self._as_assigned(entry))
                self.warn(f"a capability is built by `{built}`; its type is only known when the code runs")
                found.append(
                    CapabilityInfo(
                        name=name,
                        builtin=name in _compat.BUILTIN_CAPABILITIES,
                        category="custom",
                        package=None,
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
                )
                keys.append(None)
                continue
            base = self._library_base(callee)
            details: dict[str, Any] = {}
            expressions: dict[str, str] = {}
            deferred, native = False, None
            native_tools: list[str] | None = None
            capability_id: str | None = None
            skills: list[SkillInfo] = []
            hooks: list[HookInfo] = []
            if call is not None and isinstance(target, Expression):
                deferred, native = self._capability_arguments(callee, call, target, details, expressions)
                capability_id = self._keyword_string(call, "id", target)
                if isinstance(callee, External) and callee.comes_from("pydantic_ai_harness", "Skills"):
                    skills = self._skills(call, target, details, expressions)
                if isinstance(callee, External) and callee.comes_from("pydantic_ai_harness", "SubAgents"):
                    agents = self._sub_agents(call, target) if "agents" in expressions else None
                    if agents is not None:
                        details["agents"] = agents
                        del expressions["agents"]
                if isinstance(callee, External) and callee.comes_from("pydantic_ai", "Hooks"):
                    hooks = self._hooks(call, target, details, expressions)
                if _is_combined(callee):
                    # Kept as one entry: it shows what it holds by class, as the live agent does, and gives
                    # what they give. Their source is not shown, as it may hold a key.
                    members = self._members(call, target, 0)
                    names = [self._built(member)[3] for member in members]
                    if members and None not in names:
                        details["capabilities"] = names
                    native_tools = _union(self._member_native_tools(member, 1) for member in members)
            found.append(
                CapabilityInfo(
                    name=name,
                    builtin=name in _compat.BUILTIN_CAPABILITIES,
                    category=(None if base is None else category_of(base.name, base.last)) or "custom",
                    package=package_of(callee.name) if isinstance(callee, External) else None,
                    deferred=deferred,
                    details=details,
                    expressions=expressions,
                    toolsets=self._owned_toolsets[index],
                    native_tools=(
                        self._capability_native_tools(base, call, target, native)
                        if native_tools is None
                        else native_tools
                    ),
                    capability_id=capability_id,
                    stack=None,
                    skills=skills,
                    hooks=hooks,
                )
            )
            key = self._merge_key(callee, call, target)
            if key is not None and key not in defaults and isinstance(callee, External):
                defaults[key] = self._class_defaults(self.extractor.installed_class(callee.name), 0)
            keys.append(key)
        return _merge_alike(found, keys, defaults)

    def _class_defaults(self, cls: Definition | None, depth: int) -> dict[str, Any]:
        """The fields an installed class of Pydantic AI declares a literal default for, with its bases' fields."""
        if cls is None or not isinstance(cls.node, ast.ClassDef) or depth > 4:
            return {}
        found: dict[str, Any] = {}
        for base in cls.node.bases:
            node = base.value if isinstance(base, ast.Subscript) else base  # ``NativeOrLocalTool[AgentDepsT]``
            resolved = self.resolver.resolve(node, cls.scope, cls.node.lineno)
            if isinstance(resolved, External):
                resolved = self.extractor.installed_class(resolved.name)
            if isinstance(resolved, Definition):
                found.update(self._class_defaults(resolved, depth + 1))
        for statement in cls.node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and isinstance(statement.value, ast.Constant)
            ):
                found[statement.target.id] = statement.value.value
        return found

    def _merge_key(self, callee: Resolved, call: ast.Call | None, target: Resolved) -> tuple[str, str] | None:
        """The class and the id of a capability that Pydantic AI merges with another of that class and id.

        It merges those of a class that declares a default ``id`` in its body; two others with one id are an
        error. A computed id, or a class whose installed source is not read (the harness's), is never merged.
        """
        if call is None or not isinstance(target, Expression) or not isinstance(callee, External):
            return None
        if package_of(callee.name) != "pydantic_ai" or any(keyword.arg is None for keyword in call.keywords):
            return None
        cls = self.extractor.installed_class(callee.name)
        declared: str | None = None
        for statement in [] if cls is None or not isinstance(cls.node, ast.ClassDef) else cls.node.body:
            named = statement.target if isinstance(statement, ast.AnnAssign) else None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                named = statement.targets[0]
            value = statement.value if isinstance(statement, ast.AnnAssign | ast.Assign) else None
            if isinstance(named, ast.Name) and named.id == "id" and isinstance(value, ast.Constant):
                declared = value.value if isinstance(value.value, str) else None
        if declared is None:
            return None
        given = next((keyword.value for keyword in call.keywords if keyword.arg == "id"), None)
        capability_id = declared if given is None else string(self.resolver, given, target.scope, target.line)
        return None if capability_id is None else (callee.last, capability_id)

    def _skills(
        self, call: ast.Call, target: Expression, details: dict[str, Any], expressions: dict[str, str]
    ) -> list[SkillInfo]:
        """The skills a harness ``Skills(...)`` offers, read from the ``SKILL.md`` files of the analysed directories."""
        for key in ("include", "exclude"):
            names = details.get(key)
            if isinstance(names, list):  # sorted and once each, as the live capability holds them in a set
                details[key] = sorted({str(name) for name in cast("list[Any]", names)})
        if "directories" in expressions:
            self.warn(
                f"the skill directories are computed by `{brief(expressions['directories'])}`; the skills are only "
                "known when the code runs"
            )
            return []
        given = details.get("directories")
        directories = [given] if isinstance(given, str) else given
        if isinstance(given, str):
            details["directories"] = directories  # a list, as the live capability holds it
        if not isinstance(directories, list) or not all(
            isinstance(item, str) for item in cast("list[Any]", directories)
        ):
            return []
        directories = cast("list[str]", directories)
        for key in ("include", "exclude"):
            if key in expressions:
                self.warn(
                    f"the skills of `Skills` are chosen by `{brief(expressions[key])}`, which is computed; "
                    "all of them are shown"
                )
        include, exclude = details.get("include"), details.get("exclude")
        project = self.extractor.project
        bases = [base.resolve() for base in project.bases] or [project.root.resolve()]
        module = target.module.path
        home = next((base for base in project.bases if base in module.parents), project.root)
        root = self._skills_root(call, target, home)
        if root is None:
            return []
        if isinstance(root, list):  # the workspace is decided when the code runs: the libraries are looked for
            candidates, root = root, root[0]
            for candidate in candidates:
                found = [(directory, library(candidate, directory, bases)) for directory in directories]
                if any(path is not None for _, path in found):
                    root = candidate
                    for directory, path in found:
                        if path is not None:
                            self.warn(
                                f"`Skills` reads `{directory}` from a workspace decided when the code runs; the skills "
                                f"shown are those in `{shown(path, project.root)}`"
                            )
                    break
        if "scanner" in details or "scanner" in expressions:
            self.warn("skills are shown unscanned; a run scans them first")
        catalog = read_catalog(
            directories,
            root,
            bases=bases,
            shown_from=project.root,
            include=[str(name) for name in cast("list[Any]", include)] if isinstance(include, list) else None,
            exclude=[str(name) for name in cast("list[Any]", exclude)] if isinstance(exclude, list) else [],
            max_file_bytes=self.extractor.max_file_bytes,
        )
        for warning in catalog.warnings:
            self.warn(warning)
        return catalog.skills

    def _sub_agents(self, call: ast.Call, target: Expression) -> list[dict[str, Any]] | None:
        """What each ``SubAgent(...)`` that ``SubAgents(agents=[...])`` holds is given, as the live capability shows
        it: the name of its agent, and its ``name``, ``description`` and ``models``. ``None`` unless all are read."""
        node = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
        node = next((keyword.value for keyword in call.keywords if keyword.arg == "agents"), node)
        listed = None if node is None else self._listed(node, target.scope, target.line, "sub-agents")
        if listed is None:
            return None
        found: list[dict[str, Any]] = []
        for item in listed:
            made = self.resolver.resolve(item.node, item.scope, item.line)
            if not isinstance(made, Expression) or not isinstance(made.node, ast.Call):
                return None
            sub = self.resolver.callee(made.node, made.scope, made.line)
            if not (isinstance(sub, External) and sub.comes_from("pydantic_ai_harness", "SubAgent")):
                return None
            if any(isinstance(argument, ast.Starred) for argument in made.node.args) or any(
                keyword.arg is None for keyword in made.node.keywords
            ):
                return None
            positional = zip(("agent", "name", "description", "models"), made.node.args, strict=False)
            given: dict[str, ast.expr] = dict(positional)
            given.update((keyword.arg, keyword.value) for keyword in made.node.keywords if keyword.arg is not None)
            agent = given.get("agent")
            sites = [] if agent is None else self.extractor.agents_at(agent, made.scope, made.line)
            if len(sites) != 1:
                return None
            shown: dict[str, Any] = {}
            # Read as the sub-agent's own blueprint reads it: from the call, a ``**dict`` or its spec.
            sub_agent = _AgentBuilder(self.extractor, sites[0], self.ids)
            named = sub_agent.arguments.get("name")
            if named is not None and not (isinstance(named, ast.Constant) and named.value is None):
                name = string(self.resolver, named, *sub_agent.where(named))
                if name is None:
                    return None
                shown["agent"] = name
            for key in ("name", "description", "models"):
                if key not in given:
                    continue
                try:
                    value = _detail(literal(self.resolver, given[key], made.scope, made.line))
                except NotLiteral:
                    return None
                if value is not None:
                    shown[key] = value
            found.append(shown)
        return found

    def _hooks(
        self, call: ast.Call, target: Expression, details: dict[str, Any], expressions: dict[str, str]
    ) -> list[HookInfo]:
        """The functions a ``Hooks(...)`` registers: by its keyword arguments, then by the ``@hooks.on.<point>``
        decorators of its module, grouped by point as ``Hooks`` keeps them."""
        by_point: dict[str, list[HookInfo]] = {}
        given = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg in _compat.HOOK_KWARGS}
        for key, point in _compat.HOOK_KWARGS.items():
            details.pop(key, None)
            expressions.pop(key, None)
            node = given.get(key)
            if node is None or (isinstance(node, ast.Constant) and node.value is None):
                continue
            function = self.resolver.resolve(node, target.scope, target.line)
            if not isinstance(function, Definition) or isinstance(function.node, ast.ClassDef):
                self.warn(f"the `{key}` hook of `Hooks` is `{brief(target.module.text(node))}`, which is not read")
                function = None
            by_point.setdefault(point, []).append(self._hook(point, function, [], target.scope, target.line))
        for item in sorted(target.module.decorated, key=lambda decorated: decorated.node.lineno):
            for decorator in item.node.decorator_list:
                method = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not (
                    isinstance(method, ast.Attribute)
                    and method.attr in _compat.HOOK_KWARGS
                    and isinstance(method.value, ast.Attribute)
                    and method.value.attr == "on"
                ):
                    continue
                made = self.resolver.resolve(method.value.value, item.scope, item.node.lineno)
                if not (isinstance(made, Expression) and made.node is call):
                    continue
                point = _compat.HOOK_KWARGS[method.attr]
                keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
                hook = self._hook(point, Definition(item.node, item.scope), keywords, item.scope, item.node.lineno)
                by_point.setdefault(point, []).append(hook)
        return [hook for hooks in by_point.values() for hook in hooks]

    def _hook(
        self, point: str, function: Definition | None, keywords: list[ast.keyword], scope: Scope, line: int
    ) -> HookInfo:
        """One registered hook; ``keywords`` are what its decorator is given (``tools=``, ``timeout=``)."""
        values: dict[str, Any] = {}
        for keyword in keywords:
            if keyword.arg not in ("tools", "timeout"):
                continue
            try:
                values[keyword.arg] = literal(self.resolver, keyword.value, scope, line)
            except NotLiteral:
                name = "a hook" if function is None else f"hook `{function.node.name}`"
                self.warn(
                    f"the `{keyword.arg}=` of {name} is computed by `{brief(scope.module.text(keyword.value))}`; "
                    "it is only known when the code runs"
                )
        tools, timeout = values.get("tools"), values.get("timeout")
        return HookInfo(
            point=point,
            function=None if function is None else function.qualified_name,
            tools=sorted({str(name) for name in cast("list[Any]", tools)}) if isinstance(tools, list | tuple) else None,
            timeout=float(timeout) if isinstance(timeout, int | float) and not isinstance(timeout, bool) else None,
            location=None if function is None else location(function.module, function.node),
        )

    def _skills_root(self, call: ast.Call, target: Expression, home: Path) -> Path | list[Path] | None:
        """Where the libraries of ``Skills(...)`` are: its ``workspace=``, else the agent's ``LocalWorkspace``.

        A list of the places to look in, nearest first, when that is decided when the code runs; ``None`` when they
        are in a workspace that is not on this machine.
        """
        guessed = [target.module.path.parent, home]
        given = next((keyword.value for keyword in call.keywords if keyword.arg == "workspace"), None)
        places: list[tuple[Expression, tuple[str, ...]]] = []
        if given is not None and not (isinstance(given, ast.Constant) and given.value is None):
            places.append(
                (Expression(given, target.scope, target.line), ("LocalWorkspaceBackend", "WindowsWorkspaceBackend"))
            )
        else:
            places.extend((entry, ("LocalWorkspace", "WindowsWorkspace")) for entry in self._capability_entries)
        for place, classes in places:
            made = self.resolver.resolve(place.node, place.scope, place.line)
            if not isinstance(made, Expression) or not isinstance(made.node, ast.Call):
                if classes[0] == "LocalWorkspaceBackend":
                    return guessed
                continue
            callee = self.resolver.callee(made.node, made.scope, made.line)
            if isinstance(callee, External) and callee.comes_from("pydantic_ai", *classes):
                node = made.node.args[0] if made.node.args and not isinstance(made.node.args[0], ast.Starred) else None
                node = next((keyword.value for keyword in made.node.keywords if keyword.arg == "working_dir"), node)
                directory = None if node is None else string(self.resolver, node, made.scope, made.line)
                return guessed if directory is None else home / directory
            if classes[0] == "LocalWorkspaceBackend":
                name = self._class_name(place)
                if name is not None and isinstance(callee, External) and callee.is_library_class():
                    self.warn(f"`Skills` reads its skills from a {name} workspace, which is not read")
                    return None
                return guessed
        return guessed

    def _capability_arguments(
        self, callee: Resolved, call: ast.Call, target: Expression, details: dict[str, Any], expressions: dict[str, str]
    ) -> tuple[bool, bool | None]:
        """Read what a capability is given into ``details`` and ``expressions``.

        Returns whether it is given ``defer_loading=True``, and what it is given as ``native=``:
        ``None`` when nothing, ``False`` when that is computed.
        """
        arguments: list[tuple[str, ast.expr]] = []
        last = callee.last if isinstance(callee, External) else None
        callee_text = brief(target.module.text(call.func))
        if last == "ModelFallback" and call.args:
            # ``ModelFallback(*models)``: the positional arguments together are the chain of models.
            try:
                if any(isinstance(node, ast.Starred) for node in call.args):
                    raise NotLiteral
                models = [literal(self.resolver, node, target.scope, target.line) for node in call.args]
                details["models"] = [_detail(model) for model in models]
            except NotLiteral:
                expressions["models"] = ", ".join(target.module.text(node) for node in call.args)
        else:
            names = _POSITIONAL.get(last, ()) if last is not None else ()
            if isinstance(callee, External) and callee.comes_from("pydantic_ai_harness", *_HARNESS_POSITIONAL):
                names = _HARNESS_POSITIONAL[callee.last]
            for position, node in enumerate(call.args):
                if isinstance(node, ast.Starred):
                    # What it holds, and so the position of what follows, is only known when the code runs.
                    self.warn(f"capability `{callee_text}` is given `*` arguments, which are not shown")
                    break
                if position >= len(names):
                    # A parameter of unknown name may hold a key, so neither its value nor its source is shown.
                    self.warn(f"capability `{callee_text}` is given arguments by position, which are not shown")
                    break
                arguments.append((names[position], node))
        arguments.extend((keyword.arg, keyword.value) for keyword in call.keywords if keyword.arg is not None)
        if any(keyword.arg is None for keyword in call.keywords):
            self.warn(f"capability `{callee_text}` is given `**` arguments, which are not shown")
        deferred = False
        native: bool | None = None
        for key, node in arguments:
            if key == "id" or is_secret(key):
                continue
            if key == "capabilities" and _is_combined(callee):
                continue  # what a CombinedCapability holds is read by the caller, by class
            try:
                value = literal(self.resolver, node, target.scope, target.line)
            except NotLiteral:
                expressions[key] = target.module.text(node)
                if key == "native":
                    native = False
                continue
            if key == "defer_loading":
                deferred = value is True
                continue
            if key == "native":
                native = bool(value)
            shown = _detail(value)
            if shown is not None and shown != [] and shown != {}:
                details[key] = shown
        return deferred, native

    def _capability_native_tools(
        self, base: External | None, call: ast.Call | None, target: Resolved, native: bool | None
    ) -> list[str]:
        """The kinds of provider-side tool one of Pydantic AI's capabilities gives the agent."""
        if base is None or package_of(base.name) != "pydantic_ai":
            return []
        kind = _CAPABILITY_NATIVE_TOOLS.get(base.last)
        if kind is not None:
            return [kind] if native is None or native else []
        if base.last == "MCP":
            return ["mcp_server"] if native else []  # off unless it is asked for
        if base.last == "NativeTool" and call is not None and isinstance(target, Expression):
            tool = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
            tool = next((keyword.value for keyword in call.keywords if keyword.arg == "tool"), tool)
            name = None if tool is None else self._class_name(Expression(tool, target.scope, target.line))
            native_kind = None if name is None else _NATIVE_TOOLS.get(name)
            return [] if native_kind is None else [native_kind]
        return []

    def _capability_toolsets(
        self, entry: Expression, depth: int = 0, merged: tuple[Expression, ...] = ()
    ) -> list[_ToolsetEntry]:
        """The toolset a capability gives the agent: for a class of the project, what its ``get_toolset`` returns.

        Pydantic AI wraps that toolset in a ``CapabilityOwnedToolset``, so it is shown inside one.
        Of the capabilities defined outside the analysed files, only Pydantic AI's ``Toolset`` and
        ``Capability`` give one that can be read. ``merged`` are the calls of earlier ones merged into it.
        """
        target, _, callee, _ = self._built(entry)
        if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
            return []
        if _is_combined(callee):  # one kept whole gives what the capabilities it holds give
            members = self._members(target.node, target, depth)
            return [item for member in members for item in self._capability_toolsets(member, depth + 1)]
        if isinstance(callee, External) and callee.comes_from(
            "pydantic_ai", "Toolset", "Capability", *_LOCAL_FALLBACKS
        ):
            if callee.last in _LOCAL_FALLBACKS:
                given = self._local_fallback(callee, [*merged, target])
            else:
                given = self._library_capability_toolsets(callee.last, target.node, target)
            for item in given:
                item.wrappers = ["CapabilityOwnedToolset", *item.wrappers]
            return given
        if isinstance(callee, External) and callee.comes_from("pydantic_ai_harness", *_HARNESS_TOOLSETS):
            given = self._harness_toolset(callee.last, target.node, target)
            return [] if given is None else [given]
        if not isinstance(callee, Definition) or not isinstance(callee.node, ast.ClassDef):
            return []
        method = self.resolver.method(callee, "get_toolset")
        if method is None:
            return []
        found: list[_ToolsetEntry] = []
        for node in method.body.returns[:_MAX_WRAPPERS]:
            if isinstance(node, ast.Constant) and node.value is None:
                continue  # ``return None``: no toolset, in this case
            for item in self._toolset(Expression(node, method.body, node.lineno), 0):
                item.wrappers = ["CapabilityOwnedToolset", *item.wrappers]
                found.append(item)
        return found

    def _library_capability_toolsets(self, last: str, call: ast.Call, context: Expression) -> list[_ToolsetEntry]:
        """What Pydantic AI's ``Toolset(toolset)`` or ``Capability(tools=..., toolsets=...)`` gives, in its order."""
        if last == "Toolset":
            node = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
            node = next((keyword.value for keyword in call.keywords if keyword.arg == "toolset"), node)
            return [] if node is None else self._toolset(Expression(node, context.scope, context.line), 0)
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
        found: list[_ToolsetEntry] = []
        unpacked = any(keyword.arg is None for keyword in call.keywords)  # ``**kwargs`` may give either
        if "tools" in keywords or ("toolsets" not in keywords and not unpacked):
            # Its own ``FunctionToolset``, which it gives when it has tools or nothing else to give.
            toolset_id = self._keyword_string(call, "id", context)
            found.append(
                _ToolsetEntry(
                    kind="function",
                    label="FunctionToolset" + (f" {toolset_id!r}" if toolset_id else ""),
                    opaque=False,
                    toolset_id=toolset_id,
                    where=location(context.module, call),
                    tools=self._constructor_tools(call, context),
                )
            )
        if "toolsets" in keywords:
            listed = self._listed(keywords["toolsets"], context.scope, context.line, "toolsets")
            if listed is None:
                listed = [Expression(keywords["toolsets"], context.scope, context.line)]
            for item in listed:
                found.extend(self._toolset(item, 0))
        return found

    def _local_fallback(self, callee: External, occurrences: list[Expression]) -> list[_ToolsetEntry]:
        """The toolset ``WebSearch(local=...)`` or ``WebFetch(local=...)`` gives: the tool for a model without the
        provider-side one. Pydantic AI hides it from a model that has that, unless it is given ``native=False``.

        ``occurrences`` are the calls Pydantic AI merges into one, in order: ``local`` is the last one stated,
        ``native`` the last one's, which defaults to ``True``.
        """

        def stated(key: str) -> tuple[ast.Call, Expression, ast.expr] | None:
            for occurrence in reversed(occurrences):
                call = cast("ast.Call", occurrence.node)
                node = next((k.value for k in call.keywords if k.arg == key), None)
                if node is not None and not (isinstance(node, ast.Constant) and node.value is None):
                    return call, occurrence, node
            return None

        found = stated("local")
        if found is None:
            return []
        if any(stated(key) is not None for key in _NATIVE_ONLY_FIELDS.get(callee.last, ())):
            return []  # a constraint only the provider-side tool honours: the capability gives no local one
        call, context, node = found
        last = cast("ast.Call", occurrences[-1].node)
        keywords = {keyword.arg: keyword.value for keyword in last.keywords if keyword.arg is not None}
        where = location(context.module, call)
        spec: _ToolSpec | None = None
        try:
            value = literal(self.resolver, node, context.scope, context.line)
        except NotLiteral:
            spec = self.extractor.tool_of(node, context.scope, context.line)
        else:
            if value is None or value is False:
                return []
            if value is True or isinstance(value, str):  # a strategy that Pydantic AI names: ``"duckduckgo"``
                spec = self._strategy_tool(callee, where)
        native: Any = None
        if "native" in keywords:
            with contextlib.suppress(NotLiteral):
                native = literal(self.resolver, keywords["native"], occurrences[-1].scope, occurrences[-1].line)
        wrappers = [] if native is False else ["PreparedToolset"]
        toolset_id = (
            _LOCAL_FALLBACKS[callee.last] if "id" not in keywords else self._keyword_string(last, "id", occurrences[-1])
        )
        if spec is None:
            text = context.module.text(node)
            return [
                _ToolsetEntry(kind="other", label=text, opaque=True, wrappers=wrappers, expression=text, where=where)
            ]
        return [
            _ToolsetEntry(
                kind="function",
                label="FunctionToolset" + (f" {toolset_id!r}" if toolset_id else ""),
                opaque=False,
                toolset_id=toolset_id,
                wrappers=wrappers,
                where=where,
                tools=[spec],
            )
        ]

    def _strategy_tool(self, callee: External, where: SourceLocation) -> _ToolSpec | None:
        """The tool that ``local=True`` stands for, read from the installed source of the capability: what the one
        ``return`` of its method that resolves a strategy makes."""
        cls = self.extractor.installed_class(callee.name)
        method = None if cls is None else self.resolver.method(cls, _compat.LOCAL_STRATEGY_METHOD)
        if method is None or len(method.body.returns) != 1 or not isinstance(method.body.returns[0], ast.Call):
            return None
        made = self.resolver.callee(method.body.returns[0], method.body, END_OF_MODULE)
        return self.extractor.installed_tool(made, where) if isinstance(made, External) else None

    def _harness_toolset(self, last: str, call: ast.Call, context: Expression) -> _ToolsetEntry | None:
        """The toolset a capability of the harness gives, by its class: what it holds is not read."""
        toolset_class, toolset_id, renamed, inner = _HARNESS_TOOLSETS[last]
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
        if last == "SubAgents":
            listed = [call.args[index] for index in (0, 2) if len(call.args) > index]
            listed.extend(keywords[key] for key in ("agents", "agent_folders") if key in keywords)
            if not any(self._given(node, context) for node in listed):
                return None  # no agents to delegate to: no tool
        if last == "Skills" and self._flag(keywords.get("read_files"), context) is not True:
            return None
        if last == "Advisor" and self._flag(keywords.get("local"), context) is False:
            return None
        given = keywords.get("id")
        if renamed and given is not None and not (isinstance(given, ast.Constant) and given.value is None):
            toolset_id = string(self.resolver, given, context.scope, context.line)
        wrappers = ["CapabilityOwnedToolset", *inner]
        read_only = next((keyword.value for keyword in call.keywords if keyword.arg == "read_only"), None)
        if last == "FileSystem" and read_only is not None:
            with contextlib.suppress(NotLiteral):  # computed: whether it filters is only known when the code runs
                if literal(self.resolver, read_only, context.scope, context.line):
                    wrappers.append("FilteredToolset")  # what holds back the tools that write
        return _ToolsetEntry(
            kind="function",
            label=toolset_class + (f" {toolset_id!r}" if toolset_id else ""),
            opaque=True,
            toolset_id=toolset_id,
            wrappers=wrappers,
            where=location(context.module, call),
        )

    def _as_assigned_to(self, entry: Expression) -> Expression:
        """What to show for a value computed at run time: for a name given it in one place, what it was given.

        ``PROMPT = load("support")`` then ``instructions=PROMPT`` reads as ``load("support")``, as
        it does written inline. A name given a value in several places is shown as itself.
        """
        if not isinstance(unwrap(entry.node), ast.Name | ast.Attribute):
            return entry
        target = self.resolver.resolve(entry.node, entry.scope, entry.line)
        if self.resolver.reassigned() or not isinstance(target, Expression):
            return entry
        return target

    def _as_assigned(self, entry: Expression) -> str:
        """The text of an entry; for a name, the text of what the name was assigned, which says more."""
        if isinstance(unwrap(entry.node), ast.Name):
            binding = self.resolver.lookup(cast("ast.Name", unwrap(entry.node)).id, entry.scope, entry.line)
            if binding is not None and binding.kind == "assign" and isinstance(binding.node, ast.expr):
                return binding.scope.module.text(binding.node)
        return entry.module.text(entry.node)

    def _class_name(self, entry: Expression) -> str | None:
        """The class an expression constructs (or is), when that can be read.

        That is a class of the analysed files, or one of Pydantic AI's. What a call into
        any other library returns is only known when the code runs, whatever its name.
        """
        target = self.resolver.resolve(entry.node, entry.scope, entry.line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            target = self.resolver.callee(target.node, target.scope, target.line)
        if isinstance(target, External):
            return target.last if target.is_library_class() else None
        if isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
            return target.node.name
        return None

    def _library_base(self, callee: Resolved) -> External | None:
        """The class of Pydantic AI that ``callee`` is, or that a class of the analysed files derives from."""
        if isinstance(callee, External):
            return callee if callee.is_library_class() else None
        if not isinstance(callee, Definition) or not isinstance(callee.node, ast.ClassDef):
            return None
        classes: list[Definition] = [callee]
        for current in classes:  # grows while it is gone through: the bases, breadth first
            node = current.node
            assert isinstance(node, ast.ClassDef)
            for base in node.bases[:_MAX_WRAPPERS]:
                written = base.value if isinstance(base, ast.Subscript) else base
                parent = self.resolver.resolve(written, current.scope, node.lineno)
                if isinstance(parent, External) and parent.is_library_class():
                    return parent
                if (
                    isinstance(parent, Definition)
                    and isinstance(parent.node, ast.ClassDef)
                    and all(parent.node is not seen.node for seen in classes)
                    and len(classes) < 2 * _MAX_WRAPPERS
                ):
                    classes.append(parent)
        return None

    def _flag(self, node: ast.expr | None, context: Expression) -> bool | None:
        """The ``True`` or ``False`` an argument spells out; ``None`` when it is computed or not given."""
        try:
            value = None if node is None else literal(self.resolver, node, context.scope, context.line)
        except NotLiteral:
            return None
        return value if isinstance(value, bool) else None

    def _given(self, node: ast.expr, context: Expression) -> bool:
        """Whether an argument gives something: it is computed, or a value other than ``None`` or empty."""
        try:
            value = literal(self.resolver, node, context.scope, context.line)
        except NotLiteral:
            return True
        return value not in (None, [], ())

    def _sub_agent_delegates(self) -> list[Delegate]:
        """The agents a harness ``SubAgents(agents=[SubAgent(agent, ...)])`` hands tasks to, through its tool."""
        found: list[Delegate] = []
        for entry in self._capability_entries:
            target, call, callee, _ = self._built(entry)
            if call is None or not isinstance(target, Expression):
                continue
            if not (isinstance(callee, External) and callee.comes_from("pydantic_ai_harness", "SubAgents")):
                continue
            node = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
            node = next((keyword.value for keyword in call.keywords if keyword.arg == "agents"), node)
            named = next((keyword.value for keyword in call.keywords if keyword.arg == "tool_name"), None)
            tool_name = (
                _compat.SUB_AGENTS_TOOL_NAME
                if named is None
                else string(self.resolver, named, target.scope, target.line)
            )
            listed = (
                None
                if node is None or tool_name is None
                else self._listed(node, target.scope, target.line, "sub-agents")
            )
            for item in listed or []:
                made = self.resolver.resolve(item.node, item.scope, item.line)
                if not isinstance(made, Expression) or not isinstance(made.node, ast.Call):
                    continue
                sub = self.resolver.callee(made.node, made.scope, made.line)
                if not (isinstance(sub, External) and sub.comes_from("pydantic_ai_harness", "SubAgent")):
                    continue
                agent = made.node.args[0] if made.node.args and not isinstance(made.node.args[0], ast.Starred) else None
                agent = next((keyword.value for keyword in made.node.keywords if keyword.arg == "agent"), agent)
                for site in [] if agent is None else self.extractor.agents_at(agent, made.scope, made.line):
                    delegate = Delegate(tool_name=str(tool_name), agent_id=self.ids[site.key], evidence="code")
                    if delegate not in found:
                        found.append(delegate)
        return found

    # ------------------------------------------------------------ settings

    def _settings(self) -> AgentSettings:
        expressions: dict[str, str] = {}
        tool_retries, output_retries = self._retries(expressions)
        end_strategy = self._setting("end_strategy", str, expressions)
        timeout = self._setting("tool_timeout", int | float, expressions)
        return AgentSettings(
            model_settings=self._model_settings(expressions),
            end_strategy=end_strategy,
            tool_retries=tool_retries,
            output_retries=output_retries,
            tool_timeout=None if timeout is None else float(timeout),
            expressions=expressions,
            defaults=[],
        )

    def _setting(self, key: str, kind: Any, expressions: dict[str, str]) -> Any:
        node = self.arguments.get(key)
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        try:
            value = self.literal(node)
        except NotLiteral:
            value = None
        if value is None or isinstance(value, bool) or not isinstance(value, kind):
            expressions[key] = self.text(node)
            return None
        return value

    def _retries(self, expressions: dict[str, str]) -> tuple[int | None, int | None]:
        tools, output = self._retry_budgets(expressions)
        # A budget for the output alone, as earlier releases took it: ``output_retries``, once ``result_retries``.
        separate = self.arguments.get("output_retries", self.arguments.get("result_retries"))
        if separate is not None and not (isinstance(separate, ast.Constant) and separate.value is None):
            try:
                value = self.literal(separate)
            except NotLiteral:
                value = None
            if isinstance(value, int) and not isinstance(value, bool):
                output = value
                expressions.pop("output_retries", None)
            else:
                output = None
                expressions["output_retries"] = self.text(separate)
        return tools, output

    def _retry_budgets(self, expressions: dict[str, str]) -> tuple[int | None, int | None]:
        """The budgets ``retries=`` sets; a spec's, but for those the call sets itself."""
        tools, output = self._retries_given(self._spec.get("retries"), expressions)
        given = self._retries_given(self.arguments.get("retries"), expressions)
        if "tool_retries" in expressions:
            return None, None
        return (tools if given[0] is None else given[0]), (output if given[1] is None else given[1])

    def _retries_given(self, node: ast.expr | None, expressions: dict[str, str]) -> tuple[int | None, int | None]:
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None, None
        try:
            value = self.literal(node)
        except NotLiteral:
            value = None
        if isinstance(value, int) and not isinstance(value, bool):
            return value, value
        if isinstance(value, dict):
            budgets = cast("dict[str, Any]", value)
            tools, output = budgets.get("tools"), budgets.get("output")
            return (tools if isinstance(tools, int) else None, output if isinstance(output, int) else None)
        text = self.text(node)
        expressions["tool_retries"] = expressions["output_retries"] = text
        return None, None

    def _native_tools(self) -> list[str]:
        """Tools the provider runs, from ``builtin_tools=[WebSearchTool()]`` as earlier releases took them."""
        node = self.arguments.get("builtin_tools")
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return []
        entries = self._listed(node, *self.where(node), "builtin tools")
        if entries is None:
            self.warn(
                f"the builtin tools are built by `{brief(self.text(node))}`; they are only known when the code runs"
            )
            return []
        found: list[str] = []
        for entry in entries:
            name = self._class_name(entry)
            if name is None:
                text = entry.module.text(entry.node)
                self.warn(f"a builtin tool is built by `{brief(text)}`; its kind is only known when the code runs")
                continue
            kind = _NATIVE_TOOLS.get(name, name)
            if kind not in found:
                found.append(kind)
        return found

    def _model_settings(self, expressions: dict[str, str]) -> dict[str, Any] | None:
        """The settings ``model_settings=`` gives; a spec's, with those the call gives over them."""
        spec = self._model_settings_given(self._spec.get("model_settings"), expressions)
        given = self._model_settings_given(self.arguments.get("model_settings"), expressions)
        if spec is None or "model_settings" in expressions:
            return given
        return spec if given is None else {**spec, **given}

    def _model_settings_given(self, node: ast.expr | None, expressions: dict[str, str]) -> dict[str, Any] | None:
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        target = self.resolver.resolve(node, *self.where(node))
        pairs: list[tuple[str, Expression]] | None = None
        scope, _ = self.where(node)
        until = (self.scope, self.line) if scope is self.scope else None
        if isinstance(target, Expression) and not self.resolver.changed(until=until):  # ``settings["k"] = v``
            inner = target.node
            if isinstance(inner, ast.Dict):
                pairs = []
                for key, value in zip(inner.keys, inner.values, strict=True):
                    name = None if key is None else string(self.resolver, key, target.scope, target.line)
                    if name is None:
                        pairs = None
                        break
                    pairs.append((name, Expression(value, target.scope, target.line)))
            elif isinstance(inner, ast.Call) and not inner.args:
                callee = self.resolver.callee(inner, target.scope, target.line)
                # ``ModelSettings(temperature=0)`` and the provider-specific ones: Pydantic AI's settings are
                # ``TypedDict``s, so the keywords are the keys. Another library's class may be anything.
                is_settings = (
                    isinstance(callee, External) and callee.is_library_class() and callee.last.endswith("Settings")
                )
                if is_settings and all(keyword.arg is not None for keyword in inner.keywords):
                    pairs = [
                        (keyword.arg, Expression(keyword.value, target.scope, target.line))
                        for keyword in inner.keywords
                        if keyword.arg is not None
                    ]
        if pairs is None:
            expressions["model_settings"] = self.text(node)
            return None
        settings: dict[str, Any] = {}
        for name, value in pairs:
            try:
                settings[name] = literal(self.resolver, value.node, value.scope, value.line)
            except NotLiteral:
                expressions[f"model_settings.{name}"] = value.module.text(value.node)
        return settings

    # ------------------------------------------------------------ toolsets

    def _toolsets(self) -> tuple[list[Toolset], list[Tool], list[Delegate]]:
        entries: list[_ToolsetEntry] = []
        own = self._own_tools()
        if own:
            entries.append(
                _ToolsetEntry(kind="agent", label="Agent tools", opaque=False, toolset_id=AGENT_TOOLSET_ID, tools=own)
            )
        # ``mcp_servers=[...]`` is where earlier releases took MCP servers; they are toolsets all the same.
        for key in ("toolsets", "mcp_servers"):
            node = self.arguments.get(key)
            if node is None or (isinstance(node, ast.Constant) and node.value is None):
                continue
            listed = self._listed(node, *self.where(node), "toolsets")
            if listed is None:
                text = self.text(node)
                self.warn(f"the toolsets are built by `{brief(text)}`; they are only known when the code runs")
                entries.append(
                    _ToolsetEntry(
                        kind="other",
                        label=text,
                        opaque=True,
                        expression=text,
                        where=location(self.where(node)[0].module, node),
                    )
                )
            else:
                for item in listed:
                    entries.extend(self._toolset(item, 0))
        self._owned_toolsets = []
        built = [self._built(capability) for capability in self._capability_entries]
        keys = [self._merge_key(callee, call, target) for target, call, callee, _ in built]
        for index, capability in enumerate(self._capability_entries):
            start = len(entries)
            key = keys[index]
            if key is None or key not in keys[index + 1 :]:  # else merged into the last of them, which gives
                merged = tuple(
                    cast("Expression", built[earlier][0]) for earlier in range(index) if keys[earlier] == key
                )
                entries.extend(self._capability_toolsets(capability, merged=merged if key is not None else ()))
            self._owned_toolsets.append([f"ts{index}" for index in range(start, len(entries))])
        for function in self.agent.toolset_functions:
            entries.append(
                _ToolsetEntry(
                    kind="dynamic",
                    label=function.node.name,
                    opaque=True,
                    where=location(function.module, function.node),
                )
            )

        toolsets: list[Toolset] = []
        tools: list[Tool] = []
        delegates: list[Delegate] = []
        for index, entry in enumerate(entries):
            toolset = Toolset(
                id=f"ts{index}",
                toolset_id=entry.toolset_id,
                label=entry.label,
                kind=entry.kind,
                wrappers=entry.wrappers,
                opaque=entry.opaque,
                expression=entry.expression,
                location=entry.where,
            )
            toolsets.append(toolset)
            for spec in entry.tools:
                name = spec.name
                for prefix in entry.prefixes:
                    name = f"{prefix}_{name}"
                tools.append(self._tool(spec, name, toolset.id))
                for agent_id in self._delegations(spec):
                    delegate = Delegate(tool_name=name, agent_id=agent_id, evidence="code")
                    if delegate not in delegates:
                        delegates.append(delegate)
        return toolsets, tools, delegates

    def _own_tools(self) -> list[_ToolSpec]:
        """The agent's own tools: the ``tools=[...]`` argument first, then registrations in source order."""
        own: list[_ToolSpec] = []
        node = self.arguments.get("tools")
        if node is not None:
            listed = self._listed(node, *self.where(node), "tools")
            if listed is None:
                self.warn(f"the tools are built by `{brief(self.text(node))}`; they are only known when the code runs")
            for item in listed or []:
                spec = self.extractor.tool_of(item.node, item.scope, item.line)
                if spec is None:
                    text = item.module.text(item.node)
                    self.warn(f"a tool is built by `{brief(text)}`; it cannot be described without running the code")
                else:
                    own.append(spec)
        return [*own, *self.agent.tools]

    def _toolset(self, entry: Expression, depth: int) -> list[_ToolsetEntry]:
        """Describe one entry of ``toolsets=[...]``, looking through wrappers to the toolset inside."""
        text = self._as_assigned(entry)
        wrappers: list[str] = []
        prefixes: list[str] = []
        current: Resolved = self.resolver.resolve(entry.node, entry.scope, entry.line)
        for _ in range(16):
            if not isinstance(current, Expression) or not isinstance(current.node, ast.Call):
                break
            call = current.node
            function = call.func
            inner: ast.expr | None = None
            if isinstance(function, ast.Attribute) and function.attr in _WRAPPER_METHODS:
                # ``toolset.prefixed("kb")``: a method that returns a wrapper around the receiver.
                wrappers.append(_WRAPPER_METHODS[function.attr])
                if function.attr == "prefixed":
                    prefixes.append(self._prefix(call, current))
                inner = function.value
            else:
                callee = self.resolver.callee(call, current.scope, current.line)
                if isinstance(callee, External) and callee.is_library(*_WRAPPER_CLASSES):
                    wrappers.append(callee.last)
                    if callee.last == "PrefixedToolset":
                        prefixes.append(self._prefix(call, current, positional=1))
                    inner = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
                    if inner is None:
                        inner = next((item.value for item in call.keywords if item.arg == "wrapped"), None)
                elif self.extractor.toolset_made_by(call, current.scope, current.line) is None:
                    # Any other toolset class of Pydantic AI that is handed a toolset wraps that toolset:
                    # ``CodeExecutionToolset(toolset=tools)``, or a project's own class derived from one.
                    name = self._toolset_class(callee)
                    handed = self._handed_toolset(call, current) if name is not None else None
                    if name is not None and handed is not None:
                        wrappers.append(name)
                        inner = handed
            if inner is None:
                break
            current = self.resolver.resolve(inner, current.scope, current.line)
        prefixes.reverse()  # collected from the outside in; applied from the inside out
        if any(not prefix for prefix in prefixes):
            self.warn(f"a tool name prefix in `{brief(text)}` is computed; the tool names shown may lack it")
            prefixes = [prefix for prefix in prefixes if prefix]

        where = location(entry.module, entry.node)
        result = _ToolsetEntry(kind="other", label=text, opaque=True, wrappers=wrappers, where=where, prefixes=prefixes)
        if isinstance(current, Expression) and isinstance(current.node, ast.Call):
            call = current.node
            made = self.extractor.toolset_made_by(call, current.scope, current.line)
            callee = self.resolver.callee(call, current.scope, current.line)
            toolset_id = self._keyword_string(call, "id", current)
            result.toolset_id = toolset_id
            result.where = location(current.module, call)
            if made is not None:
                site, own_class = made
                site.used = True
                result.kind, result.opaque = "function", False
                result.label = (own_class or "FunctionToolset") + (f" {toolset_id!r}" if toolset_id else "")
                # A class of the project takes whatever its own ``__init__`` asks for, not a list of tools.
                given = [] if own_class is not None else self._constructor_tools(call, current)
                result.tools = [*given, *site.tools]
                for note in site.notes:
                    self.warn(note)
                return [result]
            if isinstance(callee, External) and callee.is_library("CombinedToolset") and depth < 4 and call.args:
                children = self._listed(call.args[0], current.scope, current.line, "toolsets")
                if children is not None:
                    combined: list[_ToolsetEntry] = []
                    for child in children:
                        for nested in self._toolset(child, depth + 1):
                            nested.wrappers = [*wrappers, *nested.wrappers]
                            nested.prefixes = [*nested.prefixes, *prefixes]
                            combined.append(nested)
                    return combined
            name = self._class_name(current)
            if name is not None:
                result.label = name + (f" {toolset_id!r}" if toolset_id else "")
                library = self._library_base(callee)
                if library is not None and ("MCP" in library.last or library.name.startswith("pydantic_ai.mcp")):
                    result.kind = "mcp"
                elif name == "DynamicToolset":
                    result.kind = "dynamic"
                else:
                    result.expression = current.module.text(call)
                return [result]
        elif isinstance(current, Definition) and not isinstance(current.node, ast.ClassDef):
            # A function that builds the toolset for each run.
            result.kind, result.label = "dynamic", current.node.name
            result.where = location(current.module, current.node)
            return [result]
        result.expression = text
        self.warn(f"a toolset is built by `{brief(text)}`; its tools are only known when the code runs")
        return [result]

    def _toolset_class(self, callee: Resolved) -> str | None:
        """The name of a class that is one of Pydantic AI's toolset classes, or is derived from one."""
        library = self._library_base(callee)
        if library is None or not library.last.endswith("Toolset"):
            return None
        return callee.node.name if isinstance(callee, Definition) else library.last

    def _handed_toolset(self, call: ast.Call, context: Expression) -> ast.expr | None:
        """The argument of a call that is a ``FunctionToolset`` made in the analysed source, if one is."""
        candidates: list[ast.expr] = [item.value for item in call.keywords if item.arg in ("wrapped", "toolset")]
        if call.args and not isinstance(call.args[0], ast.Starred):
            candidates.append(call.args[0])
        for candidate in candidates:
            # Looked through its own wrappers too: ``Outer(toolset=inner.prefixed("x"))``.
            target = self.resolver.resolve(candidate, context.scope, context.line)
            for _ in range(_MAX_WRAPPERS):
                if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
                    break
                if self.extractor.toolset_made_by(target.node, target.scope, target.line) is not None:
                    return candidate
                function = target.node.func
                if not (isinstance(function, ast.Attribute) and function.attr in _WRAPPER_METHODS):
                    break
                target = self.resolver.resolve(function.value, target.scope, target.line)
        return None

    def _prefix(self, call: ast.Call, context: Expression, positional: int = 0) -> str:
        """The literal prefix of a ``prefixed(...)`` wrapper, or ``""`` when it is computed."""
        node: ast.expr | None = None
        if len(call.args) > positional and not isinstance(call.args[positional], ast.Starred):
            node = call.args[positional]
        node = node or next((item.value for item in call.keywords if item.arg == "prefix"), None)
        if node is None:
            return ""
        return string(self.resolver, node, context.scope, context.line) or ""

    def _keyword_string(self, call: ast.Call, key: str, context: Expression) -> str | None:
        node = next((item.value for item in call.keywords if item.arg == key), None)
        return None if node is None else string(self.resolver, node, context.scope, context.line)

    def _constructor_tools(self, call: ast.Call, context: Expression) -> list[_ToolSpec]:
        """Tools passed to ``FunctionToolset([...])`` or ``FunctionToolset(tools=[...])``."""
        node: ast.expr | None = None
        if call.args and not isinstance(call.args[0], ast.Starred):
            node = call.args[0]
        node = node or next((item.value for item in call.keywords if item.arg == "tools"), None)
        if node is None:
            return []
        listed = self._listed(node, context.scope, context.line, "tools of a toolset")
        if listed is None:
            self.warn(
                f"the tools of a toolset are built by `{brief(context.module.text(node))}`; "
                "they are only known when the code runs"
            )
            return []
        found: list[_ToolSpec] = []
        for item in listed:
            spec = self.extractor.tool_of(item.node, item.scope, item.line)
            if spec is None:
                text = item.module.text(item.node)
                self.warn(f"a tool is built by `{brief(text)}`; it cannot be described without running the code")
            else:
                found.append(spec)
        return found

    # --------------------------------------------------------------- tools

    def _tool(self, spec: _ToolSpec, name: str, toolset: str) -> Tool:
        function = spec.function
        if function is None:
            if spec.made_by is not None:
                self.warn(
                    f"the tool `{name}` is made by `{spec.made_by}`; its name and description are read from the "
                    f"installed Pydantic AI {spec.library_version}, and its parameters are only known when the code "
                    "runs"
                )
            elif spec.unread is not None:
                self.warn(
                    f"the parameters of the tool `{name}` are those of `{brief(spec.unread)}`, "
                    "so they are only known when the code runs"
                )
            else:
                self.warn(
                    f"the tool `{name}` is `{spec.external}`, which is defined outside the analysed source, "
                    "so its description and parameters are not shown"
                )
            return Tool(
                name=name,
                toolset=toolset,
                description=spec.description,
                parameters_schema=None,
                return_schema=None,
                kind=spec.kind,
                takes_ctx=spec.takes_ctx,
                is_async=None,
                max_retries=spec.max_retries,
                function=spec.external or spec.made_by,
                origin="code",
                location=spec.where,
                library_version=spec.library_version,
            )
        node = function.node
        assert not isinstance(node, ast.ClassDef)
        description, parameter_descriptions = describe(
            ast.get_docstring(node, clean=False),
            has_return_annotation=node.returns is not None,
            style=spec.docstring_format,
        )
        if spec.docstring_format_expression is not None:
            self.warn(
                f"the docstring format of the tool `{name}` is set by `{brief(spec.docstring_format_expression)}`, "
                "so it is only known when the code runs; its descriptions are read as if it were inferred from "
                "the docstring"
            )
        builder = SchemaBuilder(self.resolver)
        skip = self.extractor.receiver_count(function) + (1 if spec.takes_ctx else 0)
        schema = builder.parameters(function, skip=skip, descriptions=parameter_descriptions)
        if builder.unresolved:
            names = ", ".join(f"`{item}`" for item in builder.unresolved)
            self.warn(
                f"the parameters of the tool `{name}` are incomplete: {names} could not be resolved "
                "from the analysed source"
            )
        for note in builder.notes:
            self.warn(f"in the parameters of the tool `{name}`, {note}")
        return Tool(
            name=name,
            toolset=toolset,
            description=spec.description if spec.description is not None else description,
            parameters_schema=schema,
            return_schema=self._return_schema(function),
            kind=spec.kind,
            takes_ctx=spec.takes_ctx,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            max_retries=spec.max_retries,
            function=function.qualified_name,
            origin="code",
            location=spec.where,
            library_version=spec.library_version,
        )

    def _return_schema(self, function: Definition) -> dict[str, Any] | None:
        """The schema of a tool's declared return type; ``None`` when that type cannot be resolved."""
        node = function.node
        assert not isinstance(node, ast.ClassDef)
        if node.returns is None:
            return {}  # no declared type: anything, which is also what Pydantic AI reports
        builder = SchemaBuilder(self.resolver)
        schema = builder.annotation(node.returns, function.scope, node.lineno)
        return None if builder.unresolved else builder.document(schema)

    def _delegations(self, spec: _ToolSpec) -> list[str]:
        """Ids of the agents a tool's body runs: ``other.run(...)``, or ``other`` handed to a helper."""
        function = spec.function
        if function is None:
            return []
        body = function.body
        found: list[str] = []
        for node in ast.walk(function.node):
            if not isinstance(node, ast.Call):
                continue
            candidates: list[ast.expr] = []
            receiver: ast.expr | None = None
            if isinstance(node.func, ast.Attribute) and node.func.attr in RUN_METHODS:
                receiver = node.func.value
                candidates.append(receiver)
            else:
                # Handed to a function of the analysed source that runs what it is given. Passed anywhere
                # else (logged, compared, kept), an agent is not run, as far as the source shows.
                candidates.extend(self._run_by_callee(node, body))
            for candidate in candidates:
                agents = self.extractor.agents_at(unwrap(candidate), body, node.lineno, prefer=self.agent.via)
                if not agents and candidate is receiver:
                    path = self._deps_path(candidate, spec)
                    if path is not None:
                        agents = self.extractor.agents_in_deps(self.agent, path)
                    else:
                        agents = self._chosen_from(candidate, body, node.lineno)
                for agent in agents:
                    agent_id = self.ids[agent.key]
                    if agent_id not in found:
                        found.append(agent_id)
        return found

    def _run_by_callee(self, call: ast.Call, scope: Scope, depth: int = 0) -> list[ast.expr]:
        """The arguments of a call that the function it calls runs: ``run_it(worker, text)`` -> ``[worker]``.

        That function is one of the analysed source whose body calls a run method on the
        parameter, or hands the parameter on to another such function.
        """
        callee = self.resolver.callee(call, scope, call.lineno)
        if depth >= _MAX_HANDED_ON or not isinstance(callee, Definition) or isinstance(callee.node, ast.ClassDef):
            return []
        body, node = callee.body, callee.node
        found: list[ast.expr] = []
        for parameter in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            argument = self.extractor.argument_for(call, body, parameter.arg)
            if isinstance(argument, ast.Name | ast.Attribute) and self._runs(body, parameter.arg, depth):
                found.append(argument)
        return found

    def _runs(self, body: Scope, parameter: str, depth: int) -> bool:
        """Whether a function's body runs its parameter as an agent, itself or through another function."""
        key = (id(body), parameter)
        known = self.extractor.runs_parameter.get(key)
        if known is None:
            self.extractor.runs_parameter[key] = False  # a function that hands the parameter to itself
            known = self._runs_in(body, parameter, depth)
            self.extractor.runs_parameter[key] = known
        return known

    def _runs_in(self, body: Scope, parameter: str, depth: int) -> bool:
        if not isinstance(body.node, ast.FunctionDef | ast.AsyncFunctionDef):
            return False
        bindings = body.bindings.get(parameter, [])
        if len(bindings) != 1 or bindings[0].kind != "opaque":
            return False  # given another value in the body: what it runs may not be what it was handed
        for node in ast.walk(body.node):
            if not isinstance(node, ast.Call):
                continue
            method = node.func
            if isinstance(method, ast.Attribute) and method.attr in RUN_METHODS:
                runs = unwrap(method.value)
                if isinstance(runs, ast.Name) and runs.id == parameter:
                    return True
            handed = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(isinstance(item, ast.Name) and item.id == parameter for item in handed) and any(
                isinstance(item, ast.Name) and item.id == parameter
                for item in self._run_by_callee(node, body, depth + 1)
            ):
                return True
        return False

    def _deps_path(self, node: ast.expr, spec: _ToolSpec) -> list[str] | None:
        """For ``ctx.deps`` or ``ctx.deps.field`` in a tool that takes the run context: what follows ``deps``."""
        function = spec.function
        if function is None or isinstance(function.node, ast.ClassDef):
            return None
        parameters = [*function.node.args.posonlyargs, *function.node.args.args]
        parameters = parameters[self.extractor.receiver_count(function) :]
        if not spec.takes_ctx or not parameters:
            return None
        names: list[str] = []
        base = unwrap(node)
        while isinstance(base, ast.Attribute) and len(names) <= _MAX_DEPS_PATH:
            names.append(base.attr)
            base = base.value
        names.reverse()
        if not isinstance(base, ast.Name) or base.id != parameters[0].arg or names[:1] != ["deps"]:
            return None
        return names[1:] if len(names) <= _MAX_DEPS_PATH else None

    def _chosen_from(self, node: ast.expr, scope: Scope, line: int) -> list[_AgentSite]:
        """The agents of a written-out dict or list that the agent being run is picked from.

        ``AGENTS[name].run(...)``, or ``agent = AGENTS.get(name)`` and then ``agent.run(...)``:
        which one runs is decided by the key, so each agent the container holds is a delegate.
        """
        target = self.resolver.resolve(node, scope, line)
        if not isinstance(target, Expression):
            return []
        picked = target.node
        container: ast.expr | None = None
        key: ast.expr | None = None
        if isinstance(picked, ast.Subscript) and not isinstance(picked.slice, ast.Slice):
            container, key = picked.value, picked.slice
        elif (
            isinstance(picked, ast.Call)
            and isinstance(picked.func, ast.Attribute)
            and picked.func.attr == "get"
            and 1 <= len(picked.args) <= 2
            and not isinstance(picked.args[0], ast.Starred)
        ):
            container, key = picked.func.value, picked.args[0]
        if container is None or key is None:
            return []
        held = self.resolver.resolve(container, target.scope, target.line)
        if not isinstance(held, Expression):
            return []
        if isinstance(held.node, ast.Dict):
            values = held.node.values
            # A key the source spells out picks one entry; any other key may pick any of them.
            written = string(self.resolver, key, target.scope, target.line)
            if written is not None:
                keys = [None if k is None else string(self.resolver, k, held.scope, held.line) for k in held.node.keys]
                if written in keys:
                    last = len(keys) - 1 - keys[::-1].index(written)  # a key written twice holds the later value
                    values = [held.node.values[last]]
        elif isinstance(held.node, ast.List | ast.Tuple) and isinstance(picked, ast.Subscript):
            values = held.node.elts
        else:
            return []
        agents: list[_AgentSite] = []
        for value in values[:_MAX_LIST_ITEMS]:
            agent = self.extractor.agent_at(value, held.scope, held.line)
            if agent is not None and agent not in agents:
                agents.append(agent)
        return agents


def _merge_alike(
    found: list[CapabilityInfo],
    keys: list[tuple[str, str] | None],
    defaults: dict[tuple[str, str], dict[str, Any]],
) -> list[CapabilityInfo]:
    """The capabilities as the agent keeps them: those of one class and id merged into one, where the last stood.

    Merged by Pydantic AI's rules: a value only one states survives, lists and dicts are joined with the later
    winning a shared key, and any other value is the later one's. Only ``None`` is not stated, so a later one
    that leaves out a field its class defaults to another value states that default, which is not shown. The
    toolsets are those the last one gives, made from the merged arguments (`_Builder._toolsets`).
    """
    merged: list[CapabilityInfo] = []
    for index, (capability, key) in enumerate(zip(found, keys, strict=True)):
        if key is None:
            merged.append(capability)
            continue
        if key in keys[index + 1 :]:
            continue  # merged into the last of them
        group = [item for item, other in zip(found, keys, strict=True) if other == key]
        details: dict[str, Any] = {}
        expressions: dict[str, str] = {}
        declared = defaults.get(key, {})
        for item in group:
            for name in [*details, *expressions]:
                if name not in item.details and name not in item.expressions and declared.get(name) is not None:
                    details.pop(name, None)  # its default, stated by leaving it out
                    expressions.pop(name, None)
            for name, value in item.details.items():
                expressions.pop(name, None)
                details[name] = _merged_value(details.get(name), value)
            for name, text in item.expressions.items():
                details.pop(name, None)
                expressions[name] = text
        merged.append(
            capability.model_copy(
                update={
                    "details": details,
                    "expressions": expressions,
                    "toolsets": _union(item.toolsets for item in group),
                    "native_tools": _union(item.native_tools for item in group),
                }
            )
        )
    return merged


def _merged_value(earlier: Any, later: Any) -> Any:
    if isinstance(earlier, list) and isinstance(later, list):
        return [*cast("list[Any]", earlier), *(item for item in cast("list[Any]", later) if item not in earlier)]
    if isinstance(earlier, dict) and isinstance(later, dict):
        return {**cast("dict[str, Any]", earlier), **cast("dict[str, Any]", later)}
    return later


def _env(read: EnvironmentRead) -> EnvironmentValue:
    return EnvironmentValue(variable=read.variable, default=read.default)


def _environment_words(read: EnvironmentRead) -> str:
    """``the environment variable `MODEL` when the code runs; the source's default is `gpt-5.2```."""
    default = "the source gives no default" if read.default is None else f"the source's default is `{read.default}`"
    return f"the environment variable `{read.variable}` when the code runs; {default}"
