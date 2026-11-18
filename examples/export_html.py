"""Record a run without a server and save it as one self-contained HTML file. Offline, no API key.

Run it:  uv run python examples/export_html.py   (then open run.html in a browser)
"""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import pydantic_ai_viz as pviz

agent = Agent(TestModel(), name="librarian", instructions="Find the book the user is asking about.")


@agent.tool_plain
def search_catalogue(title: str) -> list[str]:
    """Search the library catalogue by title."""
    return [f"{title} (2nd edition)", f"{title}: a study guide"]


if __name__ == "__main__":
    with pviz.record(agent, title="Library search") as session:
        agent.run_sync("Do you have 'The Art of Computer Programming'?")
        recording = session.save("run.pviz.jsonl")
        page = session.export_html("run.html")
    print(f"Wrote {recording} and {page}. The files contain the prompts and tool results.")
