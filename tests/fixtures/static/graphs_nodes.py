"""``BaseNode`` classes in builder graphs, and edges that come from return types. Runnable, for the parity test."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Union

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext, StepNode, reduce_list_append


@dataclass
class A(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext[None, None]) -> B | C:
        return B()


@dataclass
class B(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext[None, None]) -> "C":
        return C()


@dataclass
class C(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext[None, None]) -> Union[A, End[int]]:
        return End(1)


b1 = GraphBuilder(name="reach", output_type=int)


@b1.step
async def first(ctx: StepContext[None, None, None]) -> A:
    return A()


b1.add(b1.edge_from(b1.start_node).to(first))
b1.add_reachable(A)
reach = b1.build()

b2 = GraphBuilder(name="auto", output_type=int)


@b2.step
async def begin(ctx: StepContext[None, None, None]) -> A | End[int]:
    return A()


b2.add(b2.edge_from(b2.start_node).to(begin), b2.node(A), b2.node(B), b2.node(C))
auto = b2.build()

b3 = GraphBuilder(name="matchnode", output_type=int)


@b3.step
async def pick(ctx: StepContext[None, None, None]) -> A | B:
    return A()


b3.add(
    b3.edge_from(b3.start_node).to(pick),
    b3.edge_from(pick).to(b3.decision().branch(b3.match_node(A)).branch(b3.match_node(B))),
    b3.node(A),
    b3.node(B),
    b3.node(C),
)
matchnode = b3.build()

b4 = GraphBuilder(name="mixed", output_type=int)


@b4.step
async def tail(ctx: StepContext[None, None, object]) -> int:
    return 1


@dataclass
class Loop(BaseNode[None, None, int]):
    async def run(self, ctx: GraphRunContext[None, None]) -> Loop | Annotated[StepNode[None, None], tail]:
        return Loop()


b4.add(
    b4.edge_from(b4.start_node).to(Loop),
    b4.node(Loop),
    b4.edge_from(tail).to(b4.end_node),
)
mixed = b4.build()

b5 = GraphBuilder(name="roots", output_type=int)
b5.add_edge(b5.start_node, A)
b5.add_reachable(C, A)
roots = b5.build()

# ---- a node that starts two paths runs both: a fork goes in between ------------

b6 = GraphBuilder(name="multi", output_type=list[int])


@b6.step
async def origin(ctx: StepContext[None, None, None]) -> int:
    return 1


@b6.step
async def left(ctx: StepContext[None, None, int]) -> int:
    return 1


@b6.step
async def right(ctx: StepContext[None, None, int]) -> int:
    return 2


both = b6.join(reduce_list_append, initial_factory=list[int])
b6.add(b6.edge_from(b6.start_node).to(origin))
b6.add(b6.edge_from(origin).to(left))
b6.add(b6.edge_from(origin).label("r").to(right))
b6.add(b6.edge_from(left, right).to(both), b6.edge_from(both).to(b6.end_node))
multi = b6.build()
