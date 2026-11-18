---
title: pydantic-ai-viz
description: See what your Pydantic AI agents are and what they do, live or replayed.
---

# pydantic-ai-viz

See what your [Pydantic AI](https://ai.pydantic.dev/) agents **are** (the blueprint) and what they **do** (the run) on one canvas, live or replayed. No API key, no account, and nothing leaves your machine.

!!! note "Unofficial project"

    pydantic-ai-viz is a community project. It is not affiliated with or endorsed by Pydantic.

<figure class="screenshot" markdown="span">
  [![A finished agent run: the blueprint canvas with every tool marked done, the span timeline, and the inspector open on a retried tool call](assets/screenshots/hero-light.png#only-light)![A finished agent run: the blueprint canvas with every tool marked done, the span timeline, and the inspector open on a retried tool call](assets/screenshots/hero-dark.png#only-dark)](https://lowweilin.github.io/pydantic-ai-viz/demo/)
  <figcaption>The support demo after its run, with the retried tool call selected. Click the image to open the live demo.</figcaption>
</figure>

## Three commands

=== "See a demo"

    ```bash
    uvx pydantic-ai-viz demo
    ```

    Opens a browser tab and plays a scripted support agent: streamed tokens, parallel tool calls, a retry, a delegation and a structured result. The model is scripted, so there is no API key and no network call.

=== "Draw your agent"

    ```bash
    pydantic-ai-viz view my_app.agents:support_agent
    ```

    Draws the agent's blueprint: model, instructions, tools with their schemas, output type and the agents it delegates to. Nothing is run and no model is called.

=== "Watch your script"

    ```bash
    pydantic-ai-viz run my_script.py
    ```

    Runs your script unchanged and shows every agent run in it as it happens.

Or do the same from Python, with one line around the code you already have:

```python
import pydantic_ai_viz as pviz
from my_app import agent

with pviz.serve(agent, open_browser=True) as session:
    agent.run_sync("Where is my order?")
    session.wait()  # keep the viewer up until Ctrl+C
```

[Quickstart](quickstart.md){ .md-button .md-button--primary }
[Open the live demo](https://lowweilin.github.io/pydantic-ai-viz/demo/){ .md-button }

## What it shows

Blueprint

:   The agent as a diagram, read from the `Agent` object before anything runs: model, instructions, toolsets, tools with JSON schemas, output type, retries and delegation to other agents. [Visualize an agent](guides/visualize-an-agent.md)

Live runs

:   Model requests, tool calls, retries and sub-agent runs light up on the blueprint and stack up in a span timeline as they happen. You do not change how you call the agent. [Watch live runs](guides/live-runs.md)

Inspector

:   Click any span to read the conversation, tool arguments and results, token usage and the raw span attributes.

Graphs

:   `pydantic-graph` graphs get a blueprint too, and one changed call site lights up the active node during a run. [Graphs](guides/graphs.md)

Recordings and exports

:   Scrub back through any run. Save it as a `.pviz.jsonl` recording, or export one self-contained HTML file that opens offline. [Export and share](guides/export-and-share.md)

## What it is not

- **Not a production tracing backend.** It captures runs in one process and keeps them in memory. For production, use [Pydantic Logfire](https://pydantic.dev/logfire). The two work side by side; see [Use with Logfire](guides/logfire.md).
- **Not a chat playground.** Pydantic AI already has `agent.to_web()`.

## Requirements

Python 3.11 or newer and Pydantic AI 2.60 or newer (`pydantic-ai-slim>=2.60,<3`). See [Compatibility](reference/compatibility.md) for exactly what was tested.
