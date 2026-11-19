"""Reading literal values out of expressions, without evaluating anything.

A value is read only when the source spells it out: constants, containers of
constants, string concatenation, f-strings and ``"...".format(...)`` whose every
placeholder is itself such a value, the ``strip`` methods of ``str``, ``a if condition else b`` whose
condition is such a value, and a name that is assigned
such a value once and whose value is not changed in place afterwards. A call of
any function, of the standard library or not, is not evaluated. Everything else
raises ``NotLiteral``; the caller then reports the source text instead of a value.

There is no ``eval`` and no ``ast.literal_eval`` here: both walk recursively
without a bound. This reader counts what it visits and gives up on anything
deeper or larger than a prompt or a settings dict could plausibly be.
"""

from __future__ import annotations

import ast
import string as _string
from collections.abc import Callable
from typing import Any

from ._resolve import Expression, Resolver
from ._source import Scope, clip_expression

MAX_DEPTH = 24
MAX_NODES = 5_000
MAX_STRING_CHARS = 200_000
_MAX_INT = 2**63
_STR_METHODS = frozenset({"strip", "lstrip", "rstrip"})
"""Methods of ``str`` applied to a literal string, when called without arguments."""


class NotLiteral(Exception):
    """The expression is not a literal value that can be read from source."""


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
        if isinstance(node.func, ast.Attribute) and node.func.attr in _STR_METHODS:
            return _string_method(resolver, expression, node, depth, budget)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return _format(node, child)
        # What a function or method of the analysed files returns, when it has one ``return``.
        return _followed(resolver, expression, depth, budget)
    raise NotLiteral


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


def format_template(resolver: Resolver, node: ast.expr, scope: Scope, line: int) -> str | None:
    """``"You are {role}.".format(...)`` as a template: the string it is called on, braces and all."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"):
        return None
    return string(resolver, node.func.value, scope, line)


def fstring_template(node: ast.JoinedStr) -> str:
    """An f-string as a template: its literal text with each placeholder as ``{expression}``."""
    parts: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            parts.append(str(part.value))
        elif isinstance(part, ast.FormattedValue):
            parts.append("{" + _placeholder(part) + "}")
    return "".join(parts)


def _placeholder(part: ast.FormattedValue) -> str:
    try:
        text = ast.unparse(part.value)
    except (RecursionError, ValueError):
        text = "..."
    text = clip_expression(text)
    if part.conversion in (ord("r"), ord("s"), ord("a")):
        text += f"!{chr(part.conversion)}"
    spec = part.format_spec
    if isinstance(spec, ast.JoinedStr):
        text += ":" + "".join(str(item.value) if isinstance(item, ast.Constant) else "{...}" for item in spec.values)
    return text
