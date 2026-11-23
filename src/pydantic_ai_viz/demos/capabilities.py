"""Capabilities demo: one agent that uses most of what a capability can give it.

The `briefing` agent writes a short brief about the Apollo 13 accident. It thinks first, writes
a plan, searches and reads an archive through web tools, loads one of its two skills on demand,
saves a note to memory and then answers. Its `capabilities=` list shows up on the canvas, with
the skills it offers, and each step of the run shows up in the timeline.

Everything is scripted and offline: the "model" is a `FunctionModel`, the web tools read a small
archive bundled in this module, and nothing is ever fetched. The MCP server is illustrative: its
tools run provider-side, so the address is never contacted. The skills, `fact-check` and
`house-style`, live in `_skills`, next to this module, and are only read; the run loads
`house-style` and leaves `fact-check` unloaded.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import MCP, ModelFallback, Thinking, WebFetch, WebSearch
from pydantic_ai.workspaces import LocalWorkspaceBackend, WindowsWorkspaceBackend
from pydantic_ai_harness import GuardrailResult, InputGuardrail, Memory, Planning, Skills
from pydantic_ai_harness.memory import InMemoryMemoryStore, MemoryStore

from ._base import Demo
from ._script import History, Turn, call, pause, playback, scripted_model

QUESTION = "Brief me on the Apollo 13 accident: what failed, and how did the crew get home?"

_ARCHIVE: dict[str, tuple[str, str]] = {
    "https://archive.example/apollo-13/cause": (
        "Apollo 13 review board report, cause",
        "On 13 April 1970 a fan stirred oxygen tank 2, and bare wires that had been damaged by an "
        "earlier overheated test caught fire. The tank burst, crippled the service module and cut "
        "off the command module's power and water.",
    ),
    "https://archive.example/apollo-13/return": (
        "Apollo 13 mission report, return",
        "The crew moved into the lunar module, which served as a lifeboat. They swung round the "
        "Moon, fitted a square carbon dioxide filter to a round socket with tape and a sock, and "
        "splashed down safely on 17 April 1970.",
    ),
}

_SOURCES = "Apollo 13 review board report; Apollo 13 mission report"

_BRIEF = (
    "Verdict: an oxygen tank explosion ended the landing, and the crew got home in the lunar module.\n"
    "- What happened: oxygen tank 2 burst on 13 April 1970 and crippled the service module.\n"
    "- Why: bare wires damaged in an earlier test caught fire when a fan stirred the tank.\n"
    "- How it ended: the crew used the lunar module as a lifeboat and splashed down on 17 April.\n"
    f"Sources: {_SOURCES}."
)


_notebooks: dict[str, MemoryStore] = {}


def notebook(ctx: RunContext[Any]) -> MemoryStore:
    """The memory store of one run: every play of the scenario starts from an empty one."""
    return _notebooks.setdefault(ctx.run_id or "", InMemoryMemoryStore())


def skills_backend() -> LocalWorkspaceBackend | WindowsWorkspaceBackend:
    """A host workspace rooted at this package, where the bundled skills are read from."""
    root = Path(__file__).parent
    return WindowsWorkspaceBackend(root) if sys.platform == "win32" else LocalWorkspaceBackend(root)


async def search_archive(query: str) -> list[dict[str, str]]:
    """Search the bundled archive and return the matching pages.

    Args:
        query: What to look for.
    """
    await pause(500)
    return [{"title": title, "url": url} for url, (title, _) in _ARCHIVE.items()]


async def fetch_page(url: str) -> str:
    """Read one page of the bundled archive.

    Args:
        url: The address of the page, as returned by a search.
    """
    await pause(350)
    return _ARCHIVE[url][1] if url in _ARCHIVE else f"No page at {url}"


def check_topic(prompt: str) -> GuardrailResult:
    """Let a prompt through unless it asks for something the briefing desk does not cover."""
    if "medical advice" in prompt.lower():
        return GuardrailResult.block("The briefing desk does not give medical advice.")
    return GuardrailResult.allow()


def _read_pages(history: History) -> Turn:
    hits = history.result("search_archive")
    return Turn(
        text="The archive has two pages that fit. Reading both.",
        calls=tuple(call("fetch_page", url=hit["url"]) for hit in hits),
    )


briefing = Agent(
    scripted_model(
        "scripted-capabilities",
        Turn(
            thinking=(
                "A short brief needs the cause and the recovery. I should look in the archive rather than "
                "answer from memory, follow the house style, and keep a note of which pages I used."
            ),
            text="First a plan, then the archive.",
            calls=(
                call(
                    "write_plan",
                    items=[
                        {"id": "1", "content": "Search the archive", "status": "in_progress"},
                        {"id": "2", "content": "Read the pages that fit", "status": "pending"},
                        {"id": "3", "content": "Load the house style", "status": "pending"},
                        {"id": "4", "content": "Write the brief", "status": "pending"},
                    ],
                ),
            ),
        ),
        Turn(calls=(call("search_archive", query="Apollo 13 oxygen tank explosion and return"),)),
        _read_pages,
        Turn(text="Now the house style.", calls=(call("load_capability", id="house-style"),)),
        Turn(
            calls=(
                call(
                    "write_memory",
                    content="- Apollo 13 briefs: cite the review board report and the mission report.",
                ),
            )
        ),
        Turn(text=_BRIEF),
    ),
    name="briefing",
    description="Writes a short, sourced brief from an archive",
    instructions=(
        "You write short briefings. Plan first, search the archive, read the pages you will cite, load the "
        "house style before you write, and note anything worth remembering."
    ),
    capabilities=[
        Thinking(effort="high"),
        WebSearch(native=False, local=search_archive),
        WebFetch(native=False, local=fetch_page),
        MCP(url="https://docs.example.com/mcp", native=True, local=False),
        Skills(directories=["_skills"], workspace=skills_backend()),
        Memory(store_resolver=notebook),
        Planning(),
        InputGuardrail(guard=check_topic),
        ModelFallback(scripted_model("scripted-capabilities-backup", Turn(text=_BRIEF))),
    ],
)


async def run(speed: float = 1.0) -> str:
    """Play the scenario once and return the brief."""
    with playback(speed):
        result = await briefing.run(QUESTION)
    return result.output


demo = Demo(
    name="capabilities",
    title="Capabilities tour",
    description="One agent plans, searches, reads, loads a skill and keeps a note, all through capabilities.",
    targets=(briefing,),
    run=run,
)
