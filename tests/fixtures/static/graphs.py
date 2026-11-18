"""Graphs written out with the builder API. Runnable, for the parity test."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import GraphBuilder, StepContext, reduce_list_append


@dataclass
class Draft:
    text: str = ""
    revision: int = 0


# ---- a review loop -----------------------------------------------------------

pipeline_builder = GraphBuilder(name="pipeline", state_type=Draft, input_type=str, output_type=str)


@pipeline_builder.step(label="Write draft")
async def write(ctx: StepContext[Draft, None, str | bool]) -> str:
    return "draft"


@pipeline_builder.step(label="Review")
async def review(ctx: StepContext[Draft, None, str]) -> bool:
    return True


@pipeline_builder.step
async def publish(ctx: StepContext[Draft, None, bool]) -> str:
    return ctx.state.text


def _approved(verdict: bool) -> bool:
    return verdict


pipeline_builder.add(
    pipeline_builder.edge_from(pipeline_builder.start_node).to(write),
    pipeline_builder.edge_from(write).to(review),
    pipeline_builder.edge_from(review).to(
        pipeline_builder.decision(note="approved?")
        .branch(pipeline_builder.match(bool, matches=_approved).label("approved").to(publish))
        .branch(pipeline_builder.match(bool).label("needs work").to(write))
    ),
    pipeline_builder.edge_from(publish).to(pipeline_builder.end_node),
)

pipeline = pipeline_builder.build()


# ---- map, broadcast and join -------------------------------------------------

fan = GraphBuilder(input_type=list[int], output_type=list[int])


@fan.step
async def source(ctx: StepContext[None, None, list[int]]) -> list[int]:
    return ctx.inputs


@fan.step
async def square(ctx: StepContext[None, None, int]) -> int:
    return ctx.inputs**2


@fan.step
async def negate(ctx: StepContext[None, None, int]) -> int:
    return -ctx.inputs


collect = fan.join(reduce_list_append, initial_factory=list[int])
fan.add(
    fan.edge_from(fan.start_node).to(source),
    fan.edge_from(source).map().label("each").to(square, negate),
    fan.edge_from(square, negate).to(collect),
    fan.edge_from(collect).to(fan.end_node),
)
fan_out = fan.build()


# ---- one edge at a time, with ids of our own ---------------------------------

wiring = GraphBuilder(name="wiring", input_type=int, output_type=str)


async def _halve(ctx: StepContext[None, None, int]) -> int:
    return ctx.inputs // 2


async def _describe_small(ctx: StepContext[None, None, int]) -> str:
    return "small"


async def _describe_large(ctx: StepContext[None, None, int]) -> str:
    return "large"


halve = wiring.step(_halve, node_id="halve", label="Halve it")
small = wiring.step(_describe_small, label="Small")
large = wiring.step(_describe_large)

size = (
    wiring.decision(note="how big?", node_id="size")
    .branch(wiring.match(int, matches=lambda value: value > 10).label("big").to(large))
    .branch(wiring.match(int).to(small))
)

wiring.add_edge(wiring.start_node, halve, label="in")
wiring.add_edge(halve, size)
wiring.add_edge(small, wiring.end_node)
wiring.add_edge(large, wiring.end_node, label="out")
wired = wiring.build()
