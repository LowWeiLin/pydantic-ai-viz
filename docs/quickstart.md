# Quickstart

Five minutes, no API key. You will play a demo, draw the blueprint of an agent, and watch a run live.

## Install

=== "pip"

    ```bash
    pip install pydantic-ai-viz
    ```

=== "uv"

    ```bash
    uv add --dev pydantic-ai-viz
    ```

=== "No install"

    ```bash
    uvx pydantic-ai-viz demo
    ```

    `uvx` runs the command in a throwaway environment. Good for the demo; to visualize your own agents, install the package next to them.

It needs Python 3.11 or newer. It installs `pydantic-ai-slim` and `opentelemetry-sdk` and nothing else, so it adds no model provider and no web framework to your project.

## 1. Play the demo

```bash
pydantic-ai-viz demo
```

A browser tab opens and a scripted support agent starts working on a ticket. Watch for:

- tokens streaming into the model request,
- three tool calls running in parallel,
- one tool turning amber (it asked the model to retry) and then succeeding,
- a dashed edge to a second agent turning solid when the delegation actually happens,
- the output node lighting up when the run ends.

Click any row in the timeline to inspect it. Drag the scrubber, or press ++space++, to replay. Press ++question++ for all keyboard shortcuts.

`pydantic-ai-viz demo --list` shows the other demos; see [Demos](demos.md). Stop the server with ++ctrl+c++.

## 2. Draw the blueprint of an agent

Save this as `quickstart.py`. It uses Pydantic AI's `TestModel`, so it runs offline:

```python title="quickstart.py"
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

agent = Agent(TestModel(), name="helper", instructions="Answer briefly.")


@agent.tool_plain
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    print(agent.run_sync("What is 2 + 3?").output)
```

Then:

```bash
pydantic-ai-viz view quickstart.py
```

The viewer shows the `helper` agent, its model, the `add` tool with its parameter schema, and a text output. `view` reads the source of `quickstart.py` and stops there: the file is not imported and none of it runs, so no agent is run and no model is called.

A target can also name one agent in a file: `pydantic-ai-viz view my_app/agents.py:support_agent`. More in [Visualize an agent](guides/visualize-an-agent.md), and in [How view works](concepts/how-view-works.md).

## 3. Watch a run live

### Without changing your code

```bash
pydantic-ai-viz run quickstart.py
```

`run` starts the viewer, runs your script as `python quickstart.py` would, and keeps serving after the script ends so you can look around. Every agent run in the script appears as it happens.

### From your code

```python
import pydantic_ai_viz as pviz

from quickstart import agent

with pviz.serve(agent, open_browser=True) as session:
    agent.run_sync("What is 2 + 3?")
    session.wait()  # block until Ctrl+C so the viewer stays up
```

[`serve`](reference/python-api.md#serve) starts a local server on `http://127.0.0.1:8765` and captures every agent run in this process from then on. The agent you pass is drawn at once; other agents show up when they first run. The calls to your agent stay exactly as they were.

More in [Watch live runs](guides/live-runs.md).

## 4. Keep a run

Use [`record`](reference/python-api.md#record) when you want the file and not a server:

```python
import pydantic_ai_viz as pviz

from quickstart import agent

with pviz.record(agent) as session:
    agent.run_sync("What is 2 + 3?")
    session.save("helper.pviz.jsonl")  # a recording: replay it with `pydantic-ai-viz open`
    session.export_html("helper.html")  # an export: one HTML file, opens offline
```

!!! warning "Exports contain your data"

    A recording or an export holds the prompts, tool arguments and tool results of the run in plain text. Read [Privacy](guides/privacy.md) before you share one.

## Where next

- Running against a real model works the same way; nothing here is specific to `TestModel`.
- [Token streaming](guides/token-streaming.md) shows tokens as they arrive. It is opt-in for a reason.
- [Graphs](guides/graphs.md) covers `pydantic-graph`.
- Already using Logfire? Read [Use with Logfire](guides/logfire.md) first.
- If no runs appear, see [Troubleshooting](troubleshooting.md).
