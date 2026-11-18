"""Output types in every shape. Runnable, for the parity test."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput, PromptedOutput, TextOutput, ToolOutput
from pydantic_ai.models.test import TestModel
from typing_extensions import NotRequired


class Priority(str, Enum):
    LOW = "low"
    HIGH = "high"


class Address(BaseModel):
    """Where to send it."""

    street: str
    city: str = Field(description="City name.", min_length=1, max_length=80)
    postcode: Optional[str] = None


class Person(BaseModel):
    name: str
    age: int = Field(ge=0, le=150)
    emails: list[str] = Field(default_factory=list, max_length=5)
    address: Address
    previous: list[Address] = []
    priority: Priority = Priority.LOW
    nickname: Annotated[str | None, Field(description="What friends say.")] = None
    kind: Literal["customer"] = "customer"
    _secret: str = "hidden"


class Employee(Person):
    """A person with a job."""

    title: str = Field(alias="jobTitle")
    manager: Employee | None = None


@dataclass
class Point:
    """A place on the map."""

    x: float
    y: float
    label: str = "origin"
    tags: list[str] = field(default_factory=list)


class Movie(TypedDict):
    title: str
    year: int
    rating: NotRequired[float]


Score = int | float
Answer = Person | Point


def shout(text: str) -> str:
    return text.upper()


def as_point(x: float, y: float) -> Point:
    """An output function."""
    return Point(x, y)


model = TestModel()

plain = Agent(model, name="plain")
text = Agent(model, name="text", output_type=str)
number = Agent(model, name="number", output_type=int)
person = Agent(model, name="person", output_type=Person)
employee = Agent(model, name="employee", output_type=Employee)
point = Agent(model, name="point", output_type=Point)
movie = Agent(model, name="movie", output_type=Movie)
people = Agent(model, name="people", output_type=list[Person])
union = Agent(model, name="union", output_type=Person | Point)  # type: ignore[arg-type]
alias = Agent(model, name="alias", output_type=Answer)  # type: ignore[arg-type]
score = Agent(model, name="score", output_type=Score)  # type: ignore[arg-type]
several = Agent(model, name="several", output_type=[Person, Point])
tool = Agent(model, name="tool", output_type=ToolOutput(Person, name="give_person"))
native = Agent(model, name="native", output_type=NativeOutput(Point))
prompted = Agent(model, name="prompted", output_type=PromptedOutput([Person, Point]))
text_function = Agent(model, name="text_function", output_type=TextOutput(shout))
function = Agent(model, name="function", output_type=as_point)
mixed = Agent(model, name="mixed", output_type=[ToolOutput(Person, name="give_person"), Point, as_point])
choice = Agent(model, name="choice", output_type=Literal["yes", "no"])  # type: ignore[arg-type]
