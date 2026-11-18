"""Article pipeline demo: a step graph with a review loop and an agent inside each step.

The graph is `write` -> `review` -> "approved?" -> `publish`. The `writer` agent's first draft
is full of buzzwords, so the `critic` agent sends it back once; the second draft passes and is
published. Each agent keeps its message history in the graph state, so the revision and the
second review are real follow-up turns.

Everything is scripted and offline: the "models" are `FunctionModel`s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_graph import GraphBuilder, StepContext

from ._base import Demo
from ._script import History, Turn, call, finish, pause, playback, scripted_model

BRIEF = "Announce dark mode in the Harbor notes app. Around 50 words, for the product blog."
TITLE = "Dark mode comes to Harbor"

_FIRST_DRAFT = (
    "We are thrilled to unveil a revolutionary, game-changing dark mode for Harbor. Our team worked "
    "incredibly hard to craft this cutting-edge experience, and we just know you are going to love it. "
    "Dark mode is finally here. Enjoy!"
)
_SECOND_DRAFT = (
    "Harbor now has a dark mode. Open Settings, choose Appearance and pick Dark, or pick Auto to follow "
    "your system setting. Notes, code blocks and attachments all adapt, and the editor keeps its contrast "
    "at every text size. It is available today on desktop and mobile."
)
_BUZZWORDS = ("thrilled", "revolutionary", "game-changing", "cutting-edge")
_REVIEW_PROMPT = "Review this draft for the product blog:"


class DraftCheck(BaseModel):
    """Mechanical checks on a draft."""

    words: int
    buzzwords: list[str]
    says_how_to_enable: bool


class Review(BaseModel):
    """The critic's verdict on a draft."""

    approved: bool
    feedback: str = Field(description="What to change, or why it is ready.")


@dataclass
class Draft:
    """Graph state: the article as it moves through the pipeline."""

    brief: str = ""
    text: str = ""
    revision: int = 0
    writer_messages: list[ModelMessage] = field(default_factory=list[ModelMessage])
    critic_messages: list[ModelMessage] = field(default_factory=list[ModelMessage])


# --- agents -------------------------------------------------------------------------------

writer = Agent(
    scripted_model("scripted-pipeline", Turn(text=_FIRST_DRAFT), Turn(text=_SECOND_DRAFT)),
    name="writer",
    description="Drafts and revises short product posts",
    instructions="Write short posts for the product blog. When you get reviewer feedback, revise the draft.",
)


def _check_draft(history: History) -> Turn:
    return Turn(calls=(call("check_draft", text=history.prompt.removeprefix(_REVIEW_PROMPT).strip()),))


def _verdict(history: History) -> Turn:
    check: DraftCheck = history.result("check_draft")
    problems: list[str] = []
    if check.buzzwords:
        problems.append(f"Cut the hype ({', '.join(check.buzzwords)}).")
    if not check.says_how_to_enable:
        problems.append("Tell readers how to turn it on.")
    if problems:
        return Turn(calls=(finish(approved=False, feedback=" ".join(problems)),))
    return Turn(calls=(finish(approved=True, feedback=f"Clear and concrete in {check.words} words. Ship it."),))


critic = Agent(
    scripted_model("scripted-pipeline", _check_draft, _verdict, _check_draft, _verdict),
    name="critic",
    description="Reviews drafts against the house style",
    output_type=Review,
    instructions="Review drafts for the product blog. Run the style check first. Approve only plain, concrete writing.",
)


@critic.tool_plain
async def check_draft(text: str) -> DraftCheck:
    """Count words and flag hype and missing how-to information.

    Args:
        text: The draft to check.
    """
    await pause(400)
    lowered = text.lower()
    return DraftCheck(
        words=len(text.split()),
        buzzwords=[word for word in _BUZZWORDS if word in lowered],
        says_how_to_enable="settings" in lowered,
    )


# --- graph --------------------------------------------------------------------------------

builder = GraphBuilder(name="pipeline", state_type=Draft, input_type=str, output_type=str)


@builder.step(label="Write draft")
async def write(ctx: StepContext[Draft, None, str | Review]) -> str:
    state = ctx.state
    if isinstance(ctx.inputs, Review):
        prompt = f"Revise the draft. Reviewer feedback: {ctx.inputs.feedback}"
    else:
        state.brief = ctx.inputs
        prompt = f"Write the post for this brief: {ctx.inputs}"
    result = await writer.run(prompt, message_history=state.writer_messages)
    state.writer_messages = result.all_messages()
    state.text = result.output
    state.revision += 1
    return result.output


@builder.step(label="Review")
async def review(ctx: StepContext[Draft, None, str]) -> Review:
    state = ctx.state
    result = await critic.run(f"{_REVIEW_PROMPT}\n\n{ctx.inputs}", message_history=state.critic_messages)
    state.critic_messages = result.all_messages()
    return result.output


@builder.step(label="Publish")
async def publish(ctx: StepContext[Draft, None, Review]) -> str:
    await pause(500)
    return f"# {TITLE}\n\n{ctx.state.text}"


def _approved(verdict: Review) -> bool:
    return verdict.approved


builder.add(
    builder.edge_from(builder.start_node).to(write),
    builder.edge_from(write).to(review),
    builder.edge_from(review).to(
        builder.decision(note="approved?")
        .branch(builder.match(Review, matches=_approved).label("approved").to(publish))
        .branch(builder.match(Review).label("needs work").to(write))
    ),
    builder.edge_from(publish).to(builder.end_node),
)

graph = builder.build()


async def run(speed: float = 1.0) -> str:
    """Play the scenario once, traced through `pydantic_ai_viz.run_graph`, and return the published post."""
    from pydantic_ai_viz import run_graph

    with playback(speed):
        return await run_graph(graph, state=Draft(), inputs=BRIEF)


demo = Demo(
    name="pipeline",
    title="Article pipeline",
    description="A step graph writes, reviews and publishes a post, looping back once for a rewrite.",
    targets=(graph, writer, critic),
    run=run,
)
