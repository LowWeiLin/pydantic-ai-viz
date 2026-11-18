"""Agents that run other agents from inside their tools. Runnable, for the parity test."""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

lead = Agent(TestModel(), name="lead")


@lead.tool
async def ask_writer(ctx: RunContext[None], brief: str) -> str:
    """Defined before the agent it calls: the name is looked up when the tool runs."""
    result = await writer.run(brief, usage=ctx.usage)
    return result.output


writer = Agent(TestModel(), name="writer")
reviewer = Agent(TestModel(), name="reviewer")
checker = reviewer  # an alias for the same agent
silent = Agent(TestModel(), name="silent")


def run_and_unwrap(agent: Agent[None, str], prompt: str) -> str:
    """A helper that runs whichever agent it is given."""
    return agent.run_sync(prompt).output


@lead.tool_plain
def ask_reviewer(text: str) -> str:
    """Runs the reviewer through its alias."""
    return checker.run_sync(text).output


@lead.tool_plain
def ask_through_helper(text: str) -> str:
    """Hands an agent to a helper instead of calling it."""
    return run_and_unwrap(writer, text)


@lead.tool_plain
async def ask_both(text: str) -> str:
    """Two agents from one tool, one of them streamed."""
    first = await writer.run(text)
    async with reviewer.run_stream(first.output) as stream:
        return await stream.get_output()


@lead.tool_plain
def try_again(text: str) -> str:
    """A tool that runs its own agent."""
    return lead.run_sync(text).output


@lead.tool_plain
def just_mentions(text: str) -> str:
    """Reads an attribute of an agent without running it: not a delegation."""
    return f"{silent.name}: {text}"


@writer.tool_plain
async def iterate(text: str) -> str:
    """Delegates with ``iter``."""
    async with reviewer.iter(text) as run:
        async for _ in run:
            pass
    return "done"
