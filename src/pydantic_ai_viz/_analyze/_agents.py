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
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, cast

from .. import _compat
from .._models import (
    AgentBlueprint,
    AgentSettings,
    CapabilityInfo,
    Delegate,
    Instruction,
    ModelInfo,
    OutputInfo,
    SourceLocation,
    Tool,
    Toolset,
)
from ._docstring import describe
from ._resolve import Definition, Expression, External, Project, Resolved, Resolver
from ._schema import NOT_FROM_THE_MODEL, SchemaBuilder
from ._source import CallSite, Module, Mutation, Scope, clip_expression, unwrap
from ._values import NotLiteral, fstring_template, literal, string

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
_TOOL_FACTORIES: dict[str, str] = {
    "duckduckgo_search_tool": "duckduckgo_search",
    "tavily_search_tool": "tavily_search",
    "exa_search_tool": "exa_search",
    "exa_find_similar_tool": "exa_find_similar",
    "exa_get_contents_tool": "exa_get_contents",
    "exa_answer_tool": "exa_answer",
    "image_generation_tool": "generate_image",
}
"""Functions in ``pydantic_ai.common_tools`` that return a ready-made ``Tool`` -> the name that tool has."""
_ENVIRONMENT_READERS = frozenset({"os.getenv", "os.environ.get"})
_MAX_LIST_ITEMS = 500
_MAX_WRAPPERS = 4
_MAX_EXPANSION_DEPTH = 6


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


@dataclass(slots=True, eq=False)
class _ToolsetSite:
    """A ``FunctionToolset(...)`` construction and the tools registered on it."""

    site: CallSite
    tools: list[_ToolSpec] = field(default_factory=list[_ToolSpec])
    notes: list[str] = field(default_factory=list[str])
    """Warnings about registrations that could not be read, for every agent that uses the toolset."""


@dataclass(slots=True, eq=False)
class _AgentSite:
    """An ``Agent(...)`` construction and what decorators and calls add to it."""

    site: CallSite
    tools: list[_ToolSpec] = field(default_factory=list[_ToolSpec])
    instructions: list[Instruction] = field(default_factory=list[Instruction])
    system_prompts: list[Instruction] = field(default_factory=list[Instruction])
    toolset_functions: list[Definition] = field(default_factory=list[Definition])
    notes: list[str] = field(default_factory=list[str])
    """Warnings about registrations that could not be read."""

    @property
    def module(self) -> Module:
        return self.site.scope.module


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

    def __init__(self, project: Project, resolver: Resolver) -> None:
        self.project = project
        self.resolver = resolver
        self.agents: dict[int, _AgentSite] = {}
        """``id(ast.Call)`` -> the agent constructed there, in discovery order."""
        self.toolsets: dict[int, _ToolsetSite] = {}
        self.conditional = False
        """Set by ``items`` when an entry it returned depends on a condition."""
        self.added_unread: list[str] = []
        """Filled by ``items``: statements that add entries to a list it returned which it could not read."""

    # -------------------------------------------------------------- driver

    def find(self) -> None:
        """Locate the agents and toolsets, then attach what decorators and calls register on them."""
        for module in self.project.modules:
            for site in sorted(module.calls, key=lambda item: (item.node.lineno, item.node.col_offset)):
                callee = self.resolver.callee(site.node, site.scope)
                if isinstance(callee, External):
                    if callee.is_library("Agent"):
                        self.agents[id(site.node)] = _AgentSite(site)
                    elif callee.is_library("FunctionToolset"):
                        self.toolsets[id(site.node)] = _ToolsetSite(site)
        for module in self.project.modules:
            self._registrations(module)

    def blueprints(self) -> list[AgentBlueprint]:
        ids = {key: f"a{index}" for index, key in enumerate(self.agents, start=1)}
        return [_AgentBuilder(self, agent, ids).build() for agent in self.agents.values()]

    def agent_at(self, node: ast.expr, scope: Scope, line: int | None = None) -> _AgentSite | None:
        """The agent an expression stands for, if it can be traced to one construction."""
        target = self.resolver.resolve(node, scope, line)
        for _ in range(_MAX_WRAPPERS):
            if not isinstance(target, Expression) or not isinstance(target.node, ast.Call):
                return None
            call = target.node
            found = self.agents.get(id(call))
            if found is not None:
                return found
            # ``TemporalAgent(agent)``, ``DBOSAgent(agent)``: Pydantic AI's wrappers run the agent they are given.
            callee = self.resolver.callee(call, target.scope, target.line)
            if not (isinstance(callee, External) and callee.is_library_class() and callee.last.endswith("Agent")):
                return None
            wrapped = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
            wrapped = wrapped or next((item.value for item in call.keywords if item.arg == "wrapped"), None)
            if wrapped is None:
                return None
            target = self.resolver.resolve(wrapped, target.scope, target.line)
        return None

    def agent_id(self, node: ast.expr, scope: Scope, line: int | None = None) -> str | None:
        """Blueprint id of the agent an expression stands for."""
        found = self.agent_at(node, scope, line)
        if found is None:
            return None
        return f"a{list(self.agents).index(id(found.site.node)) + 1}"

    def _receiver(self, node: ast.expr, scope: Scope, line: int) -> _AgentSite | _ToolsetSite | None:
        target = self.resolver.resolve(node, scope, line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            key = id(target.node)
            return self.agents.get(key) or self.toolsets.get(key)
        return None

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
        if not isinstance(method, ast.Attribute):
            return
        receiver = self._receiver(method.value, scope, line)
        if receiver is None:
            return
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
        elif isinstance(receiver, _AgentSite):
            if name == "instructions" or name == "system_prompt":
                prompt = Instruction(
                    kind=name,
                    dynamic=True,
                    text=None,
                    function=function.qualified_name,
                    expression=None,
                    location=where,
                )
                (receiver.instructions if name == "instructions" else receiver.system_prompts).append(prompt)
            elif name == "toolset":
                receiver.toolset_functions.append(function)

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
            if isinstance(callee, External) and callee.is_library(*_TOOL_FACTORIES):
                # ``duckduckgo_search_tool()``: a tool the library makes, known by the name the library gives it.
                where = location(target.module, call)
                return _ToolSpec(_TOOL_FACTORIES[callee.last], None, where, external=callee.name)
            if isinstance(callee, External) and callee.is_library("Tool"):
                keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
                inner = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
                inner = inner or keywords.get("function")
                if inner is None:
                    return None
                spec = self.tool_of(inner, target.scope, target.line, depth + 1)
                if spec is None:
                    return None
                takes_ctx = self._bool(keywords.get("takes_ctx"), target.scope, target.line)
                if takes_ctx is not None:
                    spec.takes_ctx = takes_ctx
                self._overrides(spec, keywords, target.scope, target.line)
                return spec
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

    def __init__(self, extractor: AgentExtractor, agent: _AgentSite, ids: dict[int, str]) -> None:
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
        if self.call.args and not isinstance(self.call.args[0], ast.Starred):
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
        blueprint_id = self.ids[id(self.call)]
        name = self._text_argument("name")
        toolsets, tools, delegates = self._toolsets()
        return AgentBlueprint(
            id=blueprint_id,
            name=name,
            label=name or self.agent.site.bound_to or f"agent-{blueprint_id[1:]}",
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
            delegates=delegates,
            warnings=self.warnings,
        )

    def _text_argument(self, key: str) -> str | None:
        node = self.arguments.get(key)
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        value = string(self.resolver, node, *self.where(node))
        if value is None:
            self.warn(f"the {key} is computed by `{brief(self.text(node))}`, so it is only known when the code runs")
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
            return ModelInfo(id=value, name=name, provider=provider, offline=offline, expression=None)
        text = self.text(node)
        name: str | None = None
        provider = None
        target = self.resolver.resolve(node, *self.where(node))
        if isinstance(unwrap(node), ast.Name | ast.Attribute) and isinstance(target, Expression):
            # ``Agent(model)``: what ``model`` was assigned says more than its name does.
            text = target.module.text(target.node)
        variable: str | None = None
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
                return ModelInfo(id=None, name=rest, provider=written, offline=offline, expression=text)
        if isinstance(target, Expression):
            environment = self._environment(target)
            if environment is not None:
                variable, default = environment
                if default is not None:
                    provider, separator, name = default.partition(":")
                    if not separator:
                        provider, name = None, default
                    self.warn(
                        f"the model is read from the environment variable `{variable}` when the code runs; "
                        f"`{default}` is the default the source gives"
                    )
                    offline = provider in _compat.OFFLINE_PROVIDERS
                    return ModelInfo(id=None, name=name, provider=provider, offline=offline, expression=text)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            callee = self.resolver.callee(target.node, target.scope, target.line)
            if isinstance(callee, External) and callee.is_library(*_OFFLINE_MODELS):
                provider = _OFFLINE_MODELS[callee.last]
            elif isinstance(callee, External) and callee.name.startswith("pydantic_ai.models."):
                # One of Pydantic AI's model classes: ``OpenAIChatModel("gpt-5.2", provider=...)`` names its model.
                call = target.node
                spelled = call.args[0] if call.args and not isinstance(call.args[0], ast.Starred) else None
                spelled = spelled or next((item.value for item in call.keywords if item.arg == "model_name"), None)
                if spelled is not None:
                    name = string(self.resolver, spelled, target.scope, target.line)
                if spelled is not None and name is None:
                    named = self.resolver.resolve(spelled, target.scope, target.line)
                    environment = self._environment(named) if isinstance(named, Expression) else None
                    if environment is not None and environment[1] is not None:
                        variable, name = environment
                        self.warn(
                            f"the model name is read from the environment variable `{variable}` when the code "
                            f"runs; `{name}` is the default the source gives"
                        )
                    elif environment is not None:
                        variable = environment[0]
        if provider is None and name is None:
            if variable is not None:
                self.warn(
                    f"the model is read from the environment variable `{variable}`, so it is only known "
                    "when the code runs"
                )
            else:
                self.warn(f"the model is set by `{brief(text)}`, so it is only known when the code runs")
        return ModelInfo(id=None, name=name or text, provider=provider, offline=provider is not None, expression=text)

    def _environment(self, value: Expression) -> tuple[str, str | None] | None:
        """``(variable, default)`` when an expression reads an environment variable, with the default it names.

        ``os.getenv("MODEL", "openai:gpt-5.2")``, ``os.environ.get("MODEL")``,
        ``os.environ["MODEL"]`` and ``os.getenv("MODEL") or "openai:gpt-5.2"``.
        """
        node = value.node
        fallback: str | None = None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
            fallback = string(self.resolver, node.values[1], value.scope, value.line)
            if fallback is None:
                return None
            node = node.values[0]
        if isinstance(node, ast.Subscript):
            container = self.resolver.resolve(node.value, value.scope, value.line)
            if isinstance(container, External) and container.name == "os.environ":
                variable = string(self.resolver, node.slice, value.scope, value.line)
                return None if variable is None else (variable, fallback)
            return None
        if not isinstance(node, ast.Call) or not node.args or isinstance(node.args[0], ast.Starred):
            return None
        callee = self.resolver.callee(node, value.scope, value.line)
        if not isinstance(callee, External) or callee.name not in _ENVIRONMENT_READERS:
            return None
        variable = string(self.resolver, node.args[0], value.scope, value.line)
        if variable is None:
            return None
        given = (
            node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "default"), None)
        )
        if given is not None and not isinstance(given, ast.Starred):
            default = string(self.resolver, given, value.scope, value.line)
            if default is None and not (isinstance(given, ast.Constant) and given.value is None):
                return None  # a default that is itself computed
            return variable, default if default is not None else fallback
        return variable, fallback

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
        return self.text(node)

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
        node = self.arguments.get(kind)
        if node is None:
            return []
        entries = self._listed(node, *self.where(node), "instructions" if kind == "instructions" else "system prompts")
        if entries is None:
            entries = [Expression(node, *self.where(node))]
        prompts = (self._prompt(kind, entry) for entry in entries)
        return [prompt for prompt in prompts if prompt is not None]

    def _prompt(self, kind: PromptKind, entry: Expression) -> Instruction | None:
        node = entry.node
        where = location(entry.module, node)
        if isinstance(node, ast.Constant) and node.value is None:
            return None
        value = string(self.resolver, node, entry.scope, entry.line)
        if value is not None:
            return Instruction(kind=kind, dynamic=False, text=value, function=None, expression=None, location=where)
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
        if isinstance(target, Expression) and isinstance(target.node, ast.JoinedStr):
            template = fstring_template(target.node)
            return Instruction(kind=kind, dynamic=True, text=template, function=None, expression=None, location=where)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            # ``PROMPT.format(date=today())``: the text with its placeholders still in it, as for an f-string.
            method = target.node.func
            if isinstance(method, ast.Attribute) and method.attr == "format":
                template = string(self.resolver, method.value, target.scope, target.line)
                if template is not None:
                    return Instruction(
                        kind=kind, dynamic=True, text=template, function=None, expression=None, location=where
                    )
        text = entry.module.text(node)
        what = "instructions" if kind == "instructions" else "system prompts"
        if isinstance(target, External):
            self.warn(
                f"one of the {what} is `{brief(text)}` ({target.name}), which is defined outside the analysed "
                "source; whether it is text or a function is not known"
            )
        elif not isinstance(node, ast.Lambda):
            self.warn(f"one of the {what} is the expression `{brief(text)}`; its text is only known at run time")
        return Instruction(kind=kind, dynamic=True, text=None, function=None, expression=text, location=where)

    # -------------------------------------------------------------- output

    def _output(self) -> OutputInfo:
        # ``result_type`` is what ``output_type`` was called in early releases; code written then is read too.
        node = self.arguments.get("output_type", self.arguments.get("result_type"))
        if node is None:
            return OutputInfo(
                mode="text",
                allows_text=True,
                type_name="str",
                json_schema={"type": "string"},
                tool_names=[],
                location=None,
            )
        builder = SchemaBuilder(self.resolver)
        modes: list[str] = []
        labels: list[str] = []
        schemas: list[dict[str, Any]] = []
        self._output_member(Expression(node, *self.where(node)), builder, modes, labels, schemas, 0)
        if not modes or "tool" in modes:
            mode = "tool" if modes else "unknown"
        else:
            mode = modes[0] if len(set(modes)) == 1 else "auto"
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
                else f"the output type {names} is defined outside the analysed source, so its schema is not shown"
            )
        if document is not None and set(document) == {"x-python-type"}:
            document = None
        where = builder.locations[0] if builder.locations else None
        return OutputInfo(
            mode=mode,
            allows_text=True if mode == "text" else None,
            type_name=" | ".join(labels) or None,
            json_schema=document,
            tool_names=[],
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
                if isinstance(callee, External) and callee.is_library(*_OUTPUT_MODES):
                    wrapped = inner.args[0] if inner.args and not isinstance(inner.args[0], ast.Starred) else None
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
        modes.append("auto")
        schemas.append(builder.annotation(node, entry.scope, entry.line))

    # -------------------------------------------------------- capabilities

    def _capabilities(self) -> list[CapabilityInfo]:
        node = self.arguments.get("capabilities")
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return []
        entries = self._listed(node, *self.where(node), "capabilities")
        if entries is None:
            self.warn(
                f"the capabilities are built by `{brief(self.text(node))}`; they are only known when the code runs"
            )
            return []
        found: list[CapabilityInfo] = []
        for entry in entries:
            name = self._class_name(entry)
            if name is None:
                name = entry.module.text(entry.node)
                self.warn(f"a capability is built by `{brief(name)}`; its type is only known when the code runs")
            found.append(CapabilityInfo(name=name, builtin=name in _compat.BUILTIN_CAPABILITIES))
        return found

    def _class_name(self, entry: Expression) -> str | None:
        """The class an expression constructs (or is), when that can be read."""
        target = self.resolver.resolve(entry.node, entry.scope, entry.line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            target = self.resolver.callee(target.node, target.scope, target.line)
        if isinstance(target, External):
            return target.last if target.last[:1].isupper() else None
        if isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
            return target.node.name
        return None

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
        node = self.arguments.get("retries")
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
        node = self.arguments.get("model_settings")
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
                is_settings = isinstance(callee, External) and callee.last.endswith("Settings")
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
        text = entry.module.text(entry.node)
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
                elif self.extractor.toolsets.get(id(call)) is None:
                    # Any other class called ``...Toolset`` that is handed a toolset wraps that toolset:
                    # ``CodeExecutionToolset(toolset=tools)``, or a project's own subclass of a wrapper.
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
            site = self.extractor.toolsets.get(id(call))
            callee = self.resolver.callee(call, current.scope, current.line)
            toolset_id = self._keyword_string(call, "id", current)
            result.toolset_id = toolset_id
            result.where = location(current.module, call)
            if site is not None:
                result.kind, result.opaque = "function", False
                result.label = "FunctionToolset" + (f" {toolset_id!r}" if toolset_id else "")
                result.tools = [*self._constructor_tools(call, current), *site.tools]
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
                if "MCP" in name or (isinstance(callee, External) and callee.name.startswith("pydantic_ai.mcp")):
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
        """The name of a class that is a toolset by its name, or by a base class from Pydantic AI."""
        if isinstance(callee, External):
            return callee.last if callee.last.endswith("Toolset") else None
        if not isinstance(callee, Definition) or not isinstance(callee.node, ast.ClassDef):
            return None
        if callee.node.name.endswith("Toolset"):
            return callee.node.name
        for base in callee.node.bases[:_MAX_WRAPPERS]:
            written = base.value if isinstance(base, ast.Subscript) else base
            parent = self.resolver.resolve(written, callee.scope, callee.node.lineno)
            if isinstance(parent, External) and parent.is_library_class() and parent.last.endswith("Toolset"):
                return callee.node.name
        return None

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
                if id(target.node) in self.extractor.toolsets:
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
                function=spec.external,
                origin="code",
                location=spec.where,
            )
        node = function.node
        assert not isinstance(node, ast.ClassDef)
        description, parameter_descriptions = describe(
            ast.get_docstring(node, clean=False), has_return_annotation=node.returns is not None
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
            if isinstance(node.func, ast.Attribute) and node.func.attr in RUN_METHODS:
                candidates.append(node.func.value)
            candidates.extend(argument for argument in node.args if isinstance(argument, ast.Name | ast.Attribute))
            candidates.extend(
                keyword.value for keyword in node.keywords if isinstance(keyword.value, ast.Name | ast.Attribute)
            )
            for candidate in candidates:
                agent = self.extractor.agent_at(unwrap(candidate), body, node.lineno)
                if agent is not None:
                    agent_id = self.ids[id(agent.site.node)]
                    if agent_id not in found:
                        found.append(agent_id)
        return found
