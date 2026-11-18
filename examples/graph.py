"""Watch a pydantic-graph run node by node. Offline. Run it:  uv run python examples/graph.py"""

import asyncio

from pydantic_graph import GraphBuilder, StepContext

import pydantic_ai_viz as pviz

builder = GraphBuilder(name="headline", input_type=str, output_type=str)


@builder.step(label="Title case")
async def title_case(ctx: StepContext[None, None, str]) -> str:
    return ctx.inputs.title()


@builder.step(label="Add byline")
async def add_byline(ctx: StepContext[None, None, str]) -> str:
    return f"{ctx.inputs} - by the graph desk"


builder.add_edge(builder.start_node, title_case)
builder.add_edge(title_case, add_byline)
builder.add_edge(add_byline, builder.end_node)
graph = builder.build()
if __name__ == "__main__":
    session = pviz.serve(graph, open_browser=True)
    print(asyncio.run(pviz.run_graph(graph, inputs="graphs you can watch")), f"(see {session.url}, Ctrl+C to stop)")
    session.wait()
