"""Tool signatures that are easy to get wrong. Runnable, for the parity test."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional, TypedDict, Union
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel


class Args(BaseModel):
    """Search arguments."""

    query: str
    limit: int = 5


class Hit(BaseModel):
    title: str
    score: float


class Colour(str, Enum):
    RED = "red"
    BLUE = "blue"


class Window(TypedDict):
    start: date
    end: date


signatures = Agent(TestModel(), name="signatures")


@signatures.tool_plain
def nothing(x: int) -> None:
    """Does nothing."""


@signatures.tool_plain
def single_model(args: Args) -> list[Hit]:
    """A lone model argument is sent as that model."""
    return []


@signatures.tool_plain
def untyped(x, y=3, *rest, flag: bool = False, **extra):
    """No annotations at all."""


@signatures.tool
def nested(
    ctx: RunContext[None], hits: list[Hit], best: Hit | None = None, mode: "str | None" = None
) -> dict[str, Hit]:
    """Nested models.

    Args:
        hits: The hits.
        best: The best one.
    """
    return {}


@signatures.tool_plain
def containers(
    tags: set[str],
    pair: tuple[int, str],
    many: tuple[float, ...],
    lookup: dict[str, list[int]],
    maybe: Optional[int] = None,
    either: Union[int, str] = 0,
    anything: Any = None,
) -> list[dict[str, Any]]:
    """Containers and unions."""
    return []


@signatures.tool_plain
def scalars(
    when: datetime,
    day: date,
    ident: UUID,
    colour: Colour,
    window: Window,
    size: Literal["s", "m", "l"] = "m",
    only: Literal["fixed"] = "fixed",
    ratio: float = 0.5,
    label: str = "none",
    blob: bytes = b"",
) -> bool:
    """Scalars with formats, an enum, a TypedDict and literals."""
    return True
