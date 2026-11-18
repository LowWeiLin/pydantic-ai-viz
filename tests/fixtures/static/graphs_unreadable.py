"""Graphs that are assembled in ways the source does not spell out."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext


@dataclass
class State:
    n: int = 0


# ---- edges added in a loop ---------------------------------------------------

looped_builder = GraphBuilder(name="looped", input_type=int, output_type=int)


@looped_builder.step
async def first(ctx: StepContext[None, None, int]) -> int:
    return ctx.inputs


@looped_builder.step(label="Second step")
async def second(ctx: StepContext[None, None, int]) -> int:
    return ctx.inputs


looped_builder.add(looped_builder.edge_from(looped_builder.start_node).to(first))
for before, after in [(first, second), (second, looped_builder.end_node)]:
    looped_builder.add(looped_builder.edge_from(before).to(after))
looped = looped_builder.build()


# ---- BaseNode classes: edges come from return annotations ---------------------


@dataclass
class Increment(BaseNode[State, None, int]):
    async def run(self, ctx: GraphRunContext[State, None]) -> Check:
        ctx.state.n += 1
        return Check()


@dataclass
class Check(BaseNode[State, None, int]):
    async def run(self, ctx: GraphRunContext[State, None]) -> Increment | End[int]:
        return End(ctx.state.n) if ctx.state.n >= 3 else Increment()


counter_builder = GraphBuilder(name="counter", state_type=State, output_type=int)
counter_builder.add(
    counter_builder.edge_from(counter_builder.start_node).to(Increment),
    counter_builder.node(Increment),
    counter_builder.node(Check),
)
counter = counter_builder.build()


# ---- paths built by a helper, and steps nothing connects -----------------------


def make_paths(builder):
    return []


helped = GraphBuilder(name="helped")


@helped.step
async def alone(ctx: StepContext[None, None, None]) -> None:
    return None


@helped.step
async def also_alone(ctx: StepContext[None, None, None]) -> None:
    return None


helped.add(*make_paths(helped))
