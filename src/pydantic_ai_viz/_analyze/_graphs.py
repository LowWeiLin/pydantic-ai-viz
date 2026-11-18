"""Graph blueprints from source: a ``GraphBuilder`` and the edges added to it, or a ``Graph(nodes=[...])``.

The builder API is a small language: ``builder.edge_from(a).label("x").to(b)``,
``builder.decision().branch(builder.match(T).to(c))`` and so on. This module reads
that language where it is written out as nested calls, mirroring how
pydantic-graph numbers the nodes it creates on its own (decisions, forks, joins).

Edges that pydantic-graph infers from return annotations are inferred here the
same way: a ``BaseNode`` class goes where its ``run`` method says it returns, and
so does a step function whose return type names only nodes.

The older way of writing a graph, ``Graph(nodes=[A, B])`` over ``BaseNode``
classes, is read as well, although the pydantic-graph that is installed may no
longer run it: the classes are the nodes and their return types are the edges.

A graph can also be assembled in ways no reader of the source can follow: edges
added in a loop, paths built by helper functions, a node class imported from a
package that is not analysed. Whatever was not understood is named in a warning
that points at ``--import``; the nodes and edges that were understood are still
reported.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .. import _compat
from .._models import GraphBlueprint, GraphEdge, GraphNode, SourceLocation
from ._agents import location
from ._resolve import Definition, Expression, External, Project, Resolved, Resolver
from ._source import CallSite, Module, Scope
from ._values import string

START, END = "__start__", "__end__"
_MAX_CHAIN = 64
_MAX_DEPTH = 8
_MAX_HINT_MEMBERS = 64
_MAX_HINT_CHARS = 500
_MAX_REACHABLE = 2_000
_TYPING = frozenset({"typing", "typing_extensions"})
_USE_IMPORT = "run `pydantic-ai-viz view --import` on trusted code to see the whole graph"
_RUN_METHODS = frozenset({"run", "run_sync", "iter"})
"""Methods of a ``Graph(nodes=[...])`` whose first argument is the node the run starts at."""


@dataclass(slots=True, eq=False)
class _Node:
    kind: str
    base: str
    """The id as written, or the name pydantic-graph derives a generated id from."""
    generated: bool
    label: str | None = None
    note: str | None = None
    where: SourceLocation | None = None
    id: str = ""
    definition: Definition | None = None
    """The step's function or the ``BaseNode`` class: where a return annotation can name what follows."""


@dataclass(slots=True, eq=False)
class _Edge:
    source: _Node
    target: _Node
    label: str | None


@dataclass(slots=True, eq=False)
class _BuilderSite:
    """A ``GraphBuilder(...)`` or ``Graph(nodes=[...])`` construction and what was read of its graph."""

    site: CallSite
    classic: bool = False
    """True for ``Graph(nodes=[...])``: the node classes are listed and nothing is added afterwards."""
    built_as: str | None = None
    """The variable that ``builder.build()`` is assigned to."""
    start: _Node = field(default_factory=lambda: _Node("start", START, generated=False))
    end: _Node = field(default_factory=lambda: _Node("end", END, generated=False))
    steps: dict[int, _Node] = field(default_factory=dict[int, _Node])
    """``id`` of a step's function, of a ``builder.step(...)`` call or of a node class -> its node."""
    made: dict[int, _Node] = field(default_factory=dict[int, _Node])
    """``id`` of a ``builder.decision(...)`` or ``builder.join(...)`` call -> its node."""
    nodes: list[_Node] = field(default_factory=list[_Node])
    edges: list[_Edge] = field(default_factory=list[_Edge])
    unreadable: list[str] = field(default_factory=list[str])
    """Source text of each part of a builder graph that could not be read."""
    remarks: list[str] = field(default_factory=list[str])
    """Warnings in full, about a ``Graph(nodes=[...])``."""
    starts: list[_Node] = field(default_factory=list[_Node])
    """The source of every path added, once per path: a node that starts two paths runs both."""
    inferred: list[_Node] = field(default_factory=list[_Node])
    """Nodes whose return annotation has been read for the edges it implies."""

    def insert(self, node: _Node) -> _Node:
        if not any(node is known for known in self.nodes):
            self.nodes.append(node)
        return node

    def targets(self, node: _Node) -> list[_Node]:
        return [edge.target for edge in self.edges if edge.source is node]


class GraphExtractor:
    """Finds every ``GraphBuilder`` and ``Graph(nodes=[...])`` in a project and reads the graph it makes."""

    def __init__(self, project: Project, resolver: Resolver) -> None:
        self.project = project
        self.resolver = resolver
        self.builders: dict[int, _BuilderSite] = {}

    # -------------------------------------------------------------- driver

    def find(self) -> None:
        for module in self.project.modules:
            for site in sorted(module.calls, key=lambda item: (item.node.lineno, item.node.col_offset)):
                callee = self.resolver.callee(site.node, site.scope)
                if not isinstance(callee, External):
                    continue
                if callee.is_library("GraphBuilder"):
                    self.builders[id(site.node)] = _BuilderSite(site)
                elif callee.is_library("Graph") and any(keyword.arg == "nodes" for keyword in site.node.keywords):
                    self.builders[id(site.node)] = _BuilderSite(site, classic=True)
        if not self.builders:
            return
        for module in self.project.modules:
            self._steps(module)
        for builder in self.builders.values():
            if builder.classic:
                self._classic(builder)
        for module in self.project.modules:
            for site in sorted(module.calls, key=lambda item: (item.node.lineno, item.node.col_offset)):
                self._statement(site)

    def blueprints(self) -> list[GraphBlueprint]:
        return [self._blueprint(builder, f"g{index}") for index, builder in enumerate(self.builders.values(), start=1)]

    def builder_id(self, node: ast.expr, scope: Scope, line: int | None = None) -> str | None:
        """Blueprint id of the graph an expression stands for: the builder, or what it built."""
        target = self.resolver.resolve(node, scope, line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            call = target.node
            builder = self.builders.get(id(call))
            if builder is None and isinstance(call.func, ast.Attribute) and call.func.attr == "build":
                builder = self._builder(call.func.value, target.scope, target.line)
            if builder is not None:
                return f"g{list(self.builders.values()).index(builder) + 1}"
        return None

    def _builder(self, node: ast.expr, scope: Scope, line: int) -> _BuilderSite | None:
        target = self.resolver.resolve(node, scope, line)
        if isinstance(target, Expression) and isinstance(target.node, ast.Call):
            return self.builders.get(id(target.node))
        return None

    # --------------------------------------------------------------- steps

    def _steps(self, module: Module) -> None:
        """Register ``@builder.step`` and ``@builder.stream`` functions as step nodes."""
        for decorated in module.decorated:
            for decorator in decorated.node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                method = call.func if call is not None else decorator
                if not isinstance(method, ast.Attribute) or method.attr not in ("step", "stream"):
                    continue
                builder = self._builder(method.value, decorated.scope, decorated.node.lineno)
                if builder is None:
                    continue
                node = _Node("step", decorated.node.name, generated=False)
                node.where = location(module, decorated.node)
                node.definition = Definition(decorated.node, decorated.scope)
                if call is not None:
                    self._step_options(node, call, decorated.scope)
                builder.steps[id(decorated.node)] = node

    def _step_options(self, node: _Node, call: ast.Call, scope: Scope) -> None:
        for keyword in call.keywords:
            if keyword.arg == "label":
                node.label = string(self.resolver, keyword.value, scope, call.lineno)
            elif keyword.arg == "node_id":
                node.base = string(self.resolver, keyword.value, scope, call.lineno) or node.base

    # ---------------------------------------------------------- statements

    def _statement(self, site: CallSite) -> None:
        """Read one call on a builder: ``add``, ``add_edge``, ``add_mapping_edge`` or ``x = builder.build()``."""
        call = site.node
        method = call.func
        if not isinstance(method, ast.Attribute):
            return
        if method.attr not in ("add", "add_edge", "add_mapping_edge", "build", "add_reachable", *_RUN_METHODS):
            return
        builder = self._builder(method.value, site.scope, call.lineno)
        if builder is None:
            return
        context = Expression(call, site.scope, call.lineno)
        if builder.classic or method.attr in _RUN_METHODS:
            if builder.classic and method.attr in _RUN_METHODS:
                self._classic_start(builder, call, context)
            return
        if method.attr == "build":
            builder.built_as = builder.built_as or site.bound_to
            return
        if not site.statement:
            return
        before = len(builder.edges)
        self._add(builder, site, method.attr, context)
        # As pydantic-graph does after every addition: a step that was just made a destination
        # and whose return type names only nodes goes on to those nodes.
        for destination in _unique([edge.target for edge in builder.edges[before:]]):
            if destination.kind == "step" and not self._is_class_node(destination):
                self._infer(builder, destination)

    def _add(self, builder: _BuilderSite, site: CallSite, method: str, context: Expression) -> None:
        call = site.node
        if method == "add":
            for argument in call.args:
                if isinstance(argument, ast.Starred):
                    builder.unreadable.append(site.scope.module.text(argument))
                else:
                    self._path(builder, Expression(argument, site.scope, call.lineno))
        elif method == "add_edge":
            arguments = _arguments(call, ("source", "destination"))
            source, destination = arguments.get("source"), arguments.get("destination")
            if source is None or destination is None:
                builder.unreadable.append(site.scope.module.text(call))
                return
            label = arguments.get("label")
            self._connect(
                builder,
                [self._sub(context, source)],
                [self._sub(context, destination)],
                None if label is None else string(self.resolver, label, site.scope, call.lineno),
                context,
            )
        elif method == "add_mapping_edge":
            arguments = _arguments(call, ("source", "map_to"))
            source, destination = arguments.get("source"), arguments.get("map_to")
            if source is None or destination is None:
                builder.unreadable.append(site.scope.module.text(call))
                return
            origin = self._node(builder, self._sub(context, source), 0)
            target = self._node(builder, self._sub(context, destination), 0)
            if origin is None or target is None:
                builder.unreadable.append(site.scope.module.text(call))
                return
            fork = self._fork("map", arguments.get("fork_id"), context)
            builder.starts.append(origin)
            builder.insert(origin)
            builder.insert(target)
            builder.insert(fork)
            builder.edges.append(_Edge(origin, fork, self._text(arguments.get("pre_map_label"), context)))
            builder.edges.append(_Edge(fork, target, self._text(arguments.get("post_map_label"), context)))
        else:
            self._reachable(builder, call, context)

    def _sub(self, parent: Expression, node: ast.expr) -> Expression:
        return Expression(node, parent.scope, parent.line)

    def _text(self, node: ast.expr | None, context: Expression) -> str | None:
        return None if node is None else string(self.resolver, node, context.scope, context.line)

    # --------------------------------------------------------------- paths

    def _chain(self, expression: Expression) -> tuple[list[tuple[str, ast.Call]], Expression] | None:
        """Unroll ``root.a(...).b(...)`` into its method calls, innermost first, and the root expression."""
        target: Resolved = self.resolver.resolve(expression.node, expression.scope, expression.line)
        if not isinstance(target, Expression):
            return None
        calls: list[tuple[str, ast.Call]] = []
        node = target.node
        for _ in range(_MAX_CHAIN):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.append((node.func.attr, node))
                node = node.func.value
            else:
                calls.reverse()
                return calls, Expression(node, target.scope, target.line)
        return None

    def _path(self, builder: _BuilderSite, expression: Expression) -> None:
        """One argument of ``builder.add``: ``builder.edge_from(...)`` followed by steps that end in ``.to(...)``."""
        text = expression.module.text(expression.node)
        chain = self._chain(expression)
        if chain is None or not chain[0] or chain[0][0][0] not in ("edge_from", "node"):
            builder.unreadable.append(text)
            return
        calls, root = chain
        if self._builder(root.node, root.scope, root.line) is not builder:
            builder.unreadable.append(text)
            return
        context = Expression(expression.node, root.scope, root.line)
        if calls[0][0] == "node":
            # ``builder.node(Class)``: the paths out of a ``BaseNode`` class, from what its ``run`` returns.
            arguments = calls[0][1].args
            node = (
                self._node(builder, self._sub(context, arguments[0]), 0)
                if len(calls) == 1 and len(arguments) == 1 and not isinstance(arguments[0], ast.Starred)
                else None
            )
            if node is None or not self._is_class_node(node) or not self._infer(builder, node, again=True):
                builder.unreadable.append(text)
            return
        sources: list[_Node] = []
        for argument in calls[0][1].args:
            node = None if isinstance(argument, ast.Starred) else self._node(builder, self._sub(context, argument), 0)
            if node is None:
                builder.unreadable.append(text)
                return
            sources.append(builder.insert(node))
        if self._walk(builder, sources, calls[1:], context, 0):
            builder.starts.extend(sources)
        else:
            builder.unreadable.append(text)

    def _walk(
        self,
        builder: _BuilderSite,
        sources: list[_Node],
        calls: list[tuple[str, ast.Call]],
        context: Expression,
        depth: int,
    ) -> bool:
        """Follow ``.label()``, ``.map()``, ``.transform()`` and ``.to()`` from ``sources``; False if unreadable."""
        label: str | None = None
        forks: list[_Node] = []
        ended = False
        for name, call in calls:
            if ended:
                return False
            if name == "label":
                label = self._text(call.args[0], context) if call.args else None
            elif name == "transform":
                continue
            elif name == "map":
                fork = self._fork("map", _arguments(call, ()).get("fork_id"), context)
                builder.edges.extend(_Edge(source, fork, label) for source in sources)
                forks.append(fork)
                sources, label = [fork], None
            elif name == "to":
                targets: list[_Node] = []
                for argument in call.args:
                    target = (
                        None
                        if isinstance(argument, ast.Starred)
                        else self._node(builder, self._sub(context, argument), depth)
                    )
                    if target is None:
                        return False
                    targets.append(target)
                if not targets:
                    return False
                if len(targets) > 1:
                    fork = self._fork("broadcast", _arguments(call, ()).get("fork_id"), context)
                    builder.edges.extend(_Edge(source, fork, label) for source in sources)
                    forks.append(fork)
                    sources, label = [fork], None
                for target in targets:
                    builder.edges.extend(_Edge(source, target, label) for source in sources)
                ended = True
                if depth == 0:
                    # Inside a decision branch nothing is inserted here: the decision goes in first,
                    # and what its branches lead to follows it (see ``_insert_destination``).
                    for target in targets:
                        self._insert_destination(builder, target)
                    for fork in forks:
                        builder.insert(fork)
            else:
                return False
        return ended

    def _connect(
        self,
        builder: _BuilderSite,
        sources: list[Expression],
        targets: list[Expression],
        label: str | None,
        context: Expression,
    ) -> None:
        origins = [self._node(builder, source, 0) for source in sources]
        ends = [self._node(builder, target, 0) for target in targets]
        if any(node is None for node in (*origins, *ends)):
            builder.unreadable.append(context.module.text(context.node))
            return
        for origin in origins:
            if origin is not None:
                builder.starts.append(origin)
                builder.insert(origin)
        for end in ends:
            if end is not None:
                self._insert_destination(builder, end)
                builder.edges.extend(_Edge(origin, end, label) for origin in origins if origin is not None)

    def _insert_destination(self, builder: _BuilderSite, node: _Node) -> None:
        """Insert a destination, then whatever its decision branches lead to, in pydantic-graph's order."""
        stack = [node]
        seen: list[_Node] = []
        while stack:
            current = stack.pop()
            if any(current is known for known in seen):
                continue
            seen.append(current)
            builder.insert(current)
            if current.kind == "decision" or (current.kind in ("map", "broadcast") and current is not node):
                # Depth first, branches in the order written.
                stack.extend(reversed([edge.target for edge in builder.edges if edge.source is current]))

    # --------------------------------------------------------------- nodes

    def _fork(self, kind: str, given: ast.expr | None, context: Expression) -> _Node:
        fork_id = self._text(given, context)
        return _Node(kind, fork_id or kind, generated=fork_id is None, where=location(context.module, context.node))

    def _node(self, builder: _BuilderSite, expression: Expression, depth: int) -> _Node | None:
        """The node an expression in a path stands for, creating decision and join nodes on first sight."""
        node = expression.node
        if (
            isinstance(node, ast.Attribute)
            and node.attr in ("start_node", "end_node")
            and self._builder(node.value, expression.scope, expression.line) is builder
        ):
            return builder.start if node.attr == "start_node" else builder.end
        target = self.resolver.resolve(node, expression.scope, expression.line)
        if isinstance(target, Definition):
            known = builder.steps.get(id(target.node))
            if known is None and isinstance(target.node, ast.ClassDef) and self._is_node_class(target, 0):
                # A ``BaseNode`` class used as a node; pydantic-graph names it after the class.
                known = _Node("step", target.node.name, generated=False, where=location(target.module, target.node))
                known.definition = target
                builder.steps[id(target.node)] = known
            return known
        if not isinstance(target, Expression) or depth > _MAX_DEPTH:
            return None
        inner = target.node
        if isinstance(inner, ast.Attribute) and inner.attr in ("start_node", "end_node"):
            if self._builder(inner.value, target.scope, target.line) is builder:
                return builder.start if inner.attr == "start_node" else builder.end
            return None
        if not isinstance(inner, ast.Call):
            return None
        known = builder.steps.get(id(inner)) or builder.made.get(id(inner))
        if known is not None:
            return known
        chain = self._chain(target)
        if chain is None or not chain[0]:
            return None
        calls, root = chain
        if self._builder(root.node, root.scope, root.line) is not builder:
            return None
        name, call = calls[0]
        where = location(target.module, call)
        options = _arguments(call, ())
        node_id = self._text(options.get("node_id"), target)
        if name in ("step", "stream") and len(calls) == 1 and call.args:
            # The call form: ``write = builder.step(write_draft, label="Write")``.
            function = self.resolver.resolve(call.args[0], target.scope, target.line)
            if not isinstance(function, Definition) or isinstance(function.node, ast.ClassDef):
                return None
            made = _Node("step", node_id or function.node.name, generated=False, where=where)
            made.label = self._text(options.get("label"), target)
            made.definition = function
            builder.steps[id(inner)] = made
            return made
        if name == "join" and len(calls) == 1 and call.args:
            reducer = self.resolver.resolve(call.args[0], target.scope, target.line)
            if isinstance(reducer, Definition):
                base = reducer.node.name
            elif isinstance(reducer, External):
                base = reducer.last
            else:
                return None
            made = _Node("join", node_id or base, generated=node_id is None, where=where)
            builder.made[id(inner)] = made
            return made
        if name == "decision":
            made = _Node("decision", node_id or "decision", generated=node_id is None, where=where)
            made.note = self._text(options.get("note"), target)
            builder.made[id(inner)] = made
            for branch_name, branch in calls[1:]:
                if branch_name != "branch" or len(branch.args) != 1 or isinstance(branch.args[0], ast.Starred):
                    return None
                if not self._branch(builder, made, self._sub(target, branch.args[0]), depth + 1):
                    return None
            return made
        return None

    def _branch(self, builder: _BuilderSite, decision: _Node, expression: Expression, depth: int) -> bool:
        """``builder.match(Type).label("x").to(node)``: one way out of a decision."""
        chain = self._chain(expression)
        if chain is None or not chain[0] or chain[0][0][0] not in ("match", "match_node"):
            return False
        calls, root = chain
        if self._builder(root.node, root.scope, root.line) is not builder:
            return False
        context = Expression(expression.node, root.scope, root.line)
        if calls[0][0] == "match_node":
            # ``builder.match_node(Class)`` is a whole branch: it leads to that node class.
            arguments = calls[0][1].args
            if len(calls) != 1 or not arguments or isinstance(arguments[0], ast.Starred):
                return False
            node = self._node(builder, self._sub(context, arguments[0]), depth)
            if node is None or not self._is_class_node(node):
                return False
            builder.edges.append(_Edge(decision, node, None))
            return True
        return self._walk(builder, [decision], calls[1:], context, depth)

    # ------------------------------------------- edges from return annotations

    @staticmethod
    def _is_class_node(node: _Node) -> bool:
        return node.definition is not None and isinstance(node.definition.node, ast.ClassDef)

    def _is_node_class(self, definition: Definition, depth: int) -> bool:
        """Whether a class of the analysed files derives from pydantic-graph's ``BaseNode``."""
        node = definition.node
        if not isinstance(node, ast.ClassDef) or depth > _MAX_DEPTH:
            return False
        if "get_node_id" in definition.body.bindings:
            return False  # the class names its node itself, in code
        for base in node.bases:
            target = self.resolver.resolve(base.value if isinstance(base, ast.Subscript) else base, definition.scope)
            if isinstance(target, External) and target.is_library("BaseNode"):
                return True
            if isinstance(target, Definition) and self._is_node_class(target, depth + 1):
                return True
        return False

    def _return_hint(self, definition: Definition, depth: int = 0) -> Expression | None:
        """The return annotation of a step function, or of the ``run`` method of a node class."""
        node = definition.node
        if not isinstance(node, ast.ClassDef):
            return None if node.returns is None else Expression(node.returns, definition.scope, node.lineno)
        body = definition.body
        found = body.bindings.get("run")
        if found:
            method = found[-1].node
            if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef) and method.returns is not None:
                return Expression(method.returns, body, method.lineno)
            return None
        if depth < _MAX_DEPTH:
            for base in node.bases:
                target = self.resolver.resolve(
                    base.value if isinstance(base, ast.Subscript) else base, definition.scope
                )
                if isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
                    inherited = self._return_hint(target, depth + 1)
                    if inherited is not None:
                        return inherited
        return None

    def _typing_name(self, node: ast.expr, context: Expression) -> str | None:
        """``Union``, ``Optional`` or ``Annotated`` when ``node`` is that ``typing`` construct."""
        target = self.resolver.resolve(node, context.scope, context.line)
        if isinstance(target, External) and target.name.partition(".")[0] in _TYPING:
            return target.last
        return None

    def _hint_nodes(self, builder: _BuilderSite, hint: Expression) -> list[_Node] | None:
        """The nodes a return annotation names. ``None`` unless every member of the union is a node."""
        members = self._hint_members(hint)
        found: list[_Node] = []
        for member in members or []:
            destination = self._hint_node(builder, member)
            if destination is None:
                return None
            if not any(destination is known for known in found):
                found.append(destination)
        return found or None

    def _hint_members(self, hint: Expression) -> list[Expression] | None:
        """The members of a return annotation, with unions, string annotations and aliases opened up."""
        pending = [hint]
        found: list[Expression] = []
        for _ in range(_MAX_HINT_MEMBERS):
            if not pending:
                return found
            member = pending.pop(0)
            node = member.node
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parsed = _parse_annotation(node.value)
                if parsed is None:
                    return None
                pending.insert(0, self._sub(member, parsed))
                continue
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                pending[:0] = [self._sub(member, node.left), self._sub(member, node.right)]
                continue
            if isinstance(node, ast.Subscript) and self._typing_name(node.value, member) == "Union":
                items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                pending[:0] = [self._sub(member, item) for item in items]
                continue
            if isinstance(node, ast.Name):
                alias = self.resolver.resolve(node, member.scope, member.line)
                if isinstance(alias, Expression) and isinstance(alias.node, ast.BinOp | ast.Subscript | ast.Constant):
                    pending.insert(0, alias)  # ``Next = Check | End[int]``
                    continue
            found.append(member)
        return None

    def _hint_node(
        self, builder: _BuilderSite, member: Expression, outside: dict[str, _Node] | None = None
    ) -> _Node | None:
        """One member of a return annotation as a node: ``End[...]``, a node class, or an annotated step.

        ``outside`` maps the dotted names of nodes defined outside the analysed files to their nodes.
        """
        node = member.node
        extras: list[ast.expr] = []
        if isinstance(node, ast.Subscript) and self._typing_name(node.value, member) == "Annotated":
            items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if not items:
                return None
            node, extras = items[0], list(items[1:])
        origin = node.value if isinstance(node, ast.Subscript) else node
        target = self.resolver.resolve(origin, member.scope, member.line)
        if isinstance(target, External):
            if target.is_library("End"):
                return builder.end
            if target.is_library("StepNode", "JoinNode"):
                # ``Annotated[StepNode[...], my_step]``: the step or join is named in the metadata.
                for extra in extras:
                    named = self._node(builder, self._sub(member, extra), 1)
                    if named is not None and named.kind in ("step", "join"):
                        return named
            return None if outside is None else outside.get(target.name)
        if isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
            return self._node(builder, self._sub(member, origin), 1)
        return None

    def _infer(self, builder: _BuilderSite, node: _Node, *, again: bool = False) -> bool:
        """Add the edges out of ``node`` that its return annotation implies; False when it implies none.

        One destination is an edge to it. Several are an edge to a decision that
        leads to each, which is how pydantic-graph builds it.
        """
        if any(node is known for known in builder.inferred) and not again:
            return True
        builder.inferred.append(node)
        hint = None if node.definition is None else self._return_hint(node.definition)
        destinations = None if hint is None else self._hint_nodes(builder, hint)
        if destinations is None:
            return False
        builder.starts.append(node)
        builder.insert(node)
        first = destinations[0]
        if len(destinations) > 1:
            first = _Node("decision", "decision", generated=True, where=node.where)
            builder.edges.extend(_Edge(first, destination, None) for destination in destinations)
        builder.edges.append(_Edge(node, first, None))
        self._insert_destination(builder, first)
        for destination in destinations:
            if destination.kind == "step" and not self._is_class_node(destination):
                self._infer(builder, destination)
        return True

    # ------------------------------------------------- Graph(nodes=[...])

    def _classic(self, builder: _BuilderSite) -> None:
        """Read a ``Graph(nodes=[A, B])``: each class is a node, and goes where its ``run`` says it returns."""
        call = builder.site.node
        context = Expression(call, builder.site.scope, call.lineno)
        argument = _arguments(call, ()).get("nodes")
        listed = None if argument is None else self.resolver.resolve(argument, context.scope, context.line)
        if (
            argument is None
            or not isinstance(listed, Expression)
            or not isinstance(listed.node, ast.List | ast.Tuple)
            or len(listed.node.elts) > _MAX_REACHABLE
            or any(isinstance(item, ast.Starred) for item in listed.node.elts)
        ):
            text = context.module.text(call if argument is None else argument)
            builder.remarks.append(
                f"the nodes of this graph are given by `{text}`, so they are only known when the code runs"
            )
            return
        classes: list[_Node] = []
        outside: dict[str, _Node] = {}
        for item in listed.node.elts:
            node = self._node(builder, self._sub(listed, item), 0)
            if node is None or not self._is_class_node(node):
                # Not a node class of the analysed files: it is still a node, but where it leads is not known.
                text = listed.module.text(item)
                node = _Node("step", text.rpartition(".")[2], generated=False, where=location(listed.module, item))
                imported = self.resolver.resolve(item, listed.scope, listed.line)
                if isinstance(imported, External):
                    outside[imported.name] = node
                builder.remarks.append(
                    f"the node `{text}` is not a node class defined in the analysed source, so where it leads "
                    "is not shown"
                )
            else:
                classes.append(node)
            builder.insert(node)
        for node in classes:
            hint = None if node.definition is None else self._return_hint(node.definition)
            members = None if hint is None else self._hint_members(hint)
            if not members:
                builder.remarks.append(
                    f"`{node.base}.run` does not declare what it returns in a way that can be read, so the edges "
                    f"out of `{node.base}` are not shown"
                )
                continue
            for member in members:
                destination = self._hint_node(builder, member, outside)
                if destination is None or destination.kind not in ("step", "end"):
                    builder.remarks.append(
                        f"`{node.base}.run` can return `{_member_text(member)}`, which is not a node class defined "
                        "in the analysed source, so that edge is not shown"
                    )
                    continue
                builder.insert(destination)
                builder.edges.append(_Edge(node, destination, self._edge_label(member)))

    def _edge_label(self, member: Expression) -> str | None:
        """The label of ``Annotated[Next, Edge(label="...")]``."""
        node = member.node
        if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Tuple):
            return None
        if self._typing_name(node.value, member) != "Annotated":
            return None
        for extra in node.slice.elts[1:]:
            if isinstance(extra, ast.Call):
                callee = self.resolver.callee(extra, member.scope, member.line)
                if isinstance(callee, External) and callee.is_library("Edge"):
                    label = _arguments(extra, ("label",)).get("label")
                    return self._text(label, member)
        return None

    def _classic_start(self, builder: _BuilderSite, call: ast.Call, context: Expression) -> None:
        """``graph.run(First())``: a run of the graph that says which node it starts at."""
        argument = _arguments(call, ("start_node",)).get("start_node")
        if argument is None:
            return
        started = self.resolver.resolve(argument, context.scope, context.line)
        if isinstance(started, Expression) and isinstance(started.node, ast.Call):
            started = self.resolver.callee(started.node, started.scope, started.line)
        if not isinstance(started, Definition):
            return
        node = builder.steps.get(id(started.node))
        if node is None or any(edge.source is builder.start and edge.target is node for edge in builder.edges):
            return
        if not any(builder.start is known for known in builder.nodes):
            builder.nodes.insert(0, builder.start)
        builder.edges.append(_Edge(builder.start, node, None))

    def _reachable(self, builder: _BuilderSite, call: ast.Call, context: Expression) -> None:
        """``builder.add_reachable(A, B)``: these node classes and every node their return types lead to."""
        pending: list[_Node] = []
        for argument in call.args:
            node = None if isinstance(argument, ast.Starred) else self._node(builder, self._sub(context, argument), 0)
            if node is None or not self._is_class_node(node):
                builder.unreadable.append(context.module.text(argument))
                continue
            builder.insert(node)
            pending.append(node)
        seen: list[_Node] = []
        while pending and len(seen) < _MAX_REACHABLE:
            node = pending.pop()
            if any(node is known for known in seen):
                continue
            seen.append(node)
            # Paths that are already there win over the return annotation.
            if not builder.targets(node) and self._is_class_node(node) and not self._infer(builder, node, again=True):
                builder.unreadable.append(f"{node.base}.run")
            pending.extend(reversed(builder.targets(node)))
        if any(not any(node is known for known in seen) for node in pending):
            # Stopped at the bound: say so, so that part of a graph is not taken for the whole of it.
            builder.unreadable.append(context.module.text(call))

    # ----------------------------------------------------------- blueprint

    def _blueprint(self, builder: _BuilderSite, blueprint_id: str) -> GraphBlueprint:
        call = builder.site.node
        scope = builder.site.scope
        module = scope.module
        options = _arguments(call, ())
        context = Expression(call, scope, call.lineno)
        name = self._text(options.get("name"), context)
        warnings: list[str] = []
        nodes = list(builder.nodes)
        if builder.classic:
            # The installed pydantic-graph may not run a ``Graph(nodes=[...])`` at all, so these
            # warnings do not point at --import.
            if builder.edges and not any(edge.source is builder.start for edge in builder.edges):
                _compat.add_warning(
                    warnings,
                    "the node this graph starts at is chosen where the graph is run, and no such call was found "
                    "in the analysed source",
                )
            for remark in builder.remarks:
                _compat.add_warning(warnings, remark)
        elif not builder.edges:
            # Nothing connects the steps as far as the source shows; list them so the graph is not blank.
            nodes = sorted(builder.steps.values(), key=lambda node: node.where.line if node.where else 0)
            _compat.add_warning(warnings, f"the edges of this graph could not be read from source; {_USE_IMPORT}")
        for text in builder.unreadable:
            _compat.add_warning(
                warnings,
                f"part of this graph is built by `{text}`, which cannot be read from source; {_USE_IMPORT}",
            )
        if builder.classic and any(builder.end is known for known in nodes):
            nodes = [*(node for node in nodes if node is not builder.end), builder.end]
        _number(nodes)
        all_edges = list(builder.edges)
        for source in _unique(builder.starts):
            if sum(1 for start in builder.starts if start is source) < 2 or source.kind == "broadcast":
                continue
            # A node that starts several paths runs them all: pydantic-graph puts a fork in between.
            fork = _Node("broadcast", f"{source.id}_broadcast_fork", generated=False)
            fork.id = fork.base
            nodes.append(fork)
            all_edges = [_Edge(fork, edge.target, edge.label) if edge.source is source else edge for edge in all_edges]
            all_edges.append(_Edge(source, fork, None))
        order = {id(node): index for index, node in enumerate(nodes)}
        edges = sorted(
            (edge for edge in all_edges if id(edge.source) in order and id(edge.target) in order),
            key=lambda edge: order[id(edge.source)],
        )
        wire_nodes = [
            GraphNode(id=node.id, kind=node.kind, label=node.label, note=node.note, location=node.where)
            for node in nodes
        ]
        wire_edges = [GraphEdge(source=edge.source.id, target=edge.target.id, label=edge.label) for edge in edges]
        return GraphBlueprint(
            id=blueprint_id,
            name=name,
            label=name or builder.built_as or builder.site.bound_to or f"graph-{blueprint_id[1:]}",
            source="code",
            location=location(module, call),
            state_type=self._type(options.get("state_type"), context),
            input_type=self._type(options.get("input_type"), context),
            output_type=self._type(options.get("run_end_type" if builder.classic else "output_type"), context),
            nodes=wire_nodes,
            edges=wire_edges,
            mermaid=render_mermaid(wire_nodes, wire_edges) if wire_edges else None,
            warnings=warnings,
        )

    def _type(self, node: ast.expr | None, context: Expression) -> str | None:
        """A type argument of ``GraphBuilder`` as the live blueprint names it; ``None`` when it is not given."""
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        target = self.resolver.resolve(node, context.scope, context.line)
        if isinstance(target, External):
            if target.name in ("builtins.object", "builtins.None", "types.NoneType"):
                return None
            return target.name.removeprefix("builtins.")
        if isinstance(target, Definition):
            return target.qualified_name
        return context.module.text(node)


def _arguments(call: ast.Call, positional: tuple[str, ...]) -> dict[str, ast.expr]:
    """A call's arguments by parameter name, given the names of its positional parameters."""
    found: dict[str, ast.expr] = {}
    for name, argument in zip(positional, call.args, strict=False):
        if not isinstance(argument, ast.Starred):
            found[name] = argument
    for keyword in call.keywords:
        if keyword.arg is not None:
            found[keyword.arg] = keyword.value
    return found


def _member_text(member: Expression) -> str:
    """Source text of one member of a return annotation; one from a string annotation is unparsed."""
    if hasattr(member.node, "lineno"):
        return member.module.text(member.node)
    try:
        return ast.unparse(member.node)[:80]
    except (RecursionError, ValueError):
        return "..."


def _unique(nodes: list[_Node]) -> list[_Node]:
    """``nodes`` without repeats, by identity, in first-seen order."""
    found: list[_Node] = []
    for node in nodes:
        if not any(node is known for known in found):
            found.append(node)
    return found


def _parse_annotation(source: str) -> ast.expr | None:
    """A string annotation as an expression, without source positions (they would be those of the string)."""
    if len(source) > _MAX_HINT_CHARS:
        return None
    try:
        parsed = ast.parse(source.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    for child in ast.walk(parsed):
        for attribute in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
            if hasattr(child, attribute):
                delattr(child, attribute)
    return parsed


def _number(nodes: list[_Node]) -> None:
    """Give generated nodes their ids the way pydantic-graph does: ``decision``, ``decision_2``, ..."""
    counts: dict[str, int] = {}
    for node in nodes:
        if node.generated:
            counts[node.base] = count = counts.get(node.base, 0) + 1
            node.id = f"{node.base}_{count}" if count > 1 else node.base
        else:
            node.id = node.base


def render_mermaid(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    """A Mermaid state diagram of the graph, laid out as ``Graph.render(direction="LR")`` lays it out."""
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    depths = {START: 0}
    queue = [START]
    while queue:
        current = queue.pop(0)
        for following in adjacency.get(current, []):
            if following not in depths:
                depths[following] = depths[current] + 1
                queue.append(following)
    far = float("inf")
    lines = ["stateDiagram-v2", "  direction LR"]
    for node in sorted(nodes, key=lambda item: (depths.get(item.id, far), item.id)):
        if node.kind == "step" or node.kind not in ("start", "end", "join", "broadcast", "map", "decision"):
            lines.append(f"  {node.id}: {node.label}" if node.label else f"  {node.id}")
        elif node.kind == "join":
            lines.append(f"  state {node.id} <<join>>")
        elif node.kind in ("broadcast", "map"):
            lines.append(f"  state {node.id} <<fork>>")
        elif node.kind == "decision":
            lines.append(f"  state {node.id} <<choice>>")
            if node.note:
                lines.append(f"  note right of {node.id}\n    {node.note}\n  end note")
    lines.append("")
    ordered = sorted(
        edges,
        key=lambda edge: (depths.get(edge.source, far), depths.get(edge.target, far), edge.source, edge.target),
    )
    for edge in ordered:
        start = "[*]" if edge.source == START else edge.source
        end = "[*]" if edge.target == END else edge.target
        lines.append(f"  {start} --> {end}: {edge.label}" if edge.label else f"  {start} --> {end}")
    return "\n".join(lines)
