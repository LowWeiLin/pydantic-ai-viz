"""JSON Schema from type annotations, read from source.

Pydantic builds the real schemas from live classes. This module builds the
closest thing that can be had from annotations alone: it follows the same layout
(``properties``, ``required``, ``$defs`` with ``$ref``, ``anyOf`` for unions) so
the viewer can draw both alike, and it never invents a type. An annotation that
cannot be traced to something it understands becomes ``{"x-python-type": "<source
text>"}``, a schema that accepts anything and says what was written.

Understood: the builtin scalars and containers, ``typing`` generics, ``X | Y``,
``Optional``, ``Literal``, ``Annotated`` with a pydantic ``Field``, a few common
standard-library and pydantic types, and classes defined in the analysed files
that are pydantic models, dataclasses, ``TypedDict``s, ``NamedTuple``s or enums.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, cast

from ._resolve import Definition, Expression, External, Resolver
from ._source import FunctionNode, Scope, clip_expression
from ._values import NotLiteral, literal

Schema: TypeAlias = dict[str, Any]
ClassKind: TypeAlias = Literal["model", "dataclass", "typeddict", "namedtuple", "enum"]

UNRESOLVED_KEY = "x-python-type"
"""Schema keyword that carries the source text of a type static analysis could not resolve."""

MAX_DEPTH = 24
MAX_NODES = 4_000
MAX_FORWARD_REFERENCE_CHARS = 500

_SCALARS: dict[tuple[str, str], Schema] = {
    ("builtins", "str"): {"type": "string"},
    ("builtins", "int"): {"type": "integer"},
    ("builtins", "float"): {"type": "number"},
    ("builtins", "bool"): {"type": "boolean"},
    ("builtins", "bytes"): {"format": "binary", "type": "string"},
    ("builtins", "None"): {"type": "null"},
    ("builtins", "object"): {},
    ("typing", "Any"): {},
    ("typing_extensions", "Any"): {},
    ("builtins", "list"): {"items": {}, "type": "array"},
    ("builtins", "tuple"): {"items": {}, "type": "array"},
    ("builtins", "set"): {"items": {}, "type": "array", "uniqueItems": True},
    ("builtins", "frozenset"): {"items": {}, "type": "array", "uniqueItems": True},
    ("builtins", "dict"): {"additionalProperties": True, "type": "object"},
    ("datetime", "datetime"): {"format": "date-time", "type": "string"},
    ("datetime", "date"): {"format": "date", "type": "string"},
    ("datetime", "time"): {"format": "time", "type": "string"},
    ("datetime", "timedelta"): {"format": "duration", "type": "string"},
    ("uuid", "UUID"): {"format": "uuid", "type": "string"},
    ("decimal", "Decimal"): {"anyOf": [{"type": "number"}, {"type": "string"}]},
    ("pathlib", "Path"): {"format": "path", "type": "string"},
    ("pydantic", "HttpUrl"): {"format": "uri", "maxLength": 2083, "minLength": 1, "type": "string"},
    ("pydantic", "AnyHttpUrl"): {"format": "uri", "minLength": 1, "type": "string"},
    ("pydantic", "AnyUrl"): {"format": "uri", "minLength": 1, "type": "string"},
    ("pydantic", "EmailStr"): {"format": "email", "type": "string"},
    ("pydantic", "SecretStr"): {"format": "password", "type": "string", "writeOnly": True},
    ("pydantic", "PositiveInt"): {"exclusiveMinimum": 0, "type": "integer"},
    ("pydantic", "NonNegativeInt"): {"minimum": 0, "type": "integer"},
    ("pydantic", "PositiveFloat"): {"exclusiveMinimum": 0, "type": "number"},
    ("pydantic", "NonNegativeFloat"): {"minimum": 0, "type": "number"},
    ("pydantic", "AwareDatetime"): {"format": "date-time", "type": "string"},
    ("pydantic", "NaiveDatetime"): {"format": "date-time", "type": "string"},
    ("pydantic", "PastDatetime"): {"format": "date-time", "type": "string"},
    ("pydantic", "FutureDatetime"): {"format": "date-time", "type": "string"},
    ("pydantic", "PastDate"): {"format": "date", "type": "string"},
    ("pydantic", "FutureDate"): {"format": "date", "type": "string"},
    ("pydantic", "NegativeInt"): {"exclusiveMaximum": 0, "type": "integer"},
    ("pydantic", "NonPositiveInt"): {"maximum": 0, "type": "integer"},
    ("pydantic", "NegativeFloat"): {"exclusiveMaximum": 0, "type": "number"},
    ("pydantic", "NonPositiveFloat"): {"maximum": 0, "type": "number"},
    ("pydantic", "FiniteFloat"): {"type": "number"},
    ("pydantic", "UUID1"): {"format": "uuid1", "type": "string"},
    ("pydantic", "UUID4"): {"format": "uuid4", "type": "string"},
    ("pydantic", "FilePath"): {"format": "file-path", "type": "string"},
    ("pydantic", "DirectoryPath"): {"format": "directory-path", "type": "string"},
    ("pydantic", "Base64Str"): {"format": "base64", "type": "string"},
    ("types", "NoneType"): {"type": "null"},
    ("pydantic", "StrictStr"): {"type": "string"},
    ("pydantic", "StrictInt"): {"type": "integer"},
    ("pydantic", "StrictFloat"): {"type": "number"},
    ("pydantic", "StrictBool"): {"type": "boolean"},
}

NOT_FROM_THE_MODEL = ("DeferredToolRequests",)
"""Output types of Pydantic AI that a run can end with, but that the model does not write itself."""
_NOTHING: Schema = {"not": {}}
"""The schema nothing satisfies: what such a type contributes to the schema the model is given."""

_TYPING = frozenset({"typing", "typing_extensions", "collections", "builtins"})
_ARRAYS = frozenset(
    {"list", "List", "Sequence", "MutableSequence", "Iterable", "Iterator", "Collection", "deque", "AsyncIterable"}
)
_SETS = frozenset({"set", "Set", "frozenset", "FrozenSet", "AbstractSet", "MutableSet"})
_MAPPINGS = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "defaultdict", "OrderedDict"})
_TRANSPARENT = frozenset({"Final", "Required", "NotRequired", "ReadOnly", "ClassVar", "InitVar"})

_CONSTRAINTS: dict[str, str] = {
    "gt": "exclusiveMinimum",
    "ge": "minimum",
    "lt": "exclusiveMaximum",
    "le": "maximum",
    "multiple_of": "multipleOf",
    "pattern": "pattern",
    "title": "title",
    "examples": "examples",
}

_LITERAL_TYPES: dict[type, str] = {str: "string", int: "integer", bool: "boolean", float: "number"}


class _Unset:
    """No default value is known (which is different from a default of ``None``)."""


_UNSET = _Unset()


@dataclass(slots=True)
class _Field:
    name: str
    annotation: Expression | None
    required: bool = True
    default: Any = _UNSET
    extras: Schema = field(default_factory=dict[str, Any])
    """Schema keywords from ``Field(...)``: the description and the constraints."""
    lengths: dict[str, Any] = field(default_factory=dict[str, Any])
    """``min_length`` and ``max_length``, whose keyword depends on the type they apply to."""
    variadic: bool = False
    """A ``*args`` parameter: the annotation is the type of each item."""


def _wrapper_name(target: object) -> str | None:
    """The name of a ``typing`` construct, if that is what a subscript's base resolved to."""
    if isinstance(target, External) and target.name.partition(".")[0] in _TYPING:
        return target.last
    return None


class SchemaBuilder:
    """Builds the schemas of one document; nested classes are shared through ``$defs``."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.defs: dict[str, Schema] = {}
        self.unresolved: list[str] = []
        """Source text of each type that could not be resolved, once each, in order."""
        self.locations: list[Definition] = []
        """The classes whose fields were read, in the order they were first reached."""
        self._names: dict[int, str] = {}
        self._nodes = 0

    # ------------------------------------------------------------- public

    def annotation(self, node: ast.expr, scope: Scope, line: int | None = None) -> Schema:
        """The schema of a type annotation evaluated in ``scope``."""
        return self._type(Expression(node, scope, node.lineno if line is None else line), 0)

    def document(self, schema: Schema) -> Schema:
        """``schema`` as a complete document, with its ``$defs``.

        As pydantic does, a class that is the whole schema, or a direct member of a
        top-level union, is written in place unless something else refers to it too.
        """
        defs = dict(self.defs)
        whole: Schema = {"$defs": defs, **schema}

        def in_place(item: Schema) -> Schema:
            reference: Any = item.get("$ref")
            if len(item) == 1 and isinstance(reference, str) and _count(reference, whole) == 1:
                return defs.pop(reference.rpartition("/")[2], item)
            return item

        members: Any = schema.get("anyOf")
        if len(schema) == 1 and isinstance(members, list):
            schema = {"anyOf": [in_place(member) for member in cast("list[Schema]", members)]}
        else:
            schema = in_place(schema)
        return _sorted({"$defs": defs, **schema} if defs else schema)

    def parameters(self, function: Definition, *, skip: int, descriptions: dict[str, str]) -> Schema:
        """The schema of a tool's arguments: one property per parameter after the first ``skip``."""
        node = function.node
        assert not isinstance(node, ast.ClassDef)
        fields = self._parameters(node, function.scope, skip)
        variadic = node.args.kwarg is not None
        if len(fields) == 1 and not variadic and fields[0].annotation is not None:
            # Pydantic AI sends a lone model-like argument as that model, not wrapped in an object.
            single = self._type(fields[0].annotation, 0)
            reference: Any = single.get("$ref")
            if len(single) == 1 and isinstance(reference, str):
                return self.document(single)
        properties: Schema = {}
        required: list[str] = []
        for item in fields:
            schema = self._field_schema(item, 0)
            if "description" not in schema and item.name in descriptions:
                schema["description"] = descriptions[item.name]
            properties[item.name] = schema
            if item.required:
                required.append(item.name)
        result: Schema = {"additionalProperties": variadic, "properties": properties}
        if required:
            result["required"] = required
        result["type"] = "object"
        return self.document(result)

    # -------------------------------------------------------------- types

    def _unresolved(self, text: str) -> Schema:
        if text not in self.unresolved:
            self.unresolved.append(text)
        return {UNRESOLVED_KEY: text}

    def _type(self, expression: Expression, depth: int) -> Schema:
        self._nodes += 1
        node = expression.node
        text = _text(expression)
        if depth > MAX_DEPTH or self._nodes > MAX_NODES:
            return self._unresolved(text)
        if isinstance(node, ast.Constant):
            if node.value is None:
                return {"type": "null"}
            if isinstance(node.value, str):
                return self._forward_reference(node.value, expression, depth)
            return self._unresolved(text)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            members: list[ast.expr] = []
            current: ast.expr = node
            while isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
                members.append(current.right)
                current = current.left
            members.append(current)
            return _any_of([self._type(self._sub(expression, member), depth + 1) for member in reversed(members)])
        if isinstance(node, ast.Subscript):
            return self._subscript(node, expression, depth)
        if isinstance(node, ast.Name | ast.Attribute):
            target = self.resolver.resolve(node, expression.scope, expression.line)
            if isinstance(target, External):
                if target.is_library(*NOT_FROM_THE_MODEL):
                    return dict(_NOTHING)
                known = _SCALARS.get((target.name.partition(".")[0], target.last))
                return dict(known) if known is not None else self._unresolved(text)
            if isinstance(target, Definition):
                if isinstance(target.node, ast.ClassDef):
                    return self._class(target, depth)
                return self._unresolved(text)
            if isinstance(target, Expression) and target.node is not node:
                return self._alias(target, text, depth)
        return self._unresolved(text)

    def _alias(self, target: Expression, text: str, depth: int) -> Schema:
        """``Name = <type expression>``. A ``NewType`` is the type it wraps."""
        node = target.node
        if isinstance(node, ast.Call):
            callee = self.resolver.callee(node, target.scope, target.line)
            if _wrapper_name(callee) == "NewType" and len(node.args) == 2:
                return self._type(self._sub(target, node.args[1]), depth + 1)
            if _wrapper_name(callee) == "TypedDict" and len(node.args) == 2 and isinstance(node.args[1], ast.Dict):
                return self._functional_typed_dict(target, node, text, depth)
            return self._unresolved(text)
        return self._type(target, depth + 1)

    def _functional_typed_dict(self, target: Expression, call: ast.Call, text: str, depth: int) -> Schema:
        """``Point = TypedDict("Point", {"x": float, "y": float})``: the keys are the fields."""
        fields = call.args[1]
        assert isinstance(fields, ast.Dict)
        total = next((keyword.value for keyword in call.keywords if keyword.arg == "total"), None)
        optional = isinstance(total, ast.Constant) and total.value is False
        properties: Schema = {}
        for key, value in zip(fields.keys, fields.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return self._unresolved(text)
            properties[key.value] = self._type(self._sub(target, value), depth + 1)
        title = call.args[0].value if isinstance(call.args[0], ast.Constant) else None
        schema: Schema = {"properties": properties}
        if properties and not optional:
            schema["required"] = list(properties)
        if isinstance(title, str):
            schema["title"] = title
        schema["type"] = "object"
        return schema

    def _sub(self, parent: Expression, node: ast.expr) -> Expression:
        return Expression(node, parent.scope, parent.line)

    def _forward_reference(self, source: str, expression: Expression, depth: int) -> Schema:
        if len(source) > MAX_FORWARD_REFERENCE_CHARS:
            return self._unresolved(_text(expression))
        try:
            parsed = ast.parse(source.strip(), mode="eval").body
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return self._unresolved(source)
        # Positions in the parsed string are positions in that string, not in the file. Without
        # them, the text of a node is unparsed instead of sliced from the wrong place.
        for child in ast.walk(parsed):
            for attribute in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
                if hasattr(child, attribute):
                    delattr(child, attribute)
        return self._type(Expression(parsed, expression.scope, expression.line), depth + 1)

    def _subscript(self, node: ast.Subscript, expression: Expression, depth: int) -> Schema:
        text = _text(expression)
        arguments = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        base = self.resolver.resolve(node.value, expression.scope, expression.line)
        if isinstance(base, Definition) and isinstance(base.node, ast.ClassDef):
            return self._class(base, depth)  # a generic class of the project: its parameters are not applied
        name = _wrapper_name(base)
        if name is None or not arguments:
            return self._unresolved(text)

        def of(index: int) -> Schema:
            return self._type(self._sub(expression, arguments[index]), depth + 1)

        if name in _ARRAYS:
            return {"items": of(0), "type": "array"}
        if name in _SETS:
            return {"items": of(0), "type": "array", "uniqueItems": True}
        if name in ("tuple", "Tuple"):
            if len(arguments) == 2 and isinstance(arguments[1], ast.Constant) and arguments[1].value is Ellipsis:
                return {"items": of(0), "type": "array"}
            items = [of(index) for index in range(len(arguments))]
            return {"maxItems": len(items), "minItems": len(items), "prefixItems": items, "type": "array"}
        if name in _MAPPINGS:
            # A value type of "anything" is written ``true``, as pydantic writes it.
            return {"additionalProperties": (of(1) or True) if len(arguments) > 1 else True, "type": "object"}
        if name == "Optional":
            return _any_of([of(0), {"type": "null"}])
        if name == "Union":
            return _any_of([of(index) for index in range(len(arguments))])
        if name == "Literal":
            return self._literal(arguments, expression, text)
        if name == "Annotated":
            schema = of(0)
            for extra in arguments[1:]:
                described = self._field_call(extra, expression)
                if described is not None:
                    schema = _with_extras(schema, described)
            return schema
        if name in _TRANSPARENT:
            return of(0)
        return self._unresolved(text)

    def _literal(self, arguments: list[ast.expr], expression: Expression, text: str) -> Schema:
        values: list[Any] = []
        for argument in arguments:
            try:
                values.append(literal(self.resolver, argument, expression.scope, expression.line))
            except NotLiteral:
                return self._unresolved(text)  # an enum member, for one
        if values == [None]:
            return {"type": "null"}
        schema: Schema = {"const": values[0]} if len(values) == 1 else {"enum": values}
        kind = _common_type(values)
        if kind is not None:
            schema["type"] = kind
        return schema

    # ------------------------------------------------------------ classes

    def _class(self, definition: Definition, depth: int) -> Schema:
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        known = self._names.get(id(node))
        if known is not None:
            return {"$ref": f"#/$defs/{known}"}
        kind = self._kind(definition, 0)
        if kind is None:
            return self._unresolved(node.name)
        name = node.name
        suffix = 2
        while name in self.defs or name in self._names.values():
            name = f"{node.name}_{suffix}"
            suffix += 1
        if kind == "enum":
            schema = self._enum(definition)
            if UNRESOLVED_KEY in schema:
                return schema
            self._names[id(node)] = name
            self.defs[name] = schema
            return {"$ref": f"#/$defs/{name}"}
        self._names[id(node)] = name  # registered before the fields are read, so a recursive model ends in a $ref
        self.locations.append(definition)
        fields = self._class_fields(definition, kind, 0)
        body: Schema
        if kind == "namedtuple":
            items = [self._field_schema(item, depth + 1) for item in fields]
            required = sum(1 for item in fields if item.required)
            body = {"maxItems": len(items), "minItems": required, "prefixItems": items, "type": "array"}
        else:
            body = {"properties": {item.name: self._field_schema(item, depth + 1) for item in fields}}
            required_names = [item.name for item in fields if item.required]
            if required_names:
                body["required"] = required_names
            body["title"] = node.name
            body["type"] = "object"
        description = ast.get_docstring(node)
        if description:
            body["description"] = description
        self.defs[name] = body
        return {"$ref": f"#/$defs/{name}"}

    def _kind(self, definition: Definition, depth: int) -> ClassKind | None:
        """What kind of schema-bearing class this is, judged by its decorators and bases."""
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        if depth > 8:
            return None
        for decorator in node.decorator_list:
            target = self._callable(decorator, definition.scope)
            if isinstance(target, External) and target.last == "dataclass":
                return "dataclass"
        for base in node.bases:
            target = self._callable(base, definition.scope)
            if isinstance(target, External):
                if target.last in ("BaseModel", "BaseSettings", "SQLModel"):
                    return "model"
                if target.last == "TypedDict":
                    return "typeddict"
                if target.last == "NamedTuple":
                    return "namedtuple"
                if target.comes_from("enum", "Enum", "StrEnum", "IntEnum"):
                    return "enum"
            elif isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
                inherited = self._kind(target, depth + 1)
                if inherited is not None:
                    return inherited
        return None

    def _callable(self, node: ast.expr, scope: Scope) -> object:
        """What a decorator or base class is: ``@dataclass(frozen=True)`` and ``Base[T]`` name their callee."""
        if isinstance(node, ast.Call):
            node = node.func
        if isinstance(node, ast.Subscript):
            node = node.value
        return self.resolver.resolve(node, scope)

    def _class_fields(self, definition: Definition, kind: ClassKind, depth: int) -> list[_Field]:
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        fields: dict[str, _Field] = {}
        if depth <= 8:
            for base in node.bases:
                target = self._callable(base, definition.scope)
                if isinstance(target, Definition) and isinstance(target.node, ast.ClassDef):
                    for inherited in self._class_fields(target, kind, depth + 1):
                        fields[inherited.name] = inherited
        total = True
        for keyword in node.keywords:
            if keyword.arg == "total" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                total = False
        body = definition.body
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            name = statement.target.id
            annotation = Expression(statement.annotation, body, statement.lineno)
            wrapper = self._outer_wrapper(annotation)
            if wrapper == "ClassVar" or name == "model_config" or (kind == "model" and name.startswith("_")):
                continue
            item = _Field(name, annotation)
            if kind == "typeddict":
                item.required = wrapper == "Required" or (total and wrapper != "NotRequired")
            elif statement.value is not None:
                self._default(item, Expression(statement.value, body, statement.lineno))
            fields[item.name] = item
        return list(fields.values())

    def _outer_wrapper(self, annotation: Expression) -> str | None:
        node = annotation.node
        if isinstance(node, ast.Subscript):
            node = node.value
        if isinstance(node, ast.Name | ast.Attribute):
            return _wrapper_name(self.resolver.resolve(node, annotation.scope, annotation.line))
        return None

    def _default(self, item: _Field, value: Expression) -> None:
        """Read a class-body default: a plain value, or a pydantic ``Field(...)`` / dataclasses ``field(...)``."""
        item.required = False
        described = self._field_call(value.node, value)
        if described is None:
            item.default = self._literal_or_unset(value.node, value)
            return
        item.extras, item.lengths = described.extras, described.lengths
        item.default, item.required = described.default, described.required
        if described.name is not None:
            item.name = described.name

    def _literal_or_unset(self, node: ast.expr, context: Expression) -> Any:
        try:
            return literal(self.resolver, node, context.scope, context.line)
        except NotLiteral:
            return _UNSET

    def _field_call(self, node: ast.expr, context: Expression) -> _FieldCall | None:
        """The contents of a ``Field(...)`` or ``field(...)`` call, or ``None`` when ``node`` is not one."""
        if not isinstance(node, ast.Call):
            return None
        callee = self.resolver.callee(node, context.scope, context.line)
        if not isinstance(callee, External) or callee.last not in ("Field", "field"):
            return None
        if callee.name.partition(".")[0] not in ("pydantic", "dataclasses", "sqlmodel"):
            return None
        result = _FieldCall()
        if node.args and not isinstance(node.args[0], ast.Starred) and callee.last == "Field":
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value is Ellipsis:
                result.required = True
            else:
                result.default = self._literal_or_unset(first, context)
        else:
            result.required = True
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            if keyword.arg == "default":
                result.required = isinstance(keyword.value, ast.Constant) and keyword.value.value is Ellipsis
                if not result.required:
                    result.default = self._literal_or_unset(keyword.value, context)
            elif keyword.arg == "default_factory":
                result.required = False
            elif keyword.arg in ("alias", "validation_alias"):
                alias = self._literal_or_unset(keyword.value, context)
                if isinstance(alias, str) and result.name is None:
                    result.name = alias
            elif keyword.arg in ("min_length", "max_length"):
                value = self._literal_or_unset(keyword.value, context)
                if isinstance(value, int):
                    result.lengths[keyword.arg] = value
            elif keyword.arg == "description" or keyword.arg in _CONSTRAINTS:
                value = self._literal_or_unset(keyword.value, context)
                if not isinstance(value, _Unset) and value is not None:
                    result.extras[_CONSTRAINTS.get(keyword.arg, keyword.arg)] = value
        return result

    def _field_schema(self, item: _Field, depth: int) -> Schema:
        schema = {} if item.annotation is None else self._type(item.annotation, depth)
        if item.variadic:
            schema = {"items": schema, "type": "array"}
        schema = _with_extras(schema, _FieldCall(extras=item.extras, lengths=item.lengths))
        if not isinstance(item.default, _Unset):
            schema["default"] = item.default
        return schema

    def _enum(self, definition: Definition) -> Schema:
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        values: list[Any] = []
        body = definition.body
        for statement in node.body:
            if isinstance(statement, ast.Assign) and all(isinstance(target, ast.Name) for target in statement.targets):
                try:
                    values.append(literal(self.resolver, statement.value, body, statement.lineno))
                except NotLiteral:
                    return self._unresolved(node.name)  # ``auto()`` and the like
        if not values:
            return self._unresolved(node.name)
        schema: Schema = {"enum": values, "title": node.name}
        kind = _common_type(values)
        if kind is not None:
            schema["type"] = kind
        return schema

    # --------------------------------------------------------- parameters

    def _parameters(self, node: FunctionNode, scope: Scope, skip: int) -> list[_Field]:
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults)) + list(
            arguments.defaults
        )
        pairs: list[tuple[ast.arg, ast.expr | None, bool]] = [
            *((parameter, default, False) for parameter, default in zip(positional, defaults, strict=True))
        ][skip:]
        if arguments.vararg is not None:
            pairs.append((arguments.vararg, None, True))
        pairs.extend(
            (parameter, default, False)
            for parameter, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
        )
        fields: list[_Field] = []
        for parameter, default, variadic in pairs:
            annotation = None if parameter.annotation is None else Expression(parameter.annotation, scope, node.lineno)
            item = _Field(parameter.arg, annotation, variadic=variadic)
            if default is not None:
                self._default(item, Expression(default, scope, node.lineno))
                item.name = parameter.arg  # an alias does not rename a function parameter
            fields.append(item)
        return fields


@dataclass(slots=True)
class _FieldCall:
    required: bool = False
    default: Any = _UNSET
    name: str | None = None
    extras: Schema = field(default_factory=dict[str, Any])
    lengths: dict[str, Any] = field(default_factory=dict[str, Any])


def _text(expression: Expression) -> str:
    """Source text of a type expression; one written inside a string annotation is unparsed."""
    node = expression.node
    if hasattr(node, "lineno"):
        return expression.module.text(node)
    try:
        return clip_expression(ast.unparse(node))
    except (RecursionError, ValueError):
        return "..."


def _with_extras(schema: Schema, described: _FieldCall) -> Schema:
    """``schema`` with the description and constraints of a ``Field(...)`` applied."""
    result = {**schema, **described.extras}
    is_array = schema.get("type") == "array"
    for key, value in described.lengths.items():
        if key == "min_length":
            result["minItems" if is_array else "minLength"] = value
        else:
            result["maxItems" if is_array else "maxLength"] = value
    return result


_NAME_MAPS = frozenset({"properties", "$defs"})
_VALUES = frozenset({"default", "const", "enum", "examples"})


def _sorted(schema: Schema) -> Schema:
    """``schema`` with its keywords in alphabetical order, as pydantic writes them.

    Property and definition names keep the order of the source, and values that
    belong to the user (defaults, enum members) are left alone.
    """
    result: Schema = {}
    for key in sorted(schema):
        value: Any = schema[key]
        if key in _VALUES:
            result[key] = value
        elif key in _NAME_MAPS and isinstance(value, dict):
            named = cast("dict[str, Any]", value)
            result[key] = {name: _sorted_item(item) for name, item in named.items()}
        elif isinstance(value, dict):
            result[key] = _sorted(cast("Schema", value))
        elif isinstance(value, list):
            items = cast("list[Any]", value)
            result[key] = [_sorted_item(item) for item in items]
        else:
            result[key] = value
    return result


def _sorted_item(item: Any) -> Any:
    return _sorted(cast("Schema", item)) if isinstance(item, dict) else item


def _common_type(values: list[Any]) -> str | None:
    """The JSON type shared by every value of a ``Literal`` or an enum, if there is one."""
    names = {_LITERAL_TYPES.get(type(value)) for value in values}  # pyright: ignore[reportUnknownArgumentType]
    return names.pop() if len(names) == 1 else None


def _any_of(members: list[Schema]) -> Schema:
    """A union: nested unions are flattened and equal members merged."""
    flat: list[Schema] = []
    for member in members:
        if member == _NOTHING and len(members) > 1:
            continue  # a member the model never writes is no part of what it is asked for
        nested: Any = member.get("anyOf")
        candidates: list[Schema] = [member]
        if len(member) == 1 and isinstance(nested, list):
            candidates = cast("list[Schema]", nested)
        for candidate in candidates:
            if candidate not in flat:
                flat.append(candidate)
    return flat[0] if len(flat) == 1 else {"anyOf": flat}


def _count(reference: str, value: Any) -> int:
    """How many times a ``$ref`` to ``reference`` occurs in ``value``."""
    found = 0
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in cast("dict[str, Any]", item).items():
                if key == "$ref" and child == reference:
                    found += 1
                else:
                    stack.append(child)
        elif isinstance(item, list):
            stack.extend(cast("list[Any]", item))
    return found
