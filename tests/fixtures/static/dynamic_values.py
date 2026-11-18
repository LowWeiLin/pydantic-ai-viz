"""Values that only exist at run time. Nothing here can be imported: the packages do not exist.

The analyser must report each computed value as the text that computes it and
never as a guess.
"""

import os
from textwrap import dedent

from dotenv import load_dotenv  # not installed
from my_company.llm import get_model, settings_for  # does not exist
from my_company.prompts import PROMPTS, house_style  # does not exist
from pydantic_ai import Agent, ModelSettings, RunContext
from pydantic_ai.models.openai import OpenAIChatModel

load_dotenv()

RETRIES = 4
MODEL = "openai:gpt-5.2"
GREETING = "Hello"
GREETING = "Hi"  # assigned twice: which one applies depends on the run
TEAM = os.environ["TEAM"]
PREAMBLE = dedent(
    """
    You are part of the support team.
    Stay polite.
    """
).strip()

computed = Agent(
    get_model(),
    name=f"support-{TEAM}",
    description=house_style("support"),
    instructions=[
        PREAMBLE,
        f"You work for the {TEAM} team, tier {os.environ.get('TIER', 'basic')!r}.",
        PROMPTS["support"],
        house_style,
        lambda ctx: ctx.deps.motto,
        GREETING,
    ],
    system_prompt=PREAMBLE + " Answer in English.",
    retries=RETRIES,
    end_strategy=os.getenv("END_STRATEGY", "early"),
    tool_timeout=settings_for("timeout"),
    model_settings=ModelSettings(temperature=0.2, max_tokens=settings_for("max_tokens")),
)

by_constant = Agent(MODEL, retries=compute_retries(), model_settings=settings_for("all"))  # noqa: F821

by_class = Agent(OpenAIChatModel("gpt-5.2", provider=make_provider()))  # noqa: F821

by_env = Agent(os.getenv("MODEL", "openai:gpt-5.2"), name="by_env", instructions=GREETING)

OPTIONS = {"name": "splatted", "retries": 2}
splatted = Agent("test", **OPTIONS)

no_model = Agent(name="no_model")


@computed.instructions
def with_context(ctx: RunContext[None]) -> str:
    return "Today is a good day."
