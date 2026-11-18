"""Every way tools and toolsets reach an agent. Not runnable: some of it is deliberately unknowable."""

from external_tools import fetch_page  # outside the analysed source
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.toolsets import CombinedToolset, FunctionToolset, PrefixedToolset
from vendor_toolsets import VendorToolset, build_toolset  # outside the analysed source

kb = FunctionToolset(id="kb")
math = FunctionToolset()


@kb.tool_plain
def search_kb(query: str) -> list[str]:
    """Search the knowledge base.

    Args:
        query: Keywords.
    """
    return []


@kb.tool(retries=2)
async def read_article(ctx: RunContext[None], article_id: str) -> str:
    """Read one article."""
    return ""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(ctx: RunContext[None], a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def divide(a: float, b: float) -> float:
    """Divide."""
    return a / b


math.add_function(add)
math.add_function(multiply, retries=5)
math.add_tool(Tool(divide, name="div", description="Divide a by b."))

constructed = FunctionToolset([add, Tool(multiply, takes_ctx=True, max_retries=1)], id="constructed")

files = MCPServerStdio("uvx", args=["mcp-server-files"], id="files")
COMMON = [add, divide]


def by_hand(text: str) -> str:
    """Registered through the tools argument."""
    return text


def needs_ctx(ctx: RunContext[None], n: int) -> int:
    """Takes the run context, which is read from the annotation."""
    return n


def per_run(ctx: RunContext[None]):
    return math


worker = Agent(
    "test",
    name="worker",
    tools=[by_hand, needs_ctx, Tool(divide, name="ratio"), fetch_page, *COMMON, make_tool()],  # noqa: F821
    toolsets=[
        kb,
        math.prefixed("math"),
        constructed.filtered(lambda ctx, tool: True).prefixed("c"),
        PrefixedToolset(kb, prefix="again"),
        files,
        MCPServerStdio("npx", args=["some-server"]).prefixed("remote"),
        VendorToolset(region="eu"),
        build_toolset("extra"),
        per_run,
        CombinedToolset([math, files]),
    ],
)


@worker.tool_plain
def own_tool(x: int) -> int:
    """Registered with a decorator, after the tools argument."""
    return x


@worker.toolset
def built_per_run(ctx: RunContext[None]):
    return kb


unknown_lists = Agent("test", name="unknown_lists", tools=load_tools(), toolsets=load_toolsets())  # noqa: F821
