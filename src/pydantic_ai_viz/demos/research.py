"""Research demo: a planner fans out to parallel searchers, then hands off to a writer.

The `planner` splits a question into three sub-questions and delegates each one to the same
`searcher` sub-agent at the same time. It then passes the findings to a `writer` sub-agent and
returns the finished report as plain text.

Everything is scripted and offline: the "models" are `FunctionModel`s and the searcher reads a
small library bundled in this module.
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from ._base import Demo
from ._script import History, Turn, call, pause, playback, scripted_model

QUESTION = "Why were the Hubble Space Telescope's first images blurry, and how was it fixed?"


class Source(BaseModel):
    """One passage from the bundled library."""

    title: str
    excerpt: str


_FAILURE_REPORT = "The Hubble Space Telescope Optical Systems Failure Report (NASA, 1990)"

_LIBRARY: dict[str, tuple[frozenset[str], list[Source]]] = {
    "cause": (
        frozenset({"cause", "flaw", "shape", "wrong"}),
        [
            Source(
                title=_FAILURE_REPORT,
                excerpt=(
                    "The 2.4 m primary mirror was polished to the wrong shape: its outer edge is too flat "
                    "by about 2.2 micrometres, which produces spherical aberration."
                ),
            ),
            Source(
                title=_FAILURE_REPORT,
                excerpt=(
                    "The reflective null corrector that guided the polishing had one lens 1.3 mm out of "
                    "position, so the mirror was made very precisely to the wrong prescription."
                ),
            ),
        ],
    ),
    "testing": (
        frozenset({"testing", "tests", "missed", "launch", "caught"}),
        [
            Source(
                title=_FAILURE_REPORT,
                excerpt=(
                    "Two other test instruments showed the error during manufacture, but their results "
                    "were discounted because the reflective null corrector was trusted as more accurate."
                ),
            ),
            Source(
                title=_FAILURE_REPORT,
                excerpt="The assembled telescope was never given an end-to-end optical test before launch.",
            ),
        ],
    ),
    "repair": (
        frozenset({"repair", "repaired", "fixed", "orbit", "servicing"}),
        [
            Source(
                title="Servicing Mission 1 (STS-61), December 1993",
                excerpt=(
                    "Astronauts installed COSTAR, a set of small corrective mirrors, and swapped the main "
                    "camera for WFPC2, which had the correction built in."
                ),
            ),
            Source(
                title="Hubble instrument history",
                excerpt=(
                    "Instruments installed later carried their own correction, so COSTAR was no longer "
                    "needed and was removed in 2009."
                ),
            ),
        ],
    ),
}

_REPORT = """\
## Why Hubble launched with blurred vision

**What went wrong.** The 2.4 m primary mirror was polished very precisely to the wrong shape. Its \
edge is about 2.2 micrometres too flat, because a lens in the test device that guided the polishing \
sat 1.3 mm out of position.

**Why it was missed.** Two other instruments flagged the error, but their readings were discounted, \
and the assembled telescope never had an end-to-end optical test.

**How it was fixed.** In December 1993 astronauts fitted COSTAR's corrective mirrors and the WFPC2 \
camera, which had the correction built in. Later instruments corrected for the flaw themselves, and \
COSTAR came out in 2009."""

# --- searcher sub-agent -------------------------------------------------------------------


def _summarise_sources(history: History) -> Turn:
    sources: list[Source] = history.result("search_library")
    if not sources:
        return Turn(text=f"The library has nothing on: {history.prompt}")
    return Turn(text=" ".join(f"{source.excerpt} [{source.title}]" for source in sources))


searcher = Agent(
    scripted_model(
        "scripted-research",
        lambda history: Turn(calls=(call("search_library", query=history.prompt),)),
        _summarise_sources,
    ),
    name="searcher",
    description="Answers one narrow question from the library, with sources",
    instructions="Search the library for the question you are given and report what the sources say. Cite each one.",
)


@searcher.tool_plain
async def search_library(query: str) -> list[Source]:
    """Search the bundled offline library.

    Args:
        query: What to look for.
    """
    words = set(re.findall(r"[a-z]+", query.lower()))
    for position, (keywords, sources) in enumerate(_LIBRARY.values()):
        if words & keywords:
            await pause(450 + 200 * position)
            return sources
    await pause(450)
    return []


# --- writer sub-agent ---------------------------------------------------------------------

writer = Agent(
    scripted_model("scripted-research", Turn(text=_REPORT)),
    name="writer",
    description="Turns research findings into a short report",
    instructions="Write a short report from the findings you are given. Three bold-led paragraphs, no filler.",
)

# --- planner ------------------------------------------------------------------------------

planner = Agent(
    scripted_model(
        "scripted-research",
        Turn(
            thinking=(
                "Three separate questions here: what went wrong with the optics, why nobody caught it "
                "before launch, and how it was repaired. They do not depend on each other."
            ),
            text="I will look into the cause, the missed warning signs and the repair in parallel.",
            calls=(
                call("search", query="cause of the Hubble primary mirror flaw"),
                call("search", query="why testing missed the Hubble mirror error before launch"),
                call("search", query="how Hubble's optics were repaired in orbit"),
            ),
        ),
        lambda history: Turn(
            text="All three searches are back. Passing the findings to the writer.",
            calls=(call("write_report", question=history.prompt, findings=history.results("search")),),
        ),
        lambda history: Turn(text=history.result("write_report")),
    ),
    name="planner",
    description="Breaks a question down, delegates the research and the writing",
    instructions=(
        "You are a research lead. Split the question into independent sub-questions, send each to the "
        "searcher, then have the writer turn the findings into a report. Return the report unchanged."
    ),
)


@planner.tool
async def search(ctx: RunContext[object], query: str) -> str:
    """Ask the searcher agent one narrow question.

    Args:
        query: The sub-question to research.
    """
    result = await searcher.run(query, usage=ctx.usage)
    return result.output


@planner.tool
async def write_report(ctx: RunContext[object], question: str, findings: list[str]) -> str:
    """Have the writer agent turn findings into a report.

    Args:
        question: The original question.
        findings: What the searches found, one entry per sub-question.
    """
    prompt = f"Question: {question}\n\nFindings:\n" + "\n".join(f"- {finding}" for finding in findings)
    result = await writer.run(prompt, usage=ctx.usage)
    return result.output


async def run(speed: float = 1.0) -> str:
    """Play the scenario once and return the report."""
    with playback(speed):
        result = await planner.run(QUESTION)
    return result.output


demo = Demo(
    name="research",
    title="Research team",
    description="A planner fans out to three parallel searchers, then a writer drafts the report.",
    targets=(planner, searcher, writer),
    run=run,
)
