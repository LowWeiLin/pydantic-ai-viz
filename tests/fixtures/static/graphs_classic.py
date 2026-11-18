"""The older way of writing a graph: ``Graph(nodes=[...])`` over ``BaseNode`` classes.

The pydantic-graph this project is tested with no longer runs this file. The
analyser reads it all the same: node classes, and edges from what ``run`` returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Union

from elsewhere import Archive
from pydantic_graph import BaseNode, Edge, End, Graph, GraphRunContext


@dataclass
class QuizState:
    question: str | None = None
    attempts: int = 0
    history: list[str] = field(default_factory=list)


@dataclass
class Ask(BaseNode[QuizState]):
    """Think of a question."""

    async def run(self, ctx: GraphRunContext[QuizState]) -> Answer:
        ctx.state.question = "What is the capital of France?"
        return Answer(ctx.state.question)


@dataclass
class Answer(BaseNode[QuizState]):
    question: str

    async def run(self, ctx: GraphRunContext[QuizState]) -> Evaluate:
        return Evaluate(input(f"{self.question} "))


@dataclass
class Evaluate(BaseNode[QuizState, None, str]):
    answer: str

    async def run(
        self, ctx: GraphRunContext[QuizState]
    ) -> Annotated[End[str], Edge(label="correct")] | Annotated[Reprimand, Edge(label="wrong")]:
        if self.answer == "Paris":
            return End("Well done.")
        return Reprimand("Try again.")


@dataclass
class Reprimand(BaseNode[QuizState]):
    comment: str

    async def run(self, ctx: GraphRunContext[QuizState]) -> "Ask":
        ctx.state.attempts += 1
        return Ask()


quiz = Graph(nodes=(Ask, Answer, Evaluate, Reprimand), state_type=QuizState, run_end_type=str, name="quiz")


async def play() -> str:
    result = await quiz.run(Ask(), state=QuizState())
    return result.output


# ---- no run in sight, a node from elsewhere, and a node that does not say where it goes


@dataclass
class Fetch(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext) -> Union[Parse, Archive, End[int]]:
        return Parse()


@dataclass
class Parse(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext) -> Fetch | Retry | int:
        return Fetch()


@dataclass
class Retry(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext):
        return Fetch()


NODES = [Fetch, Parse, Retry, Archive]
crawler = Graph(nodes=NODES)

# ---- nodes that are worked out when the code runs

computed = Graph(nodes=discover_nodes(), name="computed")
