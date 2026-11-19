"""Reading and parsing one source file, and the scopes and bindings found in it.

Nothing here imports or runs the file. It is decoded, handed to ``ast.parse`` and
walked. Every way that can fail (too large, undecodable, not valid Python, nested
too deeply for the parser) ends in a ``SkippedFile`` with a reason a person can read.

The scope model is deliberately small. It records, per module, function and class
body, which names are bound and to what, so that a use of a name can be traced
back to the expression, function or class it stands for. A name bound in a way
that only running the code could resolve (a loop variable, a ``with`` target, a
parameter) is recorded as opaque, which makes every lookup through it "unknown".
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import warnings
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias, cast

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef
ScopeNode: TypeAlias = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
BindingKind: TypeAlias = Literal["assign", "def", "class", "import", "loop", "opaque"]

MAX_EXPRESSION_CHARS = 200
"""Source text of an expression is cut to this many characters on the wire."""

_WHITESPACE = re.compile(r"\s+")
MUTATORS = frozenset(
    {"append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse"}  # lists
    | {"update", "setdefault", "popitem"}  # dicts
    | {"add", "discard"}  # sets
)
"""Methods that change a list, a dict or a set in place."""
_MAYBE = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar, ast.Match)
"""Statements whose body may run or not, or more than once."""
_LEAVES = (ast.Name, ast.Constant, ast.expr_context, ast.operator, ast.unaryop, ast.boolop, ast.cmpop)
"""Nodes with nothing below them that the index needs."""
_TYPE_ALIAS: type[ast.stmt] | None = getattr(ast, "TypeAlias", None)
"""The ``type X = ...`` statement, which the parser knows from Python 3.12 on."""


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """A file that could not be analysed, and why."""

    rel: str
    reason: str


@dataclass(slots=True, eq=False)
class Binding:
    """One place where a name is given a value."""

    name: str
    kind: BindingKind
    line: int
    scope: Scope
    node: ast.AST | None = None
    """``assign``: the value expression. ``def`` and ``class``: the definition. ``loop``: what is looped over."""
    module: str | None = None
    """``import``: the dotted module named in the statement (without leading dots)."""
    attr: str | None = None
    """``import``: the name taken from that module by ``from module import attr``."""
    level: int = 0
    """``import``: number of leading dots of a relative import."""


@dataclass(slots=True, eq=False)
class Mutation:
    """A statement that changes a value in place: ``tools.append(f)``, ``settings["k"] = v``, ``del names[0]``."""

    name: str
    line: int
    method: str
    """The method called (``append``, ``update``, ...), or ``[]`` for an item that is assigned or deleted."""
    call: ast.Call | None
    scope: Scope
    """The scope the statement stands in, which need not be the one that binds the name."""
    conditional: bool
    """Whether the statement stands inside an ``if``, a loop, a ``try`` or a ``match``: it may not happen."""
    elsewhere: bool = False
    """Whether it stands in another function than the one that binds the name: when it runs is not known."""
    key: ast.expr | None = None
    """``name[key] = value``: the key. ``None`` for every other change."""
    value: ast.expr | None = None
    """``name[key] = value``: the value."""


@dataclass(slots=True, eq=False)
class Scope:
    """A module, a function body or a class body."""

    kind: Literal["module", "function", "class"]
    node: ScopeNode
    parent: Scope | None
    module: Module
    qualname: str
    """Dotted path inside the module, as ``__qualname__`` would give it; empty for the module."""
    bindings: dict[str, list[Binding]] = field(default_factory=dict[str, list[Binding]])
    attributes: dict[str, list[Binding]] = field(default_factory=dict[str, list[Binding]])
    """Class scopes: ``self.name = value`` assignments made in the class's methods."""
    returns: list[ast.expr] = field(default_factory=list[ast.expr])
    """Function scopes: the value of each ``return`` directly in this function."""
    receiver: str | None = None
    """Function scopes directly inside a class: the name of the first parameter (``self``)."""
    mutations: dict[str, list[Mutation]] = field(default_factory=dict[str, list[Mutation]])
    """In-place changes to the values of names this scope binds, wherever in the file they are made."""
    attribute_mutations: dict[str, list[Mutation]] = field(default_factory=dict[str, list[Mutation]])
    """Class scopes: in-place changes to ``self.name`` made in the class's methods."""

    def bind(self, binding: Binding) -> None:
        self.bindings.setdefault(binding.name, []).append(binding)

    def enclosing_class(self) -> Scope | None:
        """The class this function scope is a method of, if any."""
        if self.kind == "function" and self.parent is not None and self.parent.kind == "class":
            return self.parent
        return None


@dataclass(slots=True, eq=False)
class CallSite:
    """A call expression and where it stands."""

    node: ast.Call
    scope: Scope
    bound_to: str | None
    """The variable (or ``self`` attribute) the call's result is assigned to, if it is assigned directly."""
    statement: bool
    """True when the call is a whole statement of its own, such as ``builder.add(...)``."""


@dataclass(slots=True, eq=False)
class Decorated:
    """A decorated function. Decorators are evaluated in the scope the function is defined in."""

    node: FunctionNode
    scope: Scope
    body: Scope


@dataclass(slots=True, eq=False)
class Module:
    """One parsed source file."""

    path: Path
    rel: str
    """Path relative to the analysed root, with forward slashes."""
    name: str
    """Dotted module name, as far as the directory layout says."""
    lines: list[str]
    scope: Scope = field(init=False)
    directory: str = field(init=False)
    """The directory the file is in, as a string: the key under which its imports are looked up."""
    calls: list[CallSite] = field(default_factory=list[CallSite])
    decorated: list[Decorated] = field(default_factory=list[Decorated])
    star_imports: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    """``from module import *`` statements at any depth, as ``(dotted name, leading dots)``."""
    imports: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    """Every module an import statement anywhere in the file may refer to, as ``(dotted name, leading dots)``.

    ``from a import b`` is listed as ``a`` and as ``a.b``, since ``b`` may be a module.
    """
    function_scopes: dict[int, Scope] = field(default_factory=dict[int, Scope])
    """``id(FunctionDef)`` -> the scope of its body."""
    class_scopes: dict[int, Scope] = field(default_factory=dict[int, Scope])
    """``id(ClassDef)`` -> the scope of its body."""

    def __post_init__(self) -> None:
        self.directory = str(self.path.parent)

    def text(self, node: ast.AST) -> str:
        """The source text of ``node`` on one line, cut to ``MAX_EXPRESSION_CHARS``."""
        return clip_expression(self.raw_text(node))

    def raw_text(self, node: ast.AST) -> str:
        """The source text of ``node`` exactly as written, or ``"..."`` when it has no position."""
        start_line: int | None = getattr(node, "lineno", None)
        end_line: int | None = getattr(node, "end_lineno", None)
        start: int | None = getattr(node, "col_offset", None)
        end: int | None = getattr(node, "end_col_offset", None)
        if start_line is None or end_line is None or start is None or end is None:
            return "..."
        if not (1 <= start_line <= end_line <= len(self.lines)):
            return "..."
        if start_line == end_line:
            return _columns(self.lines[start_line - 1], start, end)
        parts = [_columns(self.lines[start_line - 1], start, None)]
        parts.extend(self.lines[start_line : end_line - 1])
        parts.append(_columns(self.lines[end_line - 1], None, end))
        return "\n".join(parts)


def _columns(line: str, start: int | None, end: int | None) -> str:
    """Slice a line by the UTF-8 byte offsets the parser reports."""
    if line.isascii():
        return line[start:end]
    return line.encode("utf-8")[start:end].decode("utf-8", "replace")


def clip_expression(text: str, limit: int = MAX_EXPRESSION_CHARS) -> str:
    """Put source text on one line and cut it to ``limit`` characters."""
    flat = _WHITESPACE.sub(" ", text).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


# ------------------------------------------------------------------ reading


def module_name(path: Path) -> str:
    """Dotted name of the module at ``path``: its stem, prefixed by every enclosing package.

    A directory is a package when it holds an ``__init__.py``. Nothing is imported.
    """
    parts = [] if path.stem == "__init__" else [path.stem]
    directory = path.parent
    for _ in range(64):  # a deeper package tree than this is not one
        if not (directory / "__init__.py").is_file() or directory.parent == directory:
            break
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts)) or path.parent.name


def read_module(path: Path, rel: str, *, max_bytes: int) -> Module | SkippedFile:
    """Read and parse ``path``. The file is never imported or executed."""
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return SkippedFile(rel, f"it is larger than the {max_bytes:,} byte limit ({size:,} bytes)")
        data = path.read_bytes()
    except OSError as exc:
        return SkippedFile(rel, f"it cannot be read ({exc.strerror or type(exc).__name__})")
    if len(data) > max_bytes:  # the file grew, or its size could not be trusted
        return SkippedFile(rel, f"it is larger than the {max_bytes:,} byte limit")
    return parse_module(data, path, rel)


def parse_module(data: bytes, path: Path, rel: str) -> Module | SkippedFile:
    """Parse source bytes into a ``Module`` with its scopes, or say why that is not possible."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
    except (SyntaxError, UnicodeDecodeError, LookupError):
        return SkippedFile(rel, "it is not valid UTF-8 and declares no encoding that decodes it")
    # The parser treats all three line endings alike; one form keeps line numbers and text in step.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # invalid escapes and the like are the author's business
            tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        where = f" on line {exc.lineno}" if exc.lineno else ""
        return SkippedFile(rel, f"it is not valid Python ({exc.msg}{where})")
    except ValueError as exc:  # a null byte
        return SkippedFile(rel, f"it is not valid Python ({exc})")
    except (RecursionError, MemoryError):
        return SkippedFile(rel, "it is nested too deeply to parse")
    module = Module(path=path, rel=rel, name=module_name(path), lines=text.split("\n"))
    module.scope = Scope(kind="module", node=tree, parent=None, module=module, qualname="")
    try:
        indexer = _Indexer(module)
        indexer.block(tree.body, module.scope)
        indexer.settle()
    except RecursionError:
        return SkippedFile(rel, "it is nested too deeply to analyse")
    return module


# ----------------------------------------------------------------- indexing


def unwrap(node: ast.expr) -> ast.expr:
    """Look through ``await`` to the expression that is awaited."""
    for _ in range(8):
        if isinstance(node, ast.Await):
            node = node.value
        else:
            break
    return node


class _Indexer:
    """Fills in the scopes, bindings, call sites and decorated functions of a module.

    Statements are visited recursively: the grammar limits how deeply blocks can
    nest. Expressions are not, because a chain such as ``a.b.c. ... .z`` nests as
    deep as it is long; they are scanned with the iterative ``ast.walk``.
    """

    def __init__(self, module: Module) -> None:
        self.module = module
        self._mutations: list[tuple[Mutation, str | None]] = []
        """In-place changes seen so far, each with the attribute of ``self`` it changes, if it is one."""
        self._maybe = 0
        """How many conditional statements enclose the statement being visited, within its function."""

    def settle(self) -> None:
        """File every in-place change under the scope that binds the name it changes.

        Done once the whole file is indexed: a function may change a name that the
        module binds further down.
        """
        for mutation, attribute in self._mutations:
            scope = mutation.scope
            if attribute is not None:
                owner = scope.enclosing_class()
                if owner is not None:
                    mutation.elsewhere = True  # methods run in an order the class does not spell out
                    owner.attribute_mutations.setdefault(attribute, []).append(mutation)
                continue
            current: Scope | None = scope
            innermost = True
            while current is not None:
                if (current.kind != "class" or innermost) and mutation.name in current.bindings:
                    mutation.elsewhere = current is not scope
                    current.mutations.setdefault(mutation.name, []).append(mutation)
                    break
                innermost = False
                current = current.parent

    def _mutation(
        self,
        target: ast.expr,
        method: str,
        call: ast.Call | None,
        scope: Scope,
        line: int,
        item: tuple[ast.expr, ast.expr] | None = None,
    ) -> None:
        """Record that ``target`` (a name, or an attribute of ``self``) is changed in place.

        ``item`` is the key and the value of ``target[key] = value``.
        """
        key, value = item or (None, None)
        maybe = self._maybe > 0
        if isinstance(target, ast.Name):
            self._mutations.append((Mutation(target.id, line, method, call, scope, maybe, key=key, value=value), None))
        elif (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and scope.receiver is not None
            and target.value.id == scope.receiver
        ):
            change = Mutation(target.attr, line, method, call, scope, maybe, key=key, value=value)
            self._mutations.append((change, target.attr))

    def block(self, statements: list[ast.stmt], scope: Scope) -> None:
        for statement in statements:
            self.statement(statement, scope)

    def statement(self, node: ast.stmt, scope: Scope) -> None:
        line = node.lineno
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            self._function(node, scope)
        elif isinstance(node, ast.ClassDef):
            self._class(node, scope)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                self.module.imports.extend((".".join(parts[:end]), 0) for end in range(1, len(parts) + 1))
                # ``import a.b`` binds ``a``; ``import a.b as c`` binds ``c`` to ``a.b``.
                bound = alias.asname or alias.name.partition(".")[0]
                target = alias.name if alias.asname else bound
                scope.bind(Binding(bound, "import", line, scope, module=target))
        elif isinstance(node, ast.ImportFrom):
            self.module.imports.append((node.module or "", node.level))
            for alias in node.names:
                if alias.name != "*":
                    self.module.imports.append((f"{node.module or ''}.{alias.name}".strip("."), node.level))
                if alias.name == "*":
                    self.module.star_imports.append((node.module or "", node.level))
                    continue
                scope.bind(
                    Binding(
                        alias.asname or alias.name,
                        "import",
                        line,
                        scope,
                        module=node.module or "",
                        attr=alias.name,
                        level=node.level,
                    )
                )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    self._mutation(target.value, "[]", None, scope, line, item=(target.slice, node.value))
                self._assign(target, node.value, scope, line)
            self._assigned(node.targets[0], node.value, scope)
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self._assign(node.target, node.value, scope, line)
                self._expression(node.value, scope, bound_to=_target_name(node.target, scope))
            self._expression(node.annotation, scope)
        elif _TYPE_ALIAS is not None and isinstance(node, _TYPE_ALIAS):
            alias_name: object = getattr(node, "name", None)
            alias_value: object = getattr(node, "value", None)
            if isinstance(alias_name, ast.Name) and isinstance(alias_value, ast.expr):
                scope.bind(Binding(alias_name.id, "assign", line, scope, node=alias_value))
        elif isinstance(node, ast.Return):
            if node.value is not None:
                if scope.kind == "function":
                    scope.returns.append(node.value)
                self._expression(node.value, scope, bound_to=_function_name(scope))
        elif isinstance(node, ast.Expr):
            called = unwrap(node.value)
            if isinstance(called, ast.Call) and isinstance(called.func, ast.Attribute) and called.func.attr in MUTATORS:
                self._mutation(called.func.value, called.func.attr, called, scope, line)
            self._expression(node.value, scope, statement=True)
        else:
            self._compound(node, scope)

    def _compound(self, node: ast.stmt, scope: Scope) -> None:
        """Any other statement: bind what it binds opaquely, then visit what it contains."""
        line = node.lineno
        if isinstance(node, ast.AugAssign) and not isinstance(node.target, ast.Name):
            # ``self.tools += [...]`` and ``settings["k"] += 1`` change a value that a name still stands for.
            changed = node.target.value if isinstance(node.target, ast.Subscript) else node.target
            self._mutation(changed, "[]" if isinstance(node.target, ast.Subscript) else "+=", None, scope, line)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    self._mutation(target.value, "[]", None, scope, line)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            # ``for tool in (a, b, c):`` the name stands for each item in turn, which a reader can follow.
            scope.bind(Binding(node.target.id, "loop", line, scope, node=node.iter))
        elif isinstance(node, ast.For | ast.AsyncFor | ast.AugAssign):
            self._opaque(node.target, scope, line)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if item.optional_vars is not None:
                    self._opaque(item.optional_vars, scope, line)
        maybe = isinstance(node, _MAYBE)
        self._maybe += maybe
        self._children(node, scope)
        self._maybe -= maybe

    def _children(self, node: ast.AST, scope: Scope) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self.statement(child, scope)
            elif isinstance(child, ast.ExceptHandler):
                if child.name:
                    scope.bind(Binding(child.name, "opaque", child.lineno, scope))
                self._children(child, scope)
            elif isinstance(child, ast.match_case):
                for pattern_node in ast.walk(child.pattern):
                    captured: str | None = getattr(pattern_node, "name", None) or getattr(pattern_node, "rest", None)
                    if isinstance(captured, str):
                        scope.bind(Binding(captured, "opaque", getattr(pattern_node, "lineno", 1), scope))
                self._children(child, scope)
            else:
                self._expression(child, scope)

    def _function(self, node: FunctionNode, scope: Scope) -> None:
        scope.bind(Binding(node.name, "def", node.lineno, scope, node=node))
        prefix = f"{scope.qualname}.<locals>." if scope.kind == "function" else f"{scope.qualname}."
        body = Scope(
            kind="function",
            node=node,
            parent=scope,
            module=self.module,
            qualname=f"{prefix if scope.qualname else ''}{node.name}",
        )
        self.module.function_scopes[id(node)] = body
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            parameters.append(node.args.vararg)
        if node.args.kwarg is not None:
            parameters.append(node.args.kwarg)
        for parameter in parameters:
            body.bind(Binding(parameter.arg, "opaque", node.lineno, body))
        positional = [*node.args.posonlyargs, *node.args.args]
        if scope.kind == "class" and positional and not _is_static_method(node):
            body.receiver = positional[0].arg
        # Decorators, defaults and annotations belong to the scope the function is defined in.
        for expression in (*node.decorator_list, node.args, node.returns):
            if expression is not None:
                self._expression(expression, scope)
        if node.decorator_list:
            self.module.decorated.append(Decorated(node, scope, body))
        outer, self._maybe = self._maybe, 0  # what a function does, it does whenever it is called
        self.block(node.body, body)
        self._maybe = outer

    def _class(self, node: ast.ClassDef, scope: Scope) -> None:
        scope.bind(Binding(node.name, "class", node.lineno, scope, node=node))
        prefix = f"{scope.qualname}.<locals>." if scope.kind == "function" else f"{scope.qualname}."
        body = Scope(
            kind="class",
            node=node,
            parent=scope,
            module=self.module,
            qualname=f"{prefix if scope.qualname else ''}{node.name}",
        )
        self.module.class_scopes[id(node)] = body
        for expression in (*node.decorator_list, *node.bases, *node.keywords):
            self._expression(expression, scope)
        self.block(node.body, body)

    def _assign(self, target: ast.expr, value: ast.expr, scope: Scope, line: int) -> None:
        if isinstance(target, ast.Name):
            scope.bind(Binding(target.id, "assign", line, scope, node=value))
        elif isinstance(target, ast.Attribute):
            owner = scope.enclosing_class()
            if owner is not None and isinstance(target.value, ast.Name) and target.value.id == scope.receiver:
                owner.attributes.setdefault(target.attr, []).append(
                    Binding(target.attr, "assign", line, scope, node=value)
                )
        elif isinstance(target, ast.Tuple | ast.List):
            if isinstance(value, ast.Tuple | ast.List) and len(value.elts) == len(target.elts):
                starred = any(isinstance(item, ast.Starred) for item in (*target.elts, *value.elts))
                if not starred:
                    for item, item_value in zip(target.elts, value.elts, strict=True):
                        self._assign(item, item_value, scope, line)
                    return
            self._opaque(target, scope, line)

    def _assigned(self, target: ast.expr, value: ast.expr, scope: Scope) -> None:
        """Record the calls in an assigned value, each with the name it is assigned to (``a, b = f(), g()``)."""
        paired = (
            isinstance(target, ast.Tuple | ast.List)
            and isinstance(value, ast.Tuple | ast.List)
            and len(target.elts) == len(value.elts)
            and not any(isinstance(item, ast.Starred) for item in (*target.elts, *value.elts))
        )
        if paired and isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
            for item, item_value in zip(target.elts, value.elts, strict=True):
                self._assigned(item, item_value, scope)
        else:
            self._expression(value, scope, bound_to=_target_name(target, scope))

    def _opaque(self, target: ast.expr, scope: Scope, line: int) -> None:
        """Bind every name in an assignment target to "cannot be known without running the code"."""
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                scope.bind(Binding(node.id, "opaque", line, scope))

    def _expression(self, root: ast.AST, scope: Scope, *, bound_to: str | None = None, statement: bool = False) -> None:
        """Record the calls and walrus bindings anywhere inside one expression."""
        top = unwrap(root) if isinstance(root, ast.expr) else root
        # The same breadth-first order as ``ast.walk``, without visiting the leaves that make up most of a
        # tree: this loop sees every expression of every file.
        pending: deque[ast.AST] = deque([root])
        while pending:
            node = pending.popleft()
            if isinstance(node, _LEAVES):
                continue
            for name in node._fields:
                value: object = getattr(node, name, None)
                if isinstance(value, ast.AST):
                    pending.append(value)
                elif isinstance(value, list):
                    pending.extend(item for item in cast("list[object]", value) if isinstance(item, ast.AST))
            if isinstance(node, ast.Call):
                is_top = node is top
                self.module.calls.append(
                    CallSite(node, scope, bound_to if is_top else None, statement=statement and is_top)
                )
            elif isinstance(node, ast.NamedExpr):
                scope.bind(Binding(node.target.id, "opaque", node.lineno, scope))


def _is_static_method(node: FunctionNode) -> bool:
    return any(isinstance(decorator, ast.Name) and decorator.id == "staticmethod" for decorator in node.decorator_list)


def _target_name(target: ast.expr, scope: Scope) -> str | None:
    """The plain name an assignment target gives its value: ``x`` for ``x = ...`` and ``self.x = ...``."""
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == scope.receiver:
        return target.attr
    return None


def _function_name(scope: Scope) -> str | None:
    return scope.node.name if isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef) else None


def iter_scopes(module: Module) -> Iterator[Scope]:
    """Every scope of ``module``: the module itself, then functions and classes in no particular order."""
    yield module.scope
    yield from module.function_scopes.values()
    yield from module.class_scopes.values()
