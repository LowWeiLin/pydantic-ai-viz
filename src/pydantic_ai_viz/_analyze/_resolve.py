"""Tracing a name back to what it stands for, across the analysed files.

``Resolver.resolve`` follows plain names, attribute access, imports between the
analysed files and calls of simple factory functions, and stops at one of:

* an ``Expression``: a concrete expression such as ``Agent(...)`` or ``"text"``,
  with the scope it is evaluated in;
* a ``Definition``: a ``def`` or a ``class``;
* a ``ModuleRef``: one of the analysed files, reached through an import;
* an ``External``: a dotted name that leads out of the analysed files, such as
  ``pydantic_ai.Agent`` or ``llm.get_model``;
* ``None``: the value depends on running the code.

Every walk is bounded, so cyclic aliases and self-returning factories end in ``None``.
"""

from __future__ import annotations

import ast
import bisect
import builtins
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from ._source import Binding, FunctionNode, Module, Scope, unwrap

MAX_HOPS = 32
"""How many names, attributes and factory calls one resolution may pass through."""

MAX_DEPTH = 12
"""How deeply resolutions may nest (the callee of a call that is itself a call, and so on)."""

MAX_STEPS = 512
"""How many steps one resolution may take in all, nested ones included."""

END_OF_MODULE = 10**9
"""A line number after every line: what a name means once its module has finished running."""

_BUILTINS = frozenset(dir(builtins))
_LIBRARIES = frozenset({"pydantic_ai", "pydantic_ai_slim", "pydantic_graph"})


@dataclass(frozen=True, slots=True)
class External:
    """A dotted name that leads outside the analysed files."""

    name: str

    @property
    def last(self) -> str:
        return self.name.rpartition(".")[2]

    def is_library(self, *names: str) -> bool:
        """Whether this is one of ``names`` from Pydantic AI or pydantic-graph, whichever submodule it came from."""
        return self.name.partition(".")[0] in _LIBRARIES and self.last in names

    def comes_from(self, package: str, *names: str) -> bool:
        return self.name.partition(".")[0] == package and self.last in names


@dataclass(frozen=True, slots=True, eq=False)
class Definition:
    """A ``def`` or ``class`` statement and the scope it stands in."""

    node: FunctionNode | ast.ClassDef
    scope: Scope

    @property
    def module(self) -> Module:
        return self.scope.module

    @property
    def body(self) -> Scope:
        """The scope of the definition's own body."""
        scopes = self.module.class_scopes if isinstance(self.node, ast.ClassDef) else self.module.function_scopes
        return scopes[id(self.node)]

    @property
    def qualified_name(self) -> str:
        return f"{self.module.name}.{self.body.qualname}"


@dataclass(frozen=True, slots=True, eq=False)
class Expression:
    """An expression, the scope it is evaluated in and the line it is evaluated at."""

    node: ast.expr
    scope: Scope
    line: int

    @property
    def module(self) -> Module:
        return self.scope.module


@dataclass(frozen=True, slots=True, eq=False)
class ModuleRef:
    """One of the analysed files, as the value of a name."""

    module: Module


Resolved: TypeAlias = External | Definition | Expression | ModuleRef | None


@dataclass(slots=True)
class Project:
    """The set of files analysed together.

    Imports are resolved among these files only. A file that is not part of the
    project is never opened because another file imports it; the name it would
    have provided stays an ``External``.
    """

    root: Path
    modules: list[Module] = field(default_factory=list[Module])
    _by_path: dict[Path, Module] = field(default_factory=dict[Path, Module])
    _by_name: dict[str, Module] = field(default_factory=dict[str, Module])

    def add(self, module: Module) -> None:
        self.modules.append(module)
        self._by_path[module.path] = module
        self._by_name.setdefault(module.name, module)

    def find_module(self, importer: Module, dotted: str, level: int) -> Module | None:
        """The analysed file that ``from <dots><dotted> import ...`` in ``importer`` refers to, if there is one.

        A relative import is looked up from the importing file's package. An absolute
        one is tried as a module name, then as a path below the importing file's
        directory (how a script finds its neighbours), then below the root.
        """
        parts = [part for part in dotted.split(".") if part]
        if level:
            base = importer.path.parent
            for _ in range(level - 1):
                base = base.parent
            return self._at(base, parts)
        if dotted in self._by_name:
            return self._by_name[dotted]
        return self._at(importer.path.parent, parts) or self._at(self.root, parts)

    def _at(self, base: Path, parts: list[str]) -> Module | None:
        if not parts:
            return self._by_path.get(base / "__init__.py")
        target = base.joinpath(*parts)
        return self._by_path.get(target.with_name(target.name + ".py")) or self._by_path.get(target / "__init__.py")


class _Stop:
    """Marks an expression that cannot be followed further and is its own result."""


_STOP = _Stop()


class Resolver:
    """Follows names through the scopes of a ``Project``."""

    def __init__(self, project: Project, *, deadline: float | None = None) -> None:
        self.project = project
        self.deadline = deadline
        """``time.monotonic()`` value after which nothing is resolved any more."""
        self.timed_out = False
        """Whether a resolution was refused because the deadline had passed."""
        self._steps_left = MAX_STEPS

    # ------------------------------------------------------------- lookup

    def lookup(self, name: str, scope: Scope, line: int) -> Binding | None:
        """The binding a use of ``name`` at ``line`` in ``scope`` refers to.

        Scopes are searched from the inside out, skipping class bodies that merely
        enclose the use, as Python does. When a scope binds the name more than once,
        the binding closest above the use wins; a use above every binding (a function
        that runs later) gets the first one.
        """
        current: Scope | None = scope
        innermost = True
        while current is not None:
            if current.kind != "class" or innermost:
                found = current.bindings.get(name)
                if found:
                    return _closest(found, line)
            innermost = False
            current = current.parent
        return None

    # ------------------------------------------------------------ resolve

    def resolve(self, node: ast.expr, scope: Scope, line: int | None = None, *, depth: int = 0) -> Resolved:
        """What ``node``, evaluated in ``scope``, stands for. See the module docstring."""
        if depth > MAX_DEPTH:
            return None
        if depth == 0:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                # Out of time: from here on every name is unknown, which ends the analysis quickly.
                self.timed_out = True
                return None
            self._steps_left = MAX_STEPS
        current = Expression(node, scope, node.lineno if line is None else line)
        for _ in range(MAX_HOPS):
            self._steps_left -= 1
            if self._steps_left < 0:
                return None  # hops multiply when resolutions nest; the total is capped as well
            step = self._step(current, depth)
            if isinstance(step, _Stop):
                return current
            if not isinstance(step, Expression):
                return step
            current = step
        return None

    def _step(self, current: Expression, depth: int) -> Resolved | _Stop:
        node = unwrap(current.node)
        if isinstance(node, ast.Name):
            return self._name(node.id, current.scope, current.line)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, current, depth)
        if isinstance(node, ast.Call):
            return self._factory(node, current, depth)
        if node is not current.node:
            return Expression(node, current.scope, current.line)
        return _STOP

    def _name(self, name: str, scope: Scope, line: int) -> Resolved:
        binding = self.lookup(name, scope, line)
        if binding is not None:
            return self._bound(binding)
        if name in _BUILTINS:
            return External(f"builtins.{name}")
        for star in scope.module.star_imports:
            if star.partition(".")[0] in _LIBRARIES:
                return External(f"{star}.{name}")
        return None

    def _bound(self, binding: Binding) -> Resolved:
        if binding.kind == "assign" and isinstance(binding.node, ast.expr):
            # The value is evaluated before the name is bound, so in ``x = wrap(x)`` the inner
            # ``x`` is the one from an earlier line.
            return Expression(binding.node, binding.scope, binding.line - 1)
        if binding.kind in ("def", "class") and isinstance(
            binding.node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            return Definition(binding.node, binding.scope)
        if binding.kind == "import":
            return self._imported(binding)
        return None

    def _imported(self, binding: Binding) -> Resolved:
        importer = binding.scope.module
        dotted = binding.module or ""
        module = self.project.find_module(importer, dotted, binding.level)
        if binding.attr is None:
            return ModuleRef(module) if module is not None else External(dotted)
        if module is not None:
            member = self._member(module, binding.attr)
            if member is not None:
                return member
        submodule = self.project.find_module(importer, f"{dotted}.{binding.attr}".strip("."), binding.level)
        if submodule is not None:
            return ModuleRef(submodule)
        base = _absolute(importer, dotted, binding.level)
        # ``from . import x`` outside any known package keeps its dot: ``.x``.
        return External(f"{base}{binding.attr}" if not base or base.endswith(".") else f"{base}.{binding.attr}")

    def _member(self, module: Module, name: str) -> Resolved:
        """What ``name`` is at the top level of ``module`` once the module has run."""
        found = module.scope.bindings.get(name)
        return self._bound(_closest(found, END_OF_MODULE)) if found else None

    def _attribute(self, node: ast.Attribute, current: Expression, depth: int) -> Resolved | _Stop:
        # ``a.b.c`` nests as deep as it is long, so the chain is unrolled instead of recursed into.
        names: list[str] = []
        base: ast.expr = node
        while isinstance(base, ast.Attribute):
            names.append(base.attr)
            base = base.value
        names.reverse()

        scope = current.scope
        owner = _receiver_class(base.id, scope) if isinstance(base, ast.Name) else None
        if owner is not None:
            found = owner.attributes.get(names[0]) or owner.bindings.get(names[0])
            if not found:
                return None
            value: Resolved = self._bound(found[-1])
            names = names[1:]
        else:
            value = self.resolve(base, scope, current.line, depth=depth + 1)

        for name in names:
            if isinstance(value, Expression):
                # A binding's value: see what it is before taking an attribute of it.
                value = self.resolve(value.node, value.scope, value.line, depth=depth + 1)
                if isinstance(value, Expression):
                    return _STOP  # an attribute of something that only exists at run time
            if isinstance(value, External):
                dotted = f"{value.name}.{name}"
                module = self.project.find_module(scope.module, dotted, 0)
                value = ModuleRef(module) if module is not None else External(dotted)
            elif isinstance(value, ModuleRef):
                package = value.module
                value = self._member(package, name)
                if value is None and package.path.stem == "__init__":
                    submodule = self.project.find_module(package, name, 1)
                    value = None if submodule is None else ModuleRef(submodule)
            elif isinstance(value, Definition) and isinstance(value.node, ast.ClassDef):
                found = value.body.bindings.get(name)
                value = self._bound(found[-1]) if found else None
            else:
                return None
        return value

    def _factory(self, node: ast.Call, current: Expression, depth: int) -> Resolved | _Stop:
        """The value a call returns, when it calls a function here that has exactly one ``return``."""
        callee = self.callee(node, current.scope, current.line, depth=depth + 1)
        if isinstance(callee, Definition) and not isinstance(callee.node, ast.ClassDef):
            returns = callee.body.returns
            if len(returns) == 1:
                return Expression(returns[0], callee.body, END_OF_MODULE)
        return _STOP

    def callee(self, node: ast.Call, scope: Scope, line: int | None = None, *, depth: int = 0) -> Resolved:
        """What a call expression calls. ``Agent[Deps, Out](...)`` calls ``Agent``."""
        function = node.func
        if isinstance(function, ast.Subscript):
            function = function.value
        return self.resolve(function, scope, node.lineno if line is None else line, depth=depth)


def _receiver_class(name: str, scope: Scope) -> Scope | None:
    """The class whose instance ``name`` is: ``self`` in a method, also from a function nested in it."""
    current: Scope | None = scope
    while current is not None:
        if current.kind == "function" and name in current.bindings:
            return current.enclosing_class() if current.receiver == name else None
        current = current.parent
    return None


def _closest(bindings: list[Binding], line: int) -> Binding:
    """The last binding at or above ``line``, else the first one. Bindings are recorded in source order."""
    index = bisect.bisect_right(bindings, line, key=lambda binding: binding.line)
    return bindings[index - 1] if index else bindings[0]


def _absolute(importer: Module, dotted: str, level: int) -> str:
    """The absolute name of a relative import, when the importing file's package is known."""
    if not level:
        return dotted
    package = importer.name.split(".")
    if importer.path.stem != "__init__":
        package = package[:-1]
    keep = len(package) - (level - 1)
    if keep <= 0:
        return "." * level + dotted  # outside every known package: keep it as written
    return ".".join([*package[:keep], *([dotted] if dotted else [])])
