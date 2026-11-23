"""Operations demo: an on-call agent that delegates, is watched by hooks and waits for approval.

The checkout service is failing. The `oncall` agent pages the team, hands the log reading to
its `log_reader` sub-agent and asks to restart the two services the logs point at. Restarting
needs a human's approval, so the first run ends waiting for it. The demo then answers as the
engineer would: the checkout service may be restarted, the database may not. The second run
picks the conversation up, restarts checkout and writes the incident report, which an output
validator checks before the run ends.

Hooks guard the restart tool and count model responses, a small `Pager` capability emits an
`oncall.paged` event and `CaptureEvents` puts that event on the run. Everything is scripted
and offline: the "models" are `FunctionModel`s and the logs and services are dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import (
    Agent,
    CapabilityEvent,
    DeferredToolRequests,
    DeferredToolResults,
    ModelRetry,
    RunContext,
    ToolDenied,
)
from pydantic_ai.capabilities import AbstractCapability, Hooks
from pydantic_ai_harness import SubAgent, SubAgents

from pydantic_ai_viz import CaptureEvents

from ._base import Demo
from ._script import History, Turn, call, pause, playback, scripted_model

ALERT = "Alert: checkout error rate is 38% and rising. Find the cause and get it back up."

_LOGS: dict[str, list[str]] = {
    "checkout": [
        "09:41:02 ERROR pool exhausted: 50/50 connections in use, waiting for payments-db",
        "09:41:05 ERROR request timed out after 30 s: POST /orders",
    ],
    "payments-db": [
        "09:40:58 WARN slow query: 12.4 s on orders_by_customer",
        "09:41:00 WARN replication lag 4 s",
    ],
}

_RUNNING: dict[str, str] = {"checkout": "degraded", "payments-db": "degraded"}

_REPORT = (
    "Incident: checkout errors at 38% from 09:41.\n"
    "Cause: checkout ran out of database connections while payments-db answered slowly.\n"
    "Action: checkout restarted and healthy; payments-db left running, restart refused outside the "
    "maintenance window.\n"
    "Follow-up: look into the slow orders_by_customer query."
)


@dataclass(kw_only=True)
class Paged(CapabilityEvent, namespace="oncall"):
    """The on-call team was paged about an alert."""

    team: str
    alert: str


@dataclass
class Pager(AbstractCapability[Any]):
    """Pages the on-call team when a run starts with a new alert."""

    team: str = "platform"

    async def before_run(self, ctx: RunContext[Any]) -> None:
        if isinstance(ctx.prompt, str):
            await ctx.emit(Paged(team=self.team, alert=ctx.prompt))


hooks = Hooks()


@hooks.on.before_tool_execute(tools=["restart_service"])
async def guard_restart(ctx: RunContext[Any], *, call: Any, tool_def: Any, args: Any) -> Any:
    """Only restart services the team runs."""
    if args["name"] not in _RUNNING:
        raise ModelRetry(f"There is no service called {args['name']!r}.")
    return args


@hooks.on.after_model_request
async def log_response(ctx: RunContext[Any], *, request_context: Any, response: Any) -> Any:
    """Pass each model response on unchanged; a team would log it here."""
    return response


# --- log reader sub-agent -----------------------------------------------------------------


def _log_summary(history: History) -> Turn:
    lines = [line for result in history.results("read_logs") for line in result]
    return Turn(text="Findings:\n" + "\n".join(f"- {line}" for line in lines))


log_reader = Agent(
    scripted_model(
        "scripted-operations",
        Turn(calls=(call("read_logs", service="checkout"), call("read_logs", service="payments-db"))),
        _log_summary,
    ),
    name="log_reader",
    description="Reads service logs and reports what went wrong",
    instructions="You read logs. Quote the lines that explain the failure, nothing else.",
)


@log_reader.tool_plain
async def read_logs(service: str) -> list[str]:
    """Read the latest log lines of a service.

    Args:
        service: The service, for example 'checkout'.
    """
    await pause(400)
    return _LOGS.get(service, [])


# --- on-call agent ------------------------------------------------------------------------

oncall = Agent(
    scripted_model(
        "scripted-operations",
        Turn(
            text="Paged the team. The logs will say where to look.",
            calls=(
                call(
                    "delegate_task",
                    agent_name="log-reader",
                    task="Read the logs of checkout and payments-db and quote the lines that explain the errors.",
                ),
            ),
        ),
        Turn(
            text="Checkout is out of connections because payments-db is slow. Asking to restart both.",
            calls=(call("restart_service", name="checkout"), call("restart_service", name="payments-db")),
        ),
        Turn(text=_REPORT),
    ),
    name="oncall",
    description="Handles alerts for the platform team",
    instructions="You are on call. Find the cause before you act, and say what you did and why.",
    output_type=[str, DeferredToolRequests],
    capabilities=[
        SubAgents(agents=[SubAgent(log_reader, name="log-reader", description="Reads service logs")]),
        hooks,
        Pager(id="pager"),
        CaptureEvents(),
    ],
)


@oncall.tool(requires_approval=True)
async def restart_service(ctx: RunContext[Any], name: str) -> str:
    """Restart a service. A human approves every restart.

    Args:
        name: The service to restart.
    """
    await pause(900)
    _RUNNING[name] = "healthy"
    return f"{name} restarted and healthy"


@oncall.output_validator
def check_report(ctx: RunContext[Any], output: str | DeferredToolRequests) -> str | DeferredToolRequests:
    """An incident report names its cause and what was done."""
    if isinstance(output, str) and not ("Cause:" in output and "Action:" in output):
        raise ModelRetry("Write the report with a 'Cause:' and an 'Action:' line.")
    return output


def _answers(requests: DeferredToolRequests) -> DeferredToolResults:
    """The engineer's answers: restart checkout, leave the database alone."""
    results = DeferredToolResults()
    for request in requests.approvals:
        approved = request.args_as_dict()["name"] == "checkout"
        results.approvals[request.tool_call_id] = (
            True if approved else ToolDenied("Not the database: restart it only in the maintenance window.")
        )
    return results


async def run(speed: float = 1.0) -> str:
    """Play the scenario once: the alert, the wait for approval and the resumed run. Return the report."""
    with playback(speed):
        _RUNNING.update({"checkout": "degraded", "payments-db": "degraded"})
        first = await oncall.run(ALERT)
        assert isinstance(first.output, DeferredToolRequests)
        await pause(1500)
        second = await oncall.run(message_history=first.all_messages(), deferred_tool_results=_answers(first.output))
    assert isinstance(second.output, str)
    return second.output


demo = Demo(
    name="operations",
    title="On-call operations",
    description="An on-call agent delegates log reading, waits for approval to restart and resumes once answered.",
    targets=(oncall, log_reader),
    run=run,
)
