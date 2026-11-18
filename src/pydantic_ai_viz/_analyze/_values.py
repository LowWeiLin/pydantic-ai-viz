"""Reading literal values out of expressions, without evaluating anything.

A value is read only when the source spells it out: constants, containers of
constants, string concatenation, and a name that is assigned such a value once
and whose value is not changed in place afterwards. A
handful of pure string helpers that prompts are commonly wrapped in
(``textwrap.dedent``, ``str.strip``) are applied too. Everything else raises
``NotLiteral``; the caller then reports the source text instead of a value.

There is no ``eval`` and no ``ast.literal_eval`` here: both walk recursively
without a bound. This reader counts what it visits and gives up on anything
deeper or larger than a prompt or a settings dict could plausibly be.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from ._resolve import Expression, External, Resolver
from ._source import Scope, clip_expression

MAX_DEPTH = 24
MAX_NODES = 5_000
MAX_STRING_CHARS = 200_000
_MAX_INT = 2**63


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
        if any(not isinstance(part, ast.Constant) for part in node.values):
            raise NotLiteral  # an f-string with placeholders is a template, not a value
        return _bounded("".join(str(part.value) for part in node.values if isinstance(part, ast.Constant)))
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
    if isinstance(node, ast.Name | ast.Attribute) and resolver.changes(node, expression.scope, expression.line):
        raise NotLiteral  # appended to, updated or otherwise changed after it was assigned
    if isinstance(node, ast.Name):
        binding = resolver.lookup(node.id, expression.scope, expression.line)
        if binding is not None and len(binding.scope.bindings[node.id]) > 1:
            raise NotLiteral  # assigned more than once: which value applies depends on the run
        if binding is not None and binding.kind == "assign" and isinstance(binding.node, ast.expr):
            # One assignment at a time, so every name on the way is held to the same rule.
            return _read(resolver, Expression(binding.node, binding.scope, binding.line - 1), depth + 1, budget)
    if isinstance(node, ast.Name | ast.Attribute):
        target = resolver.resolve(node, expression.scope, expression.line)
        if isinstance(target, Expression) and target.node is not node and not resolver.changed():
            return _read(resolver, target, depth + 1, budget)
        raise NotLiteral
    if isinstance(node, ast.Call):
        return _string_helper(resolver, expression, node, depth, budget)
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


def _string_helper(resolver: Resolver, expression: Expression, node: ast.Call, depth: int, budget: _Budget) -> str:
    """``dedent("...")``, ``cleandoc("...")`` and ``"...".strip()``: pure functions of a literal string."""
    if node.keywords:
        raise NotLiteral
    function = node.func
    if isinstance(function, ast.Attribute) and function.attr in ("strip", "lstrip", "rstrip") and not node.args:
        value = _read(resolver, Expression(function.value, expression.scope, expression.line), depth + 1, budget)
        if isinstance(value, str):
            return getattr(value, function.attr)()
        raise NotLiteral
    if len(node.args) != 1 or isinstance(node.args[0], ast.Starred):
        raise NotLiteral
    callee = resolver.callee(node, expression.scope, expression.line)
    if not isinstance(callee, External):
        raise NotLiteral
    if callee.name == "textwrap.dedent":
        helper = textwrap.dedent
    elif callee.name == "inspect.cleandoc":
        helper = inspect.cleandoc
    else:
        raise NotLiteral
    value = _read(resolver, Expression(node.args[0], expression.scope, expression.line), depth + 1, budget)
    if not isinstance(value, str):
        raise NotLiteral
    return helper(value)


# --------------------------------------------------------------- templates


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
