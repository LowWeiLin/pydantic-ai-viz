"""``add_reachable``: node classes found by following return types. Runnable where pydantic-graph has it."""

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

b5 = GraphBuilder(name="roots", output_type=int)
b5.add_edge(b5.start_node, A)
b5.add_reachable(C, A)
roots = b5.build()
