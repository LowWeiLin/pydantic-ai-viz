"""Live graph runs: ``run_graph`` (spec section 4.5).

pydantic-graph reports its runs through Logfire only, and has no tracer provider
to hand ours to. ``graph.iter()`` is public, though, and yields each batch of
tasks just before those tasks execute. So ``run_graph`` drives the iteration
itself and opens spans from the session's own tracer: one for the run, one per
task. The spans are ordinary OpenTelemetry spans marked with ``pydantic_ai_viz.*``
attributes, and take the same path into the session as agent spans.
"""

from __future__ import annotations

import asyncio
from contextvars import Token
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode
from pydantic_core import to_json
from pydantic_graph import Graph, GraphTask, Join

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


def _stack(task: GraphTask) -> tuple[Any, ...]:
    return tuple(getattr(task, "fork_stack", ()))


def _is_source(task: GraphTask, stack: tuple[Any, ...]) -> bool:
    """Whether ``task`` can be what produced new tasks that run under the fork stack ``stack``.

    A step, a decision or the start node hands its own stack on unchanged. A fork (a
    map or a broadcast) appends one entry that names it.
    """
    own = _stack(task)
    if own == stack:
        return True
    return bool(stack) and own == stack[:-1] and getattr(stack[-1], "fork_id", None) == task.node_id


class _OpenNodes:
    """The node spans of the tasks of one graph run that have not been seen to finish.

    ``graph.iter()`` yields a batch each time *one* task finishes: the tasks that
    follow from it. It does not say which task that was, so it is worked out from the
    fork stacks, and a span is ended only when exactly one open task fits.
    """

    def __init__(self) -> None:
        self._open: dict[Any, tuple[GraphTask, Span]] = {}
        self.exact = True
        """False once a span was ended without having seen what followed from its task."""

    def add(self, task: GraphTask, span: Span) -> None:
        self._open[task.task_id] = (task, span)

    def spans(self) -> list[Span]:
        return [span for _, span in self._open.values()]

    def finish_source_of(self, batch: list[GraphTask]) -> None:
        """End the span of the task that ``batch`` follows from, if that task can be told."""
        if not batch:
            # A task finished and nothing follows from it (a map over no items, for example).
            if len(self._open) == 1:
                self.end_all()
            return
        stack = _stack(batch[0])
        sources = [task_id for task_id, (task, _) in self._open.items() if _is_source(task, stack)]
        if len(sources) == 1:
            self._open.pop(sources[0])[1].end()
        # The branch that owns ``stack`` has moved on, so whatever it forked earlier is over, seen
        # or not: a join that cancels its remaining siblings does so without a batch of its own.
        for task_id, (task, span) in list(self._open.items()):
            own = _stack(task)
            if len(own) > len(stack) and own[: len(stack)] == stack:
                del self._open[task_id]
                span.end()
                self.exact = False

    def end_all(self) -> None:
        for _, span in self._open.values():
            span.end()
        self._open.clear()


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

    Known limits, while several nodes run in parallel (after a broadcast or a map): an agent run
    started by one of the nodes of a batch is shown under the graph run, not under its
    node; and a failure is shown on the graph run only, because the graph does not
    say which of the running nodes raised.

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
        nodes = _OpenNodes()
        token: Token[Context] | None = None
        try:
            async with graph.iter(state=state, deps=deps, inputs=inputs, infer_name=False) as run:
                batches = run.__aiter__()
                while True:
                    try:
                        # Asking for the next item is what starts the tasks of the batch just received.
                        item = await batches.__anext__()
                    except StopAsyncIteration:
                        break
                    finally:
                        if token is not None:
                            otel_context.detach(token)
                            token = None
                    if not isinstance(item, list):
                        nodes.end_all()  # the end marker: the run has its output
                        continue
                    nodes.finish_source_of(item)
                    started: list[Span] = []
                    for task in item:
                        span = tracer.start_span(
                            f"run node {task.node_id}",
                            attributes=attributes("graph_node", task.inputs, **{ATTR_GRAPH_NODE_ID: str(task.node_id)}),
                        )
                        if isinstance(graph.nodes.get(task.node_id), Join):
                            # A join folds the value in on the spot, and no batch follows from it.
                            span.end()
                        else:
                            nodes.add(task, span)
                            started.append(span)
                    if len(item) == 1 and started:
                        # Make the node the current span, so an agent run inside the step nests under it.
                        token = otel_context.attach(trace.set_span_in_context(started[0]))
                output = run.output
        except Exception as exc:
            running = nodes.spans()
            if nodes.exact and len(running) == 1:
                # With one task running we know which node failed; with several we do not.
                running[0].record_exception(exc)
                running[0].set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            raise
        except BaseException as exc:
            # Cancellation or Ctrl+C: not an error to OpenTelemetry, but the run did not finish,
            # and neither did the nodes it was in. No traceback is recorded for it.
            stopped = Status(
                StatusCode.ERROR, "cancelled" if isinstance(exc, asyncio.CancelledError) else type(exc).__name__
            )
            for span in (root, *nodes.spans()):
                span.set_status(stopped)
            raise
        finally:
            nodes.end_all()
        if include_content:
            root.set_attribute(ATTR_GRAPH_OUTPUT, _json(output))
        return output
