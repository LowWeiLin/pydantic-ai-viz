"""Reading literal values out of expressions, without evaluating anything.

A value is read only when the source spells it out: constants, containers of
constants, string concatenation, f-strings and ``"...".format(...)`` whose every
placeholder is itself such a value, the ``strip`` methods of ``str``, ``a if condition else b`` whose
condition is such a value, and a name that is assigned
such a value once and whose value is not changed in place afterwards. Everything else
raises ``NotLiteral``; the caller then reports the source text instead of a value.

A call of a function is not evaluated, with the exceptions listed in ``PURE_TEXT_FUNCTIONS``:
two functions of the standard library that only rearrange the text they are given, applied
to a literal string as Python would apply them. Reading an environment variable is not
evaluated either; ``environment`` says which variable an expression reads, and the default
the source gives, so that the caller can report both for what they are.

There is no ``eval`` and no ``ast.literal_eval`` here: both walk recursively
without a bound. This reader counts what it visits and gives up on anything
deeper or larger than a prompt or a settings dict could plausibly be.
"""

from __future__ import annotations

import ast
import inspect
import string as _string
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ._resolve import Expression, External, Resolver
from ._source import Scope, clip_expression, unwrap

MAX_DEPTH = 24
MAX_NODES = 5_000
MAX_STRING_CHARS = 200_000
_MAX_INT = 2**63
_STR_METHODS = frozenset({"strip", "lstrip", "rstrip"})
"""Methods of ``str`` applied to a literal string, when called without arguments."""
PURE_TEXT_FUNCTIONS: dict[str, Callable[[str], str]] = {
    "textwrap.dedent": textwrap.dedent,
    "inspect.cleandoc": inspect.cleandoc,
}
"""The functions of the standard library that are evaluated, on one literal string and nothing else.

Each one only rearranges the text it is given: it reads nothing else and changes nothing, so
calling the real function on the literal gives exactly what the running code gets.
"""
ENVIRONMENT_GETTERS = frozenset({"os.getenv", "os.environ.get"})
"""Calls that read an environment variable: ``(name)`` or ``(name, default)``."""
ENVIRONMENT = "os.environ"
"""The mapping of environment variables, read as ``os.environ[name]``."""


class NotLiteral(Exception):
    """The expression is not a literal value that can be read from source."""


@dataclass(frozen=True, slots=True)
class EnvironmentRead:
    """An expression that reads an environment variable when the code runs."""

    variable: str
    default: str | None
    """The default the source gives, if it gives one: what is used when the variable is not set."""


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0

    def spend(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise NotLiteral


def literal(resolver: Resolver, node: ast.expr, scope: Scope, line: int | None = None) -> Any:
    """The JSON-ready value ``node`` spells out.

    Raises:
        NotLiteral: If the value cannot be read without running code.
    """
    return _read(resolver, Expression(node, scope, node.lineno if line is None else line), 0, _Budget())


def string(resolver: Resolver, node: ast.expr, scope: Scope, line: int | None = None) -> str | None:
    """The string ``node`` spells out, or ``None`` when it is not a literal string."""
    try:
        value = literal(resolver, node, scope, line)
    except NotLiteral:
        return None
    return value if isinstance(value, str) else None


def _read(resolver: Resolver, expression: Expression, depth: int, budget: _Budget) -> Any:
    budget.spend(depth)
    node = expression.node

    def child(item: ast.expr) -> Any:
        return _read(resolver, Expression(item, expression.scope, expression.line), depth + 1, budget)

    if isinstance(node, ast.Constant):
        return _constant(node.value)
    if isinstance(node, ast.JoinedStr):
        # A placeholder that holds a literal is folded in; any other makes the f-string a template.
        return _bounded("".join(_fstring_part(part, child) for part in node.values))
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        if any(isinstance(item, ast.Starred) for item in node.elts):
            raise NotLiteral
        return [child(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        result: dict[str, Any] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise NotLiteral  # ``**other``
            name = child(key)
            if not isinstance(name, str):
                raise NotLiteral
            result[name] = child(value)
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        operand = child(node.operand)
        if isinstance(operand, bool) or not isinstance(operand, int | float):
            raise NotLiteral
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concatenate(resolver, expression, depth, budget)
    if isinstance(node, ast.IfExp):
        return child(node.body if child(node.test) else node.orelse)  # a condition that is itself a value
    if isinstance(node, ast.Name | ast.Attribute) and resolver.changes(node, expression.scope, expression.line):
        raise NotLiteral  # appended to, updated or otherwise changed after it was assigned
    if isinstance(node, ast.Name):
        binding = resolver.lookup(node.id, expression.scope, expression.line)
        if binding is not None and len(binding.scope.bindings[node.id]) > 1:
            raise NotLiteral  # assigned more than once: which value applies depends on the run
        if binding is not None and binding.kind == "assign" and isinstance(binding.node, ast.expr):
            # One assignment at a time, so every name on the way is held to the same rule.
            return _read(resolver, Expression(binding.node, binding.scope, binding.line - 1), depth + 1, budget)
    if isinstance(node, ast.Name | ast.Attribute | ast.Subscript):
        return _followed(resolver, expression, depth, budget)
    if isinstance(node, ast.Call):
        inside = resolver.cast_value(node, expression.scope, expression.line)
        if inside is not None:
            return child(inside)
        function = _pure_text_function(resolver, node, expression.scope, expression.line)
        if function is not None:
            if len(node.args) != 1 or node.keywords or isinstance(node.args[0], ast.Starred):
                raise NotLiteral
            text = child(node.args[0])
            if not isinstance(text, str):
                raise NotLiteral
            return _bounded(function(text))
        if isinstance(node.func, ast.Attribute) and node.func.attr in _STR_METHODS:
            return _string_method(resolver, expression, node, depth, budget)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return _format(node, child)
        # What a function or method of the analysed files returns, when it has one ``return``.
        return _followed(resolver, expression, depth, budget)
    raise NotLiteral


def _pure_text_function(resolver: Resolver, node: ast.Call, scope: Scope, line: int) -> Callable[[str], str] | None:
    """The function of ``PURE_TEXT_FUNCTIONS`` that ``node`` calls, under whatever name it is imported."""
    if not isinstance(node.func, ast.Name | ast.Attribute):
        return None
    callee = resolver.resolve(node.func, scope, line, depth=1)
    return PURE_TEXT_FUNCTIONS.get(callee.name) if isinstance(callee, External) else None


def environment(resolver: Resolver, node: ast.expr, scope: Scope, line: int | None = None) -> EnvironmentRead | None:
    """The environment variable ``node`` reads, followed through names and functions; ``None`` if it reads none.

    ``os.getenv(name)``, ``os.getenv(name, default)``, ``os.environ.get(...)`` alike, and
    ``os.environ[name]``, under whatever names ``os`` and its members are imported. The name of
    the variable must be a literal string, and so must the default, if the source gives one:
    otherwise the expression is not read as an environment variable at all.
    """
    target = resolver.resolve(node, scope, node.lineno if line is None else line)
    if resolver.reassigned() or not isinstance(target, Expression):
        return None  # a name assigned in more than one place: which value it holds is decided at run time
    read = unwrap(target.node)
    where = (target.scope, target.line)
    if isinstance(read, ast.Subscript) and not isinstance(read.slice, ast.Slice):
        mapping = resolver.resolve(read.value, *where, depth=1)
        if isinstance(mapping, External) and mapping.name == ENVIRONMENT:
            variable = string(resolver, read.slice, *where)
            return None if variable is None else EnvironmentRead(variable, None)
        return None
    if not isinstance(read, ast.Call) or not isinstance(read.func, ast.Name | ast.Attribute):
        return None
    callee = resolver.resolve(read.func, *where, depth=1)
    if not isinstance(callee, External) or callee.name not in ENVIRONMENT_GETTERS:
        return None
    if any(isinstance(item, ast.Starred) for item in read.args) or len(read.args) > 2:
        return None
    given: dict[str, ast.expr] = dict(zip(("key", "default"), read.args, strict=False))
    for keyword in read.keywords:
        if keyword.arg not in ("key", "default") or keyword.arg in given:
            return None  # ``**options``, or an argument these functions do not take
        given[keyword.arg] = keyword.value
    if "key" not in given:
        return None
    variable = string(resolver, given["key"], *where)
    if variable is None:
        return None
    if "default" not in given:
        return EnvironmentRead(variable, None)
    try:
        default = literal(resolver, given["default"], *where)
    except NotLiteral:
        return None  # a default the code computes: the whole expression is shown as it is written
    return EnvironmentRead(variable, default) if default is None or isinstance(default, str) else None


def _followed(resolver: Resolver, expression: Expression, depth: int, budget: _Budget) -> Any:
    """The value of what an expression resolves to: a name's value, an item, a function's return value."""
    target = resolver.resolve(expression.node, expression.scope, expression.line)
    if isinstance(target, Expression) and target.node is not expression.node and not resolver.changed():
        return _read(resolver, target, depth + 1, budget)
    raise NotLiteral


def _constant(value: object) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _bounded(value)
    if isinstance(value, int):
        if abs(value) >= _MAX_INT:
            raise NotLiteral
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise NotLiteral  # not JSON numbers
        return value
    raise NotLiteral  # bytes, complex, Ellipsis


def _bounded(text: str) -> str:
    if len(text) > MAX_STRING_CHARS:
        raise NotLiteral
    return text


def _concatenate(resolver: Resolver, expression: Expression, depth: int, budget: _Budget) -> str:
    """``"a" + "b" + NAME``: a left-leaning chain, read without recursing down its spine."""
    operands: list[ast.expr] = []
    node: ast.expr = expression.node
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        budget.spend(depth)
        operands.append(node.right)
        node = node.left
    operands.append(node)
    parts: list[str] = []
    for operand in reversed(operands):
        value = _read(resolver, Expression(operand, expression.scope, expression.line), depth + 1, budget)
        if not isinstance(value, str):
            raise NotLiteral  # adding numbers or lists is arithmetic, which is not read
        parts.append(value)
    return _bounded("".join(parts))


def _fstring_part(part: ast.expr, read: Callable[[ast.expr], Any]) -> str:
    """One part of an f-string as text: literal text, or a placeholder whose value is a literal scalar."""
    if isinstance(part, ast.Constant):
        return str(part.value)
    if not isinstance(part, ast.FormattedValue) or part.format_spec is not None:
        raise NotLiteral  # a format spec is applied by the running code; it is not read here
    value = read(part.value)
    if not isinstance(value, str | int | float):
        raise NotLiteral  # a container would be written out as Python writes it, which is not read here
    if part.conversion == ord("r"):
        return repr(value)
    if part.conversion == ord("a"):
        return ascii(value)
    return str(value)


def _string_method(resolver: Resolver, expression: Expression, node: ast.Call, depth: int, budget: _Budget) -> str:
    """``"...".strip()``, ``lstrip()`` and ``rstrip()``: methods of the ``str`` a literal is."""
    function = node.func
    if node.keywords or node.args or not isinstance(function, ast.Attribute) or function.attr not in _STR_METHODS:
        raise NotLiteral
    value = _read(resolver, Expression(function.value, expression.scope, expression.line), depth + 1, budget)
    if isinstance(value, str):
        return getattr(value, function.attr)()
    raise NotLiteral


def _format(node: ast.Call, read: Callable[[ast.expr], Any]) -> str:
    """``"{} and {name!r}".format(a, name=b)``, the method of the ``str`` a literal is, with literal scalars."""
    function = node.func
    assert isinstance(function, ast.Attribute)
    template = read(function.value)
    if not isinstance(template, str) or any(isinstance(item, ast.Starred) for item in node.args):
        raise NotLiteral
    if any(keyword.arg is None for keyword in node.keywords):
        raise NotLiteral  # ``**values``
    positional = [read(item) for item in node.args]
    named = {keyword.arg: read(keyword.value) for keyword in node.keywords if keyword.arg is not None}
    parts: list[str] = []
    numbering: set[str] = set()
    automatic = 0
    try:
        fields = list(_string.Formatter().parse(template))
    except ValueError:
        raise NotLiteral from None  # unbalanced braces: what the running code raises on
    for text, field, spec, conversion in fields:
        parts.append(text)
        if field is None:
            continue
        if spec:
            raise NotLiteral  # a format spec is applied by the running code, as in an f-string
        value: Any
        if field == "" or field.isdigit():
            numbering.add("automatic" if field == "" else "manual")
            index = automatic if field == "" else int(field)
            automatic += field == ""
            if len(numbering) > 1 or index >= len(positional):
                raise NotLiteral  # what the running code raises on
            value = positional[index]
        elif field.isidentifier() and field in named:
            value = named[field]
        else:
            raise NotLiteral  # ``{0.name}``, ``{items[0]}``, or a name the call does not give
        if not isinstance(value, str | int | float):
            raise NotLiteral
        parts.append(repr(value) if conversion == "r" else ascii(value) if conversion == "a" else str(value))
    return _bounded("".join(parts))


# --------------------------------------------------------------- templates
#
# A template is written in the syntax of ``str.format``: literal text with every brace doubled,
# and each part that the running code fills in as a placeholder in single braces. A placeholder
# whose value the source spells out is filled in already.


def _escaped(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def format_template(resolver: Resolver, node: ast.expr, scope: Scope, line: int) -> str | None:
    """``"You are {role}.".format(...)`` as a template: the string it is called on, filled in where it can be."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"):
        return None
    template = string(resolver, node.func.value, scope, line)
    if template is None:
        return None
    try:
        fields = list(_string.Formatter().parse(template))
    except ValueError:
        return None  # unbalanced braces: what the running code raises on
    spread = any(isinstance(item, ast.Starred) for item in node.args) or any(item.arg is None for item in node.keywords)
    named = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}

    def given(field: str, automatic: int) -> ast.expr | None:
        """The argument a placeholder takes; ``None`` when that is decided at run time."""
        if spread:
            return None  # ``*values`` or ``**values``: which placeholder gets what is decided at run time
        if field == "" or field.isdigit():
            index = automatic if field == "" else int(field)
            return node.args[index] if index < len(node.args) else None
        return named.get(field) if field.isidentifier() else None

    parts: list[str] = []
    automatic = 0
    for text, field, spec, conversion in fields:
        parts.append(_escaped(text))
        if field is None:
            continue
        argument = given(field, automatic)
        automatic += field == ""
        filled = None if argument is None or spec or conversion else _scalar(resolver, argument, scope, line)
        if filled is not None:
            parts.append(_escaped(filled))
        else:
            parts.append("{" + field + (f"!{conversion}" if conversion else "") + (f":{spec}" if spec else "") + "}")
    return "".join(parts)


def _scalar(resolver: Resolver, node: ast.expr, scope: Scope, line: int) -> str | None:
    """A literal string or number as ``str.format`` writes it; ``None`` for anything else."""
    try:
        value = literal(resolver, node, scope, line)
    except NotLiteral:
        return None
    return str(value) if isinstance(value, str | int | float) and not isinstance(value, bool) else None


def fstring_template(node: ast.JoinedStr, read: Callable[[ast.expr], Any] | None = None) -> str:
    """An f-string as a template, each placeholder as ``{expression}``.

    With ``read``, a placeholder whose value it reads is filled in.
    """
    parts: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            parts.append(_escaped(str(part.value)))
        elif isinstance(part, ast.FormattedValue):
            if read is not None:
                try:
                    parts.append(_escaped(_fstring_part(part, read)))
                    continue
                except NotLiteral:
                    pass
            parts.append("{" + _placeholder(part) + "}")
    return "".join(parts)


def _placeholder(part: ast.FormattedValue) -> str:
    try:
        text = ast.unparse(part.value)
    except (RecursionError, ValueError):
        text = "..."
    text = clip_expression(text)
    if text.startswith("{"):
        text = " " + text  # ``{ {...}[key]}``: not to be read as a doubled brace
    if part.conversion in (ord("r"), ord("s"), ord("a")):
        text += f"!{chr(part.conversion)}"
    spec = part.format_spec
    if isinstance(spec, ast.JoinedStr):
        text += ":" + "".join(str(item.value) if isinstance(item, ast.Constant) else "{...}" for item in spec.values)
    return text
