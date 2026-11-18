"""Live graph runs: ``run_graph`` (spec section 4.5).

pydantic-graph reports its runs through Logfire only, and has no tracer provider
to hand ours to. ``graph.iter()`` is public, though, and yields each batch of
tasks just before those tasks execute. So ``run_graph`` drives the iteration
itself and opens spans from the session's own tracer: one for the run, one per
task. The spans are ordinary OpenTelemetry spans marked with ``pydantic_ai_viz.*``
attributes, and take the same path into the session as agent spans.
"""

from __future__ import annotations

from contextvars import Token
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode
from pydantic_core import to_json
from pydantic_graph import Graph

from ._normalize import (
    ATTR_GRAPH_ID,
    ATTR_GRAPH_INPUTS,
    ATTR_GRAPH_NAME,
    ATTR_GRAPH_NODE_ID,
    ATTR_GRAPH_OUTPUT,
    ATTR_KIND,
    TRACER_NAME,
)
from ._session import Session, active_session


def _json(value: Any) -> str:
    return to_json(value, fallback=repr).decode()


async def run_graph(
    graph: Graph[Any, Any, Any, Any],
    *,
    state: Any = None,
    deps: Any = None,
    inputs: Any = None,
    session: Session | None = None,
) -> Any:
    """Run a pydantic-graph `Graph` so the viewer can follow it node by node.

    A drop-in for `await graph.run(state=..., deps=..., inputs=...)`. The graph is
    registered with the session if it is not yet. Agent runs started inside a step
    appear under that step's node.

    ```python
    import pydantic_ai_viz as pviz

    with pviz.record(graph) as session:
        output = await pviz.run_graph(graph, inputs="hello")
    ```

    Known limit: when several nodes run in parallel (after a fork), an agent run
    started by one of them is shown under the graph run, not under its node.

    Args:
        graph: The graph to run.
        state: Passed to the graph as its state.
        deps: Passed to the graph as its dependencies.
        inputs: Passed to the graph as the input of its first node.
        session: The session to report to. Defaults to the open session; when there
            is none, the graph simply runs untraced.

    Returns:
        The graph's output.
    """
    target = session if session is not None else active_session()
    if target is None or target.closed:
        # infer_name=False here and below: the name would be inferred from this function's
        # local variable, which is always called "graph".
        return await graph.run(state=state, deps=deps, inputs=inputs, infer_name=False)

    graph_id, graph_name = target._graph_for_run(graph)  # pyright: ignore[reportPrivateUsage]
    tracer = target._tracer_provider.get_tracer(TRACER_NAME)  # pyright: ignore[reportPrivateUsage]
    include_content = target.include_content

    def attributes(kind: str, value: Any, **extra: str) -> dict[str, str]:
        found = {ATTR_KIND: kind, ATTR_GRAPH_ID: graph_id, ATTR_GRAPH_NAME: graph_name, **extra}
        if include_content:
            found[ATTR_GRAPH_INPUTS] = _json(value)
        return found

    # An exception leaving this block marks the run span as failed and is re-raised.
    with tracer.start_as_current_span(f"run graph {graph_name}", attributes=attributes("graph_run", inputs)) as root:
        open_spans: list[Span] = []
        token: Token[Context] | None = None
        async with graph.iter(state=state, deps=deps, inputs=inputs, infer_name=False) as run:
            batches = run.__aiter__()
            while True:
                try:
                    # Asking for the next batch is what executes the tasks of the current one.
                    item = await batches.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    if len(open_spans) == 1:
                        # With one task running we know which node failed; with several we do not.
                        open_spans[0].record_exception(exc)
                        open_spans[0].set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
                    raise
                finally:
                    if token is not None:
                        otel_context.detach(token)
                        token = None
                    for span in open_spans:
                        span.end()
                    open_spans = []
                if isinstance(item, list):
                    open_spans = [
                        tracer.start_span(
                            f"run node {task.node_id}",
                            attributes=attributes("graph_node", task.inputs, **{ATTR_GRAPH_NODE_ID: str(task.node_id)}),
                        )
                        for task in item
                    ]
                    if len(open_spans) == 1:
                        # Make the node the current span, so an agent run inside the step nests under it.
                        token = otel_context.attach(trace.set_span_in_context(open_spans[0]))
            output = run.output
        if include_content:
            root.set_attribute(ATTR_GRAPH_OUTPUT, _json(output))
        return output
