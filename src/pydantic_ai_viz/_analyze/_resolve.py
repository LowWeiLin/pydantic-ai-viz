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
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from ._locate import skipped_directory
from ._source import Binding, FunctionNode, Module, Mutation, Scope, unwrap

MAX_HOPS = 32
"""How many names, attributes and factory calls one resolution may pass through."""

MAX_DEPTH = 12
"""How deeply resolutions may nest (the callee of a call that is itself a call, and so on)."""

MAX_STEPS = 512
"""How many steps one resolution may take in all, nested ones included."""

_MAX_LOOP_ITEMS = 64
"""How many items of a ``for`` loop over a written-out list are gone through one by one."""

_MAX_STAR_DEPTH = 4
"""How many ``from ... import *`` statements a name is followed through."""

_MAX_BASES = 8
"""How many classes, a class and the ones it derives from, an attribute of ``self`` is looked for in."""

END_OF_MODULE = 10**9
"""A line number after every line: what a name means once its module has finished running."""

_BUILTINS = frozenset(dir(builtins))
_CASTS = frozenset({"typing.cast", "typing_extensions.cast"})
_LIBRARIES = frozenset({"pydantic_ai", "pydantic_ai_slim", "pydantic_graph", "pydantic_evals", "pydantic_ai_harness"})
"""The packages of the Pydantic AI project, whose public API the analysis knows."""
_NEVER_PROJECT = _LIBRARIES | {"pydantic", "pydantic_core", "pydantic_settings", "typing_extensions"}
"""Packages whose names are recognised by the analysis. A project file never stands in for one of them."""


@dataclass(frozen=True, slots=True)
class External:
    """A dotted name that leads outside the analysed files."""

    name: str

    @property
    def last(self) -> str:
        return self.name.rpartition(".")[2]

    def is_library(self, *names: str) -> bool:
        """Whether this is one of ``names`` from a package of Pydantic AI, whichever submodule it came from."""
        return self.name.partition(".")[0] in _LIBRARIES and self.last in names

    def is_library_class(self) -> bool:
        """Whether this is a class of a package of Pydantic AI, which names its classes, and only them, capitalised."""
        return self.name.partition(".")[0] in _LIBRARIES and self.last[:1].isupper()

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
    """The set of files analysed together, and how an import in one of them finds another.

    An import is only ever resolved to a file of the project: one that was named
    as a target or, while ``opener`` is set, one inside ``boundaries`` that a file
    already read imports. Installed packages are never opened; a name they would
    have provided stays an ``External``.
    """

    root: Path
    bases: list[Path] = field(default_factory=list[Path])
    """Directories an absolute import is looked up in, after the directories above the importing file."""
    boundaries: list[Path] = field(default_factory=list[Path])
    """Directories inside which a file that is not yet part of the project may be read."""
    opener: Callable[[Path], Module | None] | None = None
    """Reads a file that an import leads to. ``None`` once the set of files is final."""
    modules: list[Module] = field(default_factory=list[Module])
    # Paths are kept as strings below: these tables are consulted for every name that is resolved.
    _by_file: dict[str, Module] = field(default_factory=dict[str, Module])
    _by_tail: dict[str, list[Module]] = field(default_factory=dict[str, list[Module]])
    _absent: set[str] = field(default_factory=set[str])
    _listings: dict[str, dict[str, bool]] = field(default_factory=dict[str, dict[str, bool]])
    _search: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    _found: dict[tuple[str, str, int], Module | None] = field(default_factory=dict[tuple[str, str, int], Module | None])

    def add(self, module: Module) -> None:
        self.modules.append(module)
        self._by_file[str(module.path)] = module
        self._by_tail.setdefault(_parts_of(module.path)[-1], []).append(module)

    def find_module(self, importer: Module, dotted: str, level: int) -> Module | None:
        """The project file that ``from <dots><dotted> import ...`` in ``importer`` refers to, if there is one.

        A relative import is looked up from the importing file's package. An absolute
        one is looked up as Python would with a source root anywhere above the
        importing file: in its own directory (how a script finds its neighbours),
        then in each directory above it up to where its project starts, then in
        ``bases``. Failing that, ``a.b.c`` is the one file of the project whose path
        ends in ``a/b/c.py``, if exactly one does.
        """
        key = (importer.directory, dotted, level)
        if key in self._found:
            return self._found[key]
        found = self._find(importer, dotted, level)
        if found is not None or self.opener is None:
            self._found[key] = found  # a miss is final only once no more files can be read
        return found

    def _find(self, importer: Module, dotted: str, level: int) -> Module | None:
        parts = [part for part in dotted.split(".") if part]
        own = importer.directory
        if level:
            base = own
            for _ in range(level - 1):
                base = os.path.dirname(base)
            return self._at(base, parts)
        if not parts or parts[0] in _NEVER_PROJECT:
            return None
        if parts[0] in sys.stdlib_module_names:
            # Only a script's own directory comes before the standard library.
            return self._at(own, parts) if "." not in importer.name and importer.path.stem != "__init__" else None
        for base in self._search_path(importer):
            found = self._at(base, parts)
            if found is not None:
                return found
        return self._by_suffix(parts) if len(parts) > 1 else None

    def _search_path(self, importer: Module) -> list[str]:
        own = importer.directory
        cached = self._search.get(own)
        if cached is None:
            cached = [own]
            directory = importer.path.parent
            limit = next((b for b in self.boundaries if b == directory or b in directory.parents), None)
            current = directory
            while limit is not None and current != limit:
                current = current.parent
                cached.append(str(current))
            # Where the importing file's outermost package lives, from which its own name is importable.
            levels = importer.name.count(".") + (1 if importer.path.stem == "__init__" else 0)
            package_root = directory
            for _ in range(levels):
                package_root = package_root.parent
            for base in (package_root, *self.bases):
                if str(base) not in cached:
                    cached.append(str(base))
            self._search[own] = cached
        return cached

    def _at(self, base: str, parts: list[str]) -> Module | None:
        if not parts:
            return self._file(os.path.join(base, "__init__.py"))
        target = os.path.join(base, *parts)
        return self._file(target + ".py") or self._file(os.path.join(target, "__init__.py"))

    def _file(self, path: str) -> Module | None:
        known = self._by_file.get(path)
        if known is not None or self.opener is None or path in self._absent:
            return known
        module = self.opener(Path(path)) if self._may_read(path) else None
        if module is None:
            self._absent.add(path)
            return None
        self.add(module)
        return module

    def _may_read(self, path: str) -> bool:
        """Whether ``path`` is a file inside a boundary, and not in a directory of installed or generated code."""
        for boundary in self.boundaries:
            prefix = os.path.join(str(boundary), "")
            if not path.startswith(prefix):
                continue
            below = path[len(prefix) :].split(os.sep)
            if any(skipped_directory(name) for name in below[:-1]):
                continue
            # Each part is checked against what its directory really holds, so that on a file system
            # that ignores case ``from tools import Search`` does not find ``search.py``.
            directory = str(boundary)
            for name in below[:-1]:
                if self._entries(directory).get(name) is not False:
                    return False
                directory = os.path.join(directory, name)
            return self._entries(directory).get(below[-1]) is True
        return False

    def _entries(self, directory: str) -> dict[str, bool]:
        """The names in a directory, each with whether it is a file. Empty when it cannot be listed."""
        known = self._listings.get(directory)
        if known is None:
            known = {}
            try:
                with os.scandir(directory) as scan:
                    for entry in scan:
                        known[entry.name] = entry.is_file()
            except OSError:
                known = {}
            self._listings[directory] = known
        return known

    def _by_suffix(self, parts: list[str]) -> Module | None:
        matches = [
            module for module in self._by_tail.get(parts[-1], ()) if _parts_of(module.path)[-len(parts) :] == parts
        ]
        return matches[0] if len(matches) == 1 else None


def _parts_of(path: Path) -> list[str]:
    """The path of a module as the parts of its dotted name would spell it: ``a/b/__init__.py`` -> ``[.., a, b]``."""
    parts = list(path.parts[:-1])
    if path.stem != "__init__":
        parts.append(path.stem)
    return parts


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
        self.trail: list[Expression] = []
        """The names the last top-level ``resolve`` passed through, in order."""
        self._standing_for: dict[int, Expression] = {}
        """``id(Binding)`` of a loop variable -> the item it stands for while ``each`` goes through the loop."""
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

    def origin(self, name: str, scope: Scope, line: int) -> Binding | None:
        """The binding that gives ``name`` its value, followed through imports between the analysed files."""
        binding = self.lookup(name, scope, line)
        for _ in range(MAX_HOPS):
            if binding is None or binding.kind != "import" or binding.attr is None:
                return binding
            module = self.project.find_module(binding.scope.module, binding.module or "", binding.level)
            found = None if module is None else module.scope.bindings.get(binding.attr)
            if not found:
                return None
            binding = _closest(found, END_OF_MODULE)
        return None

    def changes(self, node: ast.expr, scope: Scope, line: int, *, until: int | None = None) -> list[Mutation]:
        """In-place changes made to the value ``node`` names before it is used at ``line`` in ``scope``.

        ``node`` is a plain name, or an attribute of ``self``. A change counts when
        it is made before the use. Where the two stand in different scopes, the
        order they run in is not known, and every change counts.

        ``until`` is a later line of the same scope at which the value is really
        used: a list put into a dict at ``line`` and passed on at ``until`` is still
        the same list, with everything appended to it in between.
        """
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner = receiver_class(node.value.id, scope)
            return [] if owner is None else list(owner.attribute_mutations.get(node.attr, ()))
        if not isinstance(node, ast.Name):
            return []
        binding = self.origin(node.id, scope, line)
        if binding is None:
            return []
        return [
            change
            for change in binding.scope.mutations.get(binding.name, ())
            if change.scope is not scope
            or binding.scope is not scope
            or binding.line < change.line <= max(line, until or line)
        ]

    def each(self, node: ast.AST, scope: Scope, line: int) -> Iterator[None]:
        """Go through a statement once for every item its loop variable stands for.

        ``for tool in (search, fetch): agent.tool_plain(tool)`` registers two tools.
        Iterating over this method makes ``tool`` resolve to each item in turn.
        A statement outside a loop, or in a loop over something that is not a
        list written out in the source, is gone through once, the variable unknown.
        """
        loop: Binding | None = None
        for index, part in enumerate(ast.walk(node)):
            if index > MAX_STEPS:
                break
            if isinstance(part, ast.Name):
                binding = self.lookup(part.id, scope, line)
                if binding is not None and binding.kind == "loop" and id(binding) not in self._standing_for:
                    loop = binding
                    break
        items: Expression | None = None
        if loop is not None and isinstance(loop.node, ast.expr):
            looped = self.resolve(loop.node, loop.scope, loop.line - 1, depth=1)
            if (
                isinstance(looped, Expression)
                and isinstance(looped.node, ast.List | ast.Tuple)
                and len(looped.node.elts) <= _MAX_LOOP_ITEMS
                and not any(isinstance(item, ast.Starred) for item in looped.node.elts)
                and not self.changes(unwrap(loop.node), loop.scope, loop.line)
            ):
                items = looped
        if loop is None or items is None or not isinstance(items.node, ast.List | ast.Tuple):
            yield
            return
        try:
            for item in items.node.elts:
                self._standing_for[id(loop)] = Expression(item, items.scope, items.line)
                yield
        finally:
            self._standing_for.pop(id(loop), None)

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
        trail: list[Expression] = []
        try:
            for _ in range(MAX_HOPS):
                self._steps_left -= 1
                if self._steps_left < 0:
                    return None  # hops multiply when resolutions nest; the total is capped as well
                if isinstance(unwrap(current.node), ast.Name | ast.Attribute):
                    trail.append(current)
                step = self._step(current, depth)
                if isinstance(step, _Stop):
                    return current
                if not isinstance(step, Expression):
                    return step
                current = step
            return None
        finally:
            if depth == 0:
                self.trail = trail

    def changed(self, *, until: tuple[Scope, int] | None = None) -> list[Mutation]:
        """In-place changes to any name the last ``resolve`` passed through on its way to a value.

        ``tools = [a]`` followed by ``tools.append(b)`` resolves to the list ``[a]``,
        which is not what ``tools`` holds when it is used. A caller that reads the
        value has to ask here first. ``until`` is the scope and the line at which
        the value is used, when that is further down than where it was resolved.
        """
        found: list[Mutation] = []
        for hop in self.trail:
            later = until[1] if until is not None and until[0] is hop.scope else None
            found.extend(self.changes(unwrap(hop.node), hop.scope, hop.line, until=later))
        return found

    def reassigned(self) -> bool:
        """Whether a name the last ``resolve`` passed through is given a value in more than one place.

        ``tools = []`` and later, under some condition, ``tools = load()``: the value
        found is the one assigned closest above the use, which the running code may
        not have assigned at all.
        """
        for hop in self.trail:
            node = unwrap(hop.node)
            if not isinstance(node, ast.Name):
                continue
            binding = self.lookup(node.id, hop.scope, hop.line)
            if binding is not None and len(binding.scope.bindings.get(node.id, ())) > 1:
                return True
        return False

    def _step(self, current: Expression, depth: int) -> Resolved | _Stop:
        node = unwrap(current.node)
        if isinstance(node, ast.Name):
            return self._name(node.id, current.scope, current.line)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, current, depth)
        if isinstance(node, ast.Call):
            return self._factory(node, current, depth)
        if isinstance(node, ast.Subscript):
            return self._item(node, current, depth)
        if node is not current.node:
            return Expression(node, current.scope, current.line)
        return _STOP

    def _name(self, name: str, scope: Scope, line: int) -> Resolved:
        binding = self.lookup(name, scope, line)
        if binding is not None:
            return self._bound(binding)
        if name in _BUILTINS:
            return External(f"builtins.{name}")
        return self._starred(scope.module, name, 0)

    def _starred(self, module: Module, name: str, depth: int, trail: frozenset[int] = frozenset()) -> Resolved:
        """What ``name`` is when one of the module's ``from ... import *`` statements provides it."""
        library: Resolved = None
        for dotted, level in module.star_imports:
            if not level and dotted.partition(".")[0] in _LIBRARIES:
                # Whatever the project's own files do not provide is taken to be the library's.
                library = library or External(f"{dotted}.{name}")
                continue
            source = self.project.find_module(module, dotted, level)
            if source is not None and source is not module and depth < _MAX_STAR_DEPTH:
                found = self._member(source, name, depth + 1, trail)
                if found is not None:
                    return found
        return library

    def _bound(self, binding: Binding, trail: frozenset[int] = frozenset()) -> Resolved:
        if binding.kind == "assign" and isinstance(binding.node, ast.expr):
            # The value is evaluated before the name is bound, so in ``x = wrap(x)`` the inner
            # ``x`` is the one from an earlier line.
            return Expression(binding.node, binding.scope, binding.line - 1)
        if binding.kind in ("def", "class") and isinstance(
            binding.node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            return Definition(binding.node, binding.scope)
        if binding.kind == "loop":
            return self._standing_for.get(id(binding))  # unknown, unless ``each`` is going through the items
        if binding.kind == "import":
            # Two files that import a name from each other never lead to a value: stop where the trail closes.
            if id(binding) in trail or len(trail) >= MAX_HOPS:
                return None
            return self._imported(binding, trail | {id(binding)})
        return None

    def _imported(self, binding: Binding, trail: frozenset[int]) -> Resolved:
        importer = binding.scope.module
        dotted = binding.module or ""
        module = self.project.find_module(importer, dotted, binding.level)
        if binding.attr is None:
            return ModuleRef(module) if module is not None else External(dotted)
        if module is not None:
            member = self._member(module, binding.attr, trail=trail)
            if member is not None:
                return member
        submodule = self.project.find_module(importer, f"{dotted}.{binding.attr}".strip("."), binding.level)
        if submodule is not None:
            return ModuleRef(submodule)
        base = _absolute(importer, dotted, binding.level)
        # ``from . import x`` outside any known package keeps its dot: ``.x``.
        return External(f"{base}{binding.attr}" if not base or base.endswith(".") else f"{base}.{binding.attr}")

    def _member(self, module: Module, name: str, depth: int = 0, trail: frozenset[int] = frozenset()) -> Resolved:
        """What ``name`` is at the top level of ``module`` once the module has run."""
        found = module.scope.bindings.get(name)
        if found:
            return self._bound(_closest(found, END_OF_MODULE), trail)
        return self._starred(module, name, depth, trail) if module.star_imports else None

    def _attribute(self, node: ast.Attribute, current: Expression, depth: int) -> Resolved | _Stop:
        # ``a.b.c`` nests as deep as it is long, so the chain is unrolled instead of recursed into.
        names: list[str] = []
        base: ast.expr = node
        while isinstance(base, ast.Attribute):
            names.append(base.attr)
            base = base.value
        names.reverse()

        scope = current.scope
        owner = receiver_class(base.id, scope) if isinstance(base, ast.Name) else None
        if owner is not None:
            found = self._attribute_of(owner, names[0], depth)
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
                    # ``Settings().model``, ``Factory().build``: an instance of a class of the analysed files.
                    made = self._instance_class(value, depth)
                    member = None if made is None else self._instance_member(made, name, depth)
                    if member is None:
                        return _STOP  # an attribute of something that only exists at run time
                    value = member
                    continue
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

    def _attribute_of(self, owner: Scope, name: str, depth: int) -> list[Binding] | None:
        """Where ``self.name`` is given a value: in the class itself, else in a base class among the analysed files."""
        classes = [owner]
        for scope in classes:
            found = scope.attributes.get(name) or scope.bindings.get(name)
            if found:
                return found
            node = scope.node
            if not isinstance(node, ast.ClassDef) or scope.parent is None or len(classes) >= _MAX_BASES:
                continue
            for base in node.bases:
                written = base.value if isinstance(base, ast.Subscript) else base
                if not isinstance(written, ast.Name | ast.Attribute):
                    continue
                parent = self.resolve(written, scope.parent, node.lineno, depth=depth + 1)
                if isinstance(parent, Definition) and isinstance(parent.node, ast.ClassDef):
                    inherited = parent.body
                    if all(inherited is not seen for seen in classes) and len(classes) < _MAX_BASES:
                        classes.append(inherited)
        return None

    def method(self, cls: Definition, name: str) -> Definition | None:
        """The ``def`` of a method of a class, looked for in its base classes among the analysed files too."""
        if not isinstance(cls.node, ast.ClassDef):
            return None
        found = self._attribute_of(cls.body, name, 0)
        value = self._bound(found[-1]) if found else None
        if isinstance(value, Definition) and not isinstance(value.node, ast.ClassDef):
            return value
        return None

    def _instance_class(self, value: Expression, depth: int) -> Definition | None:
        """The class of the analysed files that ``value`` constructs: ``Settings()`` -> ``class Settings``."""
        if not isinstance(value.node, ast.Call):
            return None
        made = self.callee(value.node, value.scope, value.line, depth=depth + 1)
        if isinstance(made, Definition) and isinstance(made.node, ast.ClassDef):
            return made
        return None

    def _instance_member(self, cls: Definition, name: str, depth: int) -> Resolved:
        """What ``instance.name`` is, for an instance of ``cls``; ``None`` when only the running code knows.

        A method is the method. ``self.name = value``, written once in the class's
        methods, is that value. A value given in the class body is what the instance
        has only for a class that nothing outside the analysed files shapes: a
        pydantic model, a dataclass or any class with a base or decorator from a
        library sets its fields from the constructor's arguments, or the environment.
        """
        found = self._attribute_of(cls.body, name, depth)
        if not found:
            return None
        binding = found[-1]
        if binding.kind == "def":
            return self._bound(binding)
        if binding.scope.kind == "class":
            return self._bound(binding) if self._plain(cls, depth) else None
        return self._bound(binding) if len(found) == 1 else None  # set in two places: which one holds is not known

    def _plain(self, cls: Definition, depth: int) -> bool:
        """Whether a class and every class it derives from are classes of the analysed files with no decorator."""
        classes = [cls]
        for current in classes:
            node = current.node
            if not isinstance(node, ast.ClassDef) or node.decorator_list or node.keywords:
                return False
            for base in node.bases:
                parent = self.resolve(base, current.scope, node.lineno, depth=depth + 1)
                if isinstance(parent, External) and parent.name == "builtins.object":
                    continue
                if not isinstance(parent, Definition) or not isinstance(parent.node, ast.ClassDef):
                    return False
                if all(parent.node is not seen.node for seen in classes):
                    if len(classes) >= _MAX_BASES:
                        return False
                    classes.append(parent)
        return True

    def _item(self, node: ast.Subscript, current: Expression, depth: int) -> Resolved | _Stop:
        """``MODELS["fast"]``, ``PAIR[0]``: an item of a dict, list or tuple written out in the source.

        The key has to be a literal, and the container must not be changed in place
        after it is written: otherwise which item it is is only known at run time.
        """
        if isinstance(node.slice, ast.Slice):
            return _STOP
        key = self._key(node.slice, current, depth)
        if key is None:
            return _STOP
        container = unwrap(node.value)
        held = self.resolve(container, current.scope, current.line, depth=depth + 1)
        if not isinstance(held, Expression):
            return _STOP
        if isinstance(container, ast.Name | ast.Attribute) and self.changes(container, current.scope, current.line):
            return _STOP
        written = held.node
        if isinstance(written, ast.Dict) and isinstance(key, str):
            keys = [self._key(item, held, depth) if item is not None else None for item in written.keys]
            if None in keys or key not in keys:
                return _STOP  # a ``**spread`` or a computed key may be the one asked for
            last = len(keys) - 1 - keys[::-1].index(key)  # a key written twice holds the later value
            return Expression(written.values[last], held.scope, held.line)
        if isinstance(written, ast.List | ast.Tuple) and isinstance(key, int):
            if any(isinstance(item, ast.Starred) for item in written.elts) or not -len(written.elts) <= key < len(
                written.elts
            ):
                return _STOP
            return Expression(written.elts[key], held.scope, held.line)
        return _STOP

    def _key(self, node: ast.expr, context: Expression, depth: int) -> str | int | None:
        """A subscript key or dict key the source spells out: a string or an integer constant, or a name for one."""
        node = unwrap(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._key(node.operand, context, depth)
            return -inner if isinstance(inner, int) else None
        if isinstance(node, ast.Name | ast.Attribute | ast.Subscript):
            target = self.resolve(node, context.scope, context.line, depth=depth + 1)
            node = target.node if isinstance(target, Expression) else node
        if isinstance(node, ast.Constant) and isinstance(node.value, str | int) and not isinstance(node.value, bool):
            return node.value
        return None

    def _factory(self, node: ast.Call, current: Expression, depth: int) -> Resolved | _Stop:
        """The value a call returns: of a function here that has exactly one ``return``, or of ``typing.cast``."""
        callee = self.callee(node, current.scope, current.line, depth=depth + 1)
        if isinstance(callee, Definition) and not isinstance(callee.node, ast.ClassDef):
            returns = callee.body.returns
            if len(returns) == 1:
                return Expression(returns[0], callee.body, END_OF_MODULE)
        if isinstance(callee, External) and callee.name in _CASTS and len(node.args) == 2 and not node.keywords:
            # ``cast(KnownModelName, value)`` is ``value``: the cast only tells the type checker something.
            value = node.args[1]
            if not isinstance(value, ast.Starred):
                return Expression(value, current.scope, current.line)
        return _STOP

    def cast_value(self, node: ast.Call, scope: Scope, line: int) -> ast.expr | None:
        """The value inside ``typing.cast(type, value)``; ``None`` for any other call."""
        if len(node.args) != 2 or node.keywords or isinstance(node.args[1], ast.Starred):
            return None
        if not isinstance(node.func, ast.Name | ast.Attribute):
            return None
        callee = self.resolve(node.func, scope, line, depth=1)
        return node.args[1] if isinstance(callee, External) and callee.name in _CASTS else None

    def callee(self, node: ast.Call, scope: Scope, line: int | None = None, *, depth: int = 0) -> Resolved:
        """What a call expression calls. ``Agent[Deps, Out](...)`` calls ``Agent``."""
        function = node.func
        if isinstance(function, ast.Subscript):
            function = function.value
        return self.resolve(function, scope, node.lineno if line is None else line, depth=depth)


def receiver_class(name: str, scope: Scope) -> Scope | None:
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
