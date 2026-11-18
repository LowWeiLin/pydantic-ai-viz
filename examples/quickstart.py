"""Quickstart: see one agent and its runs in the browser. Offline (TestModel), no API key.

Run it:  uv run python examples/quickstart.py
"""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import pydantic_ai_viz as pviz

agent = Agent(TestModel(), name="greeter", instructions="Greet the user by their real name.")


@agent.tool_plain
def lookup_name(user_id: int) -> str:
    """Look up a user's name by id."""
    return "Ada Lovelace"


if __name__ == "__main__":
    session = pviz.serve(agent, open_browser=True)
    print(f"Serving {session.url} (Ctrl+C to stop)")
    agent.run_sync("Say hello to user 1.")
    session.wait()
