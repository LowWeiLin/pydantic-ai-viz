"""Use pydantic-ai-viz next to Logfire: Logfire owns instrumentation, the viewer gets a copy of the spans.

NEEDS `logfire` (not a dependency of this project) and is NOT run in CI:
    uv run --with logfire python examples/with_logfire.py
Without a Logfire token nothing leaves your machine (`send_to_logfire="if-token-present"`).
"""

import logfire
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import pydantic_ai_viz as pviz

agent = Agent(TestModel(), name="greeter", instructions="Greet the user.")

if __name__ == "__main__":
    # instrument=False: touch nothing global, just hand our span processor to Logfire's provider.
    session = pviz.serve(agent, instrument=False, open_browser=True)
    logfire.configure(send_to_logfire="if-token-present", additional_span_processors=[session.span_processor])
    logfire.instrument_pydantic_ai()
    print(f"Serving {session.url} (Ctrl+C to stop)")
    agent.run_sync("Hello!")
    session.wait()
