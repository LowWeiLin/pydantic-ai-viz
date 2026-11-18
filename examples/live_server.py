"""A long-lived process: a new run shows up in the viewer every few seconds. Offline, no API key.

Run it:  uv run python examples/live_server.py
"""

import itertools
import time

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import pydantic_ai_viz as pviz

CITIES = ("Lisbon", "Nairobi", "Osaka")
agent = Agent(TestModel(), name="forecaster", instructions="Answer with the current weather.")


@agent.tool_plain
def get_weather(city: str) -> dict[str, str | int]:
    """Current weather for a city."""
    return {"city": city, "sky": "clear", "celsius": 21}


if __name__ == "__main__":
    with pviz.serve(agent, open_browser=True) as session:
        print(f"Serving {session.url} (Ctrl+C to stop)")
        for city in itertools.cycle(CITIES):
            agent.run_sync(f"What is the weather in {city}?")
            time.sleep(3)
