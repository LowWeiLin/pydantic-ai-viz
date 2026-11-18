"""Agents that are not plain module-level assignments, and names that only look like agents."""

import pydantic_ai as pai
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import RunContext
from pydantic_ai.agent import Agent


def build_agent():
    made = Agent("test", instructions="Built in a factory.")

    @made.tool_plain
    def inside(x: int) -> int:
        """A tool registered inside the factory."""
        return x

    return made


from_factory = build_agent()


@from_factory.tool_plain
def outside(y: str) -> str:
    """Registered on the factory's result, outside the factory."""
    return y


def direct():
    return PydanticAgent("test", name="direct")


async def async_factory():
    return Agent("test", name="made_async")


class Team:
    """Agents as attributes."""

    shared = pai.Agent("test", name="shared")

    def __init__(self) -> None:
        self.planner = Agent[None, str]("test", name="planner")
        self.helper = Agent("test")
        self.planner.tool_plain(self.estimate)

        @self.planner.tool
        async def delegate(ctx: RunContext[None], task: str) -> str:
            """Hands the task to the helper."""
            return (await self.helper.run(task)).output

    def estimate(self, task: str) -> int:
        """A method registered as a tool: ``self`` is not a parameter of the tool."""
        return len(task)

    async def ask_shared(self, question: str) -> str:
        return (await self.shared.run(question)).output


REGISTRY = {"first": Agent("test", name="first"), "second": Agent("test")}

if __name__ == "__main__":
    in_main = Agent("test", name="in_main")

try:
    guarded = Agent("test", name="guarded")
except Exception:
    guarded = None

reassigned = Agent("test", name="one")


@reassigned.tool_plain
def for_one() -> str:
    """Belongs to the first agent called reassigned."""
    return "one"


reassigned = Agent("test", name="two")


@reassigned.tool_plain
def for_two() -> str:
    """Belongs to the second agent called reassigned."""
    return "two"


# ---- not agents ------------------------------------------------------------


class Agent:  # noqa: F811  (shadows the import from here on)
    """A class of this file that happens to be called Agent."""

    def __init__(self, *args, **kwargs) -> None:
        pass


lookalike = Agent("test", name="lookalike")


def takes_a_class(Agent):
    return Agent("test", name="from_parameter")
