"""JSON Schema from annotations, and descriptions from docstrings."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Any

import pytest
from static_support import agent, analyze_source, fixture, one_agent, tool

from pydantic_ai_viz._analyze._docstring import describe, infer_style
from pydantic_ai_viz._analyze._resolve import END_OF_MODULE, Project, Resolver
from pydantic_ai_viz._analyze._schema import SchemaBuilder
from pydantic_ai_viz._analyze._source import Module, parse_module

PRELUDE = """
from __future__ import annotations
import datetime, decimal, enum, pathlib, typing, uuid
import typing as t
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar, Final, Literal, NamedTuple, NewType, Optional, TypedDict, Union
from typing_extensions import NotRequired, Required
from pydantic import BaseModel, EmailStr, Field, HttpUrl, PositiveInt, SecretStr
from elsewhere import Mystery
"""


def schema_of(annotation: str, definitions: str = "") -> tuple[dict[str, Any], list[str]]:
    """The schema document of ``annotation``, and the type names that could not be resolved."""
    source = PRELUDE + textwrap.dedent(definitions) + f"\nTARGET: {annotation}\n"
    module = parse_module(source.encode("utf-8"), Path("types.py"), "types.py")
    assert isinstance(module, Module)
    project = Project(root=Path("."))
    project.add(module)
    builder = SchemaBuilder(Resolver(project))
    statement = module.scope.node.body[-1]
    assert isinstance(statement, ast.AnnAssign)
    schema = builder.annotation(statement.annotation, module.scope, END_OF_MODULE)
    return builder.document(schema), builder.unresolved


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("str", {"type": "string"}),
        ("int", {"type": "integer"}),
        ("float", {"type": "number"}),
        ("bool", {"type": "boolean"}),
        ("None", {"type": "null"}),
        ("bytes", {"format": "binary", "type": "string"}),
        ("Any", {}),
        ("object", {}),
        ("list", {"items": {}, "type": "array"}),
        ("dict", {"additionalProperties": True, "type": "object"}),
        ("list[str]", {"items": {"type": "string"}, "type": "array"}),
        ("typing.List[int]", {"items": {"type": "integer"}, "type": "array"}),
        ("t.Sequence[int]", {"items": {"type": "integer"}, "type": "array"}),
        ("Iterable[float]", {"items": {"type": "number"}, "type": "array"}),
        ("set[str]", {"items": {"type": "string"}, "type": "array", "uniqueItems": True}),
        ("frozenset[int]", {"items": {"type": "integer"}, "type": "array", "uniqueItems": True}),
        ("tuple[int, ...]", {"items": {"type": "integer"}, "type": "array"}),
        (
            "tuple[int, str]",
            {"maxItems": 2, "minItems": 2, "prefixItems": [{"type": "integer"}, {"type": "string"}], "type": "array"},
        ),
        ("dict[str, int]", {"additionalProperties": {"type": "integer"}, "type": "object"}),
        ("Mapping[str, Any]", {"additionalProperties": True, "type": "object"}),
        (
            "dict[str, list[int]]",
            {"additionalProperties": {"items": {"type": "integer"}, "type": "array"}, "type": "object"},
        ),
        ("str | None", {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        ("Optional[int]", {"anyOf": [{"type": "integer"}, {"type": "null"}]}),
        ("Union[int, str, None]", {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]}),
        ("int | str | int", {"anyOf": [{"type": "integer"}, {"type": "string"}]}),
        ("int | (str | None)", {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]}),
        ("Union[int]", {"type": "integer"}),
        ("Literal['a', 'b']", {"enum": ["a", "b"], "type": "string"}),
        ("Literal[1, 2, 3]", {"enum": [1, 2, 3], "type": "integer"}),
        ("Literal['only']", {"const": "only", "type": "string"}),
        ("Literal['a', 1]", {"enum": ["a", 1]}),
        ("Literal[None]", {"type": "null"}),
        ("Final[int]", {"type": "integer"}),
        ("Annotated[int, 'metadata']", {"type": "integer"}),
        (
            "Annotated[str, Field(description='A name.', min_length=2, max_length=9, pattern='^a')]",
            {"description": "A name.", "maxLength": 9, "minLength": 2, "pattern": "^a", "type": "string"},
        ),
        (
            "Annotated[list[int], Field(min_length=1, max_length=3)]",
            {"items": {"type": "integer"}, "maxItems": 3, "minItems": 1, "type": "array"},
        ),
        (
            "Annotated[float, Field(gt=0, lt=1, multiple_of=0.5, title='Ratio', examples=[0.5])]",
            {
                "examples": [0.5],
                "exclusiveMaximum": 1,
                "exclusiveMinimum": 0,
                "multipleOf": 0.5,
                "title": "Ratio",
                "type": "number",
            },
        ),
        ("datetime.datetime", {"format": "date-time", "type": "string"}),
        ("datetime.date", {"format": "date", "type": "string"}),
        ("uuid.UUID", {"format": "uuid", "type": "string"}),
        ("pathlib.Path", {"format": "path", "type": "string"}),
        ("decimal.Decimal", {"anyOf": [{"type": "number"}, {"type": "string"}]}),
        ("HttpUrl", {"format": "uri", "maxLength": 2083, "minLength": 1, "type": "string"}),
        ("EmailStr", {"format": "email", "type": "string"}),
        ("SecretStr", {"format": "password", "type": "string", "writeOnly": True}),
        ("PositiveInt", {"exclusiveMinimum": 0, "type": "integer"}),
        ("'list[int] | None'", {"anyOf": [{"items": {"type": "integer"}, "type": "array"}, {"type": "null"}]}),
        ("list['str']", {"items": {"type": "string"}, "type": "array"}),
    ],
)
def test_annotations_become_the_schema_pydantic_would_write(annotation: str, expected: dict[str, Any]) -> None:
    assert schema_of(annotation) == (expected, [])


@pytest.mark.parametrize(
    ("annotation", "definitions", "unresolved"),
    [
        ("Mystery", "", ["Mystery"]),
        ("list[Mystery]", "", ["Mystery"]),
        ("Mystery[int]", "", ["Mystery[int]"]),
        ("never_defined", "", ["never_defined"]),
        ("typing.Callable[[int], str]", "", ["typing.Callable[[int], str]"]),
        ("type[int]", "", ["type[int]"]),
        ("3", "", ["3"]),
        ("'not valid python ]['", "", ["not valid python ]["]),
        ("'Mystery | None'", "", ["Mystery"]),
        ("Plain", "class Plain:\n    x: int\n", ["Plain"]),  # a class with no schema of its own
        ("helper", "def helper(): ...\n", ["helper"]),
        ("Derived", "class Derived(Mystery):\n    x: int\n", ["Derived"]),
        ("Auto", "class Auto(enum.Enum):\n    A = enum.auto()\n", ["Auto"]),
        ("Literal[Colour.RED]", "class Colour(enum.Enum):\n    RED = enum.auto()\n", ["Literal[Colour.RED]"]),
        ("Made", "Made = make_type()\n", ["Made"]),
        ("x.y.z", "", ["x.y.z"]),
    ],
)
def test_a_type_that_cannot_be_resolved_is_marked_not_guessed(
    annotation: str, definitions: str, unresolved: list[str]
) -> None:
    schema, names = schema_of(annotation, definitions)
    assert names == unresolved
    marker = {"x-python-type": unresolved[0]}
    assert schema == marker or marker in [schema.get("items"), *schema.get("anyOf", [])]


def test_an_enum_member_is_written_as_its_value() -> None:
    definitions = "class Colour(enum.Enum):\n    RED = 'red'\n    BLUE = 'blue'\n"
    assert schema_of("Literal[Colour.RED]", definitions) == ({"const": "red", "type": "string"}, [])
    assert schema_of("Literal[Colour.RED, Colour.BLUE]", definitions)[0] == {"enum": ["red", "blue"], "type": "string"}


def test_aliases_and_new_types_are_followed() -> None:
    definitions = """
        UserId = NewType("UserId", int)
        Names = list[str]
        MaybeNames = Names | None
        Pair: typing.TypeAlias = "tuple[UserId, Names]"
    """
    assert schema_of("UserId", definitions) == ({"type": "integer"}, [])
    assert schema_of("MaybeNames", definitions)[0] == {
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}]
    }
    assert schema_of("Pair", definitions)[0] == {
        "maxItems": 2,
        "minItems": 2,
        "prefixItems": [{"type": "integer"}, {"items": {"type": "string"}, "type": "array"}],
        "type": "array",
    }


def test_models_dataclasses_typed_dicts_named_tuples_and_enums() -> None:
    definitions = '''
        class Size(str, enum.Enum):
            SMALL = "s"
            LARGE = "l"

        class Level(enum.IntEnum):
            LOW = 1
            HIGH = 2

        class Base(BaseModel):
            """The base."""
            id: int
            created: datetime.datetime | None = None

        class Item(Base):
            """An item."""
            model_config = {"extra": "forbid"}
            registry: ClassVar[dict[str, int]] = {}
            _cache: dict = {}
            name: str = Field(..., description="Its name.", min_length=1)
            size: Size = Size.SMALL
            level: Level
            tags: list[str] = Field(default_factory=list)
            price: float = Field(default=1.5, ge=0)
            code: str = Field(alias="itemCode")
            note: str | None = Field(None, description="Free text.")
            computed: int = compute_default()

        @dataclass(frozen=True)
        class Point:
            """A point."""
            x: float
            y: float = 0.0
            tags: list[str] = field(default_factory=list)
            hidden: int = field(default=3, repr=False)

        class Movie(TypedDict, total=False):
            title: Required[str]
            year: int

        class Strict(TypedDict):
            title: str
            rating: NotRequired[float]

        class Row(NamedTuple):
            key: str
            count: int = 0
    '''
    item, unresolved = schema_of("Item", definitions)
    assert unresolved == []
    assert item == {
        "$defs": {
            "Size": {"enum": ["s", "l"], "title": "Size", "type": "string"},
            "Level": {"enum": [1, 2], "title": "Level", "type": "integer"},
        },
        "description": "An item.",
        "properties": {
            "id": {"type": "integer"},  # inherited from Base, in its place
            "created": {"anyOf": [{"format": "date-time", "type": "string"}, {"type": "null"}], "default": None},
            "name": {"description": "Its name.", "minLength": 1, "type": "string"},
            "size": {"$ref": "#/$defs/Size", "default": "s"},  # an enum member is written as its value
            "level": {"$ref": "#/$defs/Level"},
            "tags": {"items": {"type": "string"}, "type": "array"},
            "price": {"default": 1.5, "minimum": 0, "type": "number"},
            "itemCode": {"type": "string"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "Free text."},
            "computed": {"type": "integer"},
        },
        "required": ["id", "name", "level", "itemCode"],
        "title": "Item",
        "type": "object",
    }
    assert schema_of("Point", definitions)[0] == {
        "description": "A point.",
        "properties": {
            "x": {"type": "number"},
            "y": {"default": 0.0, "type": "number"},
            "tags": {"items": {"type": "string"}, "type": "array"},
            "hidden": {"default": 3, "type": "integer"},
        },
        "required": ["x"],
        "title": "Point",
        "type": "object",
    }
    assert schema_of("Movie", definitions)[0]["required"] == ["title"]
    assert schema_of("Strict", definitions)[0]["required"] == ["title"]
    assert schema_of("Row", definitions)[0] == {
        "maxItems": 2,
        "minItems": 1,
        "prefixItems": [{"type": "string"}, {"default": 0, "type": "integer"}],
        "type": "array",
    }
    assert schema_of("Size", definitions)[0] == {"enum": ["s", "l"], "title": "Size", "type": "string"}


def test_nested_and_recursive_models_use_definitions() -> None:
    definitions = """
        class Leaf(BaseModel):
            value: int

        class Tree(BaseModel):
            leaf: Leaf
            children: list[Tree] = []
            parent: "Tree | None" = None

        class Wrapper(BaseModel):
            first: Leaf
            second: Leaf
            unknown: Mystery
    """
    tree, unresolved = schema_of("Tree", definitions)
    assert unresolved == []
    # A model that refers to itself stays in $defs; the document points at it.
    assert tree["$ref"] == "#/$defs/Tree"
    assert set(tree["$defs"]) == {"Leaf", "Tree"}
    assert tree["$defs"]["Tree"]["properties"] == {
        "leaf": {"$ref": "#/$defs/Leaf"},
        "children": {"default": [], "items": {"$ref": "#/$defs/Tree"}, "type": "array"},
        "parent": {"anyOf": [{"$ref": "#/$defs/Tree"}, {"type": "null"}], "default": None},
    }
    wrapper, unresolved = schema_of("Wrapper", definitions)
    assert unresolved == ["Mystery"]
    assert wrapper["properties"] == {
        "first": {"$ref": "#/$defs/Leaf"},
        "second": {"$ref": "#/$defs/Leaf"},
        "unknown": {"x-python-type": "Mystery"},
    }
    assert list(wrapper["$defs"]) == ["Leaf"]
    union, _ = schema_of("Leaf | Wrapper | None", definitions)
    assert [sorted(member) for member in union["anyOf"]] == [
        ["$ref"],  # Leaf is also used inside Wrapper, so it stays a reference
        ["properties", "required", "title", "type"],  # Wrapper is written in place
        ["type"],
    ]


def test_two_classes_with_one_name_get_separate_definitions(tmp_path: Path) -> None:
    blueprint = one_agent(
        tmp_path,
        """
        from pydantic import BaseModel
        from pydantic_ai import Agent

        class Outer(BaseModel):
            class Item(BaseModel):
                inner: int
            item: Item

        class Item(BaseModel):
            outer: str

        class Both(BaseModel):
            a: Outer
            b: Item

        agent = Agent("test", output_type=Both)
        """,
    )
    schema = blueprint.output.json_schema
    assert isinstance(schema, dict)
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    assert sorted(definitions) == ["Item", "Item_2", "Outer"]
    assert definitions["Outer"]["properties"] == {"item": {"$ref": "#/$defs/Item"}}  # type: ignore[index]


def test_schema_building_is_bounded() -> None:
    deep = "list[" * 60 + "int" + "]" * 60
    schema, unresolved = schema_of(deep)
    assert len(unresolved) == 1  # the innermost part is given up on, the outer layers stand
    assert schema["type"] == "array"
    wide = "tuple[" + ", ".join(["int"] * 6000) + "]"
    schema, unresolved = schema_of(wide)
    assert unresolved  # the budget ran out part of the way through
    assert len(schema["prefixItems"]) == 6000
    long_reference = "'" + "int | " * 200 + "int'"
    assert schema_of(long_reference)[1] != []


# --------------------------------------------------------- tool parameters


def test_tool_parameter_edge_cases() -> None:
    signatures = agent(fixture("tool_signatures.py"), "signatures")
    # A lone model argument is that model's schema, not an object wrapping it.
    single = tool(signatures, "single_model")
    assert single.parameters_schema == {
        "description": "Search arguments.",
        "properties": {"query": {"type": "string"}, "limit": {"default": 5, "type": "integer"}},
        "required": ["query"],
        "title": "Args",
        "type": "object",
    }
    assert single.return_schema == {
        "$defs": {
            "Hit": {
                "properties": {"title": {"type": "string"}, "score": {"type": "number"}},
                "required": ["title", "score"],
                "title": "Hit",
                "type": "object",
            }
        },
        "items": {"$ref": "#/$defs/Hit"},
        "type": "array",
    }
    untyped = tool(signatures, "untyped")
    assert untyped.parameters_schema == {
        "additionalProperties": True,  # **extra
        "properties": {
            "x": {},
            "y": {"default": 3},
            "rest": {"items": {}, "type": "array"},  # *rest
            "flag": {"default": False, "type": "boolean"},
        },
        "required": ["x", "rest"],
        "type": "object",
    }
    assert untyped.return_schema == {}
    assert tool(signatures, "nothing").return_schema == {"type": "null"}
    nested = tool(signatures, "nested").parameters_schema
    assert isinstance(nested, dict)
    assert nested["properties"] == {
        "hits": {"description": "The hits.", "items": {"$ref": "#/$defs/Hit"}, "type": "array"},
        "best": {"anyOf": [{"$ref": "#/$defs/Hit"}, {"type": "null"}], "default": None, "description": "The best one."},
        "mode": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    }
    assert signatures.warnings == []


def test_unresolved_parameter_types_are_marked_and_warned_about(tmp_path: Path) -> None:
    blueprint = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent, RunContext
        from database import Connection, Query

        agent = Agent("test")

        @agent.tool
        def run_query(ctx: RunContext[Connection], query: Query, limit: int = make_limit()) -> Query:
            '''Run a query.'''
        """,
    )
    (run_query,) = blueprint.tools
    assert run_query.parameters_schema == {
        "additionalProperties": False,
        "properties": {"query": {"x-python-type": "Query"}, "limit": {"type": "integer"}},
        "required": ["query"],
        "type": "object",
    }
    assert run_query.return_schema is None
    assert blueprint.warnings == [
        "the parameters of the tool `run_query` are incomplete: `Query` could not be resolved from the analysed source"
    ]


def test_an_output_type_from_outside_the_file_has_no_schema(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic import BaseModel
        from pydantic_ai import Agent
        from models import Report, Section

        class Local(BaseModel):
            section: Section
            pages: int

        whole = Agent("test", output_type=Report)
        partial = Agent("test", output_type=Local)
        computed = Agent("test", output_type=make_type())
        """,
    )
    whole = agent(found, "whole")
    assert (whole.output.mode, whole.output.type_name, whole.output.json_schema) == ("auto", "Report", None)
    assert whole.warnings == [
        "the output type `Report` is defined outside the analysed source, so its schema is not shown"
    ]
    partial = agent(found, "partial")
    assert partial.output.json_schema == {
        "properties": {"section": {"x-python-type": "Section"}, "pages": {"type": "integer"}},
        "required": ["section", "pages"],
        "title": "Local",
        "type": "object",
    }
    assert partial.warnings == [
        "the output schema is incomplete: `Section` could not be resolved from the analysed source"
    ]
    computed = agent(found, "computed")
    assert (computed.output.type_name, computed.output.json_schema) == ("make_type()", None)


def test_output_markers_set_the_mode() -> None:
    found = fixture("outputs.py")
    rows = {b.label: (b.output.mode, b.output.allows_text, b.output.type_name) for b in found.agents}
    assert rows == {
        "plain": ("text", True, "str"),
        "text": ("text", True, "str"),
        "number": ("auto", None, "int"),
        "person": ("auto", None, "Person"),
        "employee": ("auto", None, "Employee"),
        "point": ("auto", None, "Point"),
        "movie": ("auto", None, "Movie"),
        "people": ("auto", None, "list[Person]"),
        "union": ("auto", None, "Person | Point"),
        "alias": ("auto", None, "Person | Point"),  # the alias Answer is shown as what it stands for
        "score": ("auto", None, "int | float"),
        "several": ("auto", None, "Person | Point"),
        "tool": ("tool", None, "Person"),
        "native": ("native", None, "Point"),
        "prompted": ("prompted", None, "Person | Point"),
        "text_function": ("text", True, "shout"),
        "function": ("auto", None, "as_point"),
        "mixed": ("tool", None, "Person | Point | as_point"),
        "choice": ("auto", None, 'Literal["yes", "no"]'),
    }
    assert all(blueprint.output.tool_names == [] for blueprint in found.agents)  # never guessed
    assert all(blueprint.warnings == [] for blueprint in found.agents)
    assert agent(found, "choice").output.json_schema == {"enum": ["yes", "no"], "type": "string"}
    assert agent(found, "function").output.json_schema is None
    employee = agent(found, "employee").output.json_schema
    assert isinstance(employee, dict)
    assert employee["$ref"] == "#/$defs/Employee"  # it refers to itself through `manager`
    assert agent(found, "person").output.location is not None


# ---------------------------------------------------------------- docstrings


def test_docstring_styles_are_recognised() -> None:
    assert infer_style("Summary.\n\nArgs:\n    x: The x.\n") == "google"
    assert infer_style("Summary.\n\nParameters\n----------\nx : int\n    The x.\n") == "numpy"
    assert infer_style("Summary.\n\n:param x: The x.\n:returns: Something.\n") == "sphinx"
    assert infer_style("Just a sentence.") == "google"


def test_descriptions_from_each_docstring_style() -> None:
    google = """Fetch a page.

    Longer explanation
    over two lines.

    Args:
        url: Where to fetch from.
        timeout (float): Seconds to wait,
            at most.
        **options: Passed on.

    Raises:
        ValueError: Never mind.
    """
    assert describe(google, has_return_annotation=True) == (
        "Fetch a page.\n\nLonger explanation\nover two lines.",
        {"url": "Where to fetch from.", "timeout": "Seconds to wait,\nat most.", "options": "Passed on."},
    )
    numpy = """Fetch a page.

    Parameters
    ----------
    url : str
        Where to fetch from.
    timeout : float, optional
        Seconds to wait.
    """
    assert describe(numpy, has_return_annotation=True) == (
        "Fetch a page.",
        {"url": "Where to fetch from.", "timeout": "Seconds to wait."},
    )
    sphinx = """Fetch a page.

    :param url: Where to fetch from.
    :param timeout: Seconds to wait.
    :raises ValueError: Never mind.
    """
    assert describe(sphinx, has_return_annotation=True) == (
        "Fetch a page.",
        {"url": "Where to fetch from.", "timeout": "Seconds to wait."},
    )


def test_descriptions_of_odd_docstrings() -> None:
    assert describe(None, has_return_annotation=False) == (None, {})
    assert describe("", has_return_annotation=False) == (None, {})
    assert describe("One line.", has_return_annotation=False) == ("One line.", {})
    assert describe("   \n   Indented summary.\n   ", has_return_annotation=False) == ("Indented summary.", {})
    only_arguments = "\n    Args:\n        x: The x.\n    "
    assert describe(only_arguments, has_return_annotation=False) == (None, {"x": "The x."})
    # A section header on the opening line loses its indentation when the docstring is cleaned, so
    # griffe sees no section there. Pydantic AI sends the model this very text.
    assert describe("Args:\n    x: The x.\n", has_return_annotation=False) == ("Args:\nx: The x.", {})
    returns = "Do it.\n\nReturns:\n    int: The count.\n"
    assert describe(returns, has_return_annotation=True) == (
        "<summary>Do it.</summary>\n<returns>\n<description>int: The count.</description>\n</returns>",
        {},
    )
    assert describe(returns, has_return_annotation=False) == (
        "<summary>Do it.</summary>\n<returns>\n<type>int</type>\n<description>The count.</description>\n</returns>",
        {},
    )


def test_a_docstring_is_still_described_when_griffe_is_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai_viz._analyze import _docstring

    def broken(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(_docstring, "import_module", broken)
    assert describe("Fetch a page.\n\n    Args:\n        url: Where.\n", has_return_annotation=True) == (
        "Fetch a page.\n\nArgs:\n    url: Where.",
        {},
    )
