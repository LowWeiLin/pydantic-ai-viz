---
title: pydantic-ai-viz
description: See what your Pydantic AI agents are and what they do, live or replayed.
---

# pydantic-ai-viz

See what your [Pydantic AI](https://ai.pydantic.dev/) agents **are** (the blueprint) and what they **do** (the run) on one canvas, live or replayed. Draw any project's agents straight from its source code, without running a line of it. No API key, no account, and nothing leaves your machine.

!!! note "Unofficial project"

    pydantic-ai-viz is a community project. It is not affiliated with or endorsed by Pydantic.

## Try it in 10 seconds

<div class="grid cards" markdown>

-   **See a demo**

    ```bash
    uvx pydantic-ai-viz demo
    ```

    Plays a scripted support agent in your browser: streamed tokens, parallel tool calls, a retry, a delegation and a structured result. The model is scripted, so there is no API key and no network call.

-   **See your own agents, with nothing executed**

    ```bash
    uvx pydantic-ai-viz view .
    ```

    Run it in your project's directory. It draws every agent and graph in the project from its source code: model, instructions, tools with their schemas, output type and who delegates to whom. The code is parsed, never imported or run, so its dependencies need not be installed.

</div>

<figure class="screenshot" markdown="span">
  [![pydantic-ai-viz view on the demos' own source: their files in the sidebar, the triage agent with its tools, output and a delegation edge to the refunds agent, and the inspector on a tool with its parameters and the line it is defined at](assets/screenshots/view-light.png#only-light)![pydantic-ai-viz view on the demos' own source: their files in the sidebar, the triage agent with its tools, output and a delegation edge to the refunds agent, and the inspector on a tool with its parameters and the line it is defined at](assets/screenshots/view-dark.png#only-dark)](https://lowweilin.github.io/pydantic-ai-viz/demo/?src=recordings/source.pviz.jsonl)
  <figcaption><code>pydantic-ai-viz view src/pydantic_ai_viz/demos</code>: the demos' source read, none of it run. Click the image to open it in the hosted viewer.</figcaption>
</figure>

<figure class="screenshot" markdown="span">
  [![A finished agent run: the blueprint canvas with every tool marked done, the span timeline, and the inspector open on a retried tool call](assets/screenshots/hero-light.png#only-light)![A finished agent run: the blueprint canvas with every tool marked done, the span timeline, and the inspector open on a retried tool call](assets/screenshots/hero-dark.png#only-dark)](https://lowweilin.github.io/pydantic-ai-viz/demo/)
  <figcaption>The support demo after its run, with the retried tool call selected. Click the image to open the hosted demo.</figcaption>
</figure>

## From the command line

=== "Read your code"

    ```bash
    pydantic-ai-viz view my_app/
    ```

    A directory, a file (`my_app/agents.py`), one agent in it (`my_app/agents.py:support_agent`), a module name (`my_app.agents`) or a GitHub repository (`gh:owner/repo`). Imports between your files are followed. [How view works](concepts/how-view-works.md)

=== "Watch your script"

    ```bash
    pydantic-ai-viz run my_script.py
    ```

    Runs your script unchanged and shows every agent run in it as it happens.

Or watch runs from Python, with one line around the code you already have:

```python
import pydantic_ai_viz as pviz
from my_app import agent

with pviz.serve(agent, open_browser=True) as session:
    agent.run_sync("Where is my order?")
    session.wait()  # keep the viewer up until Ctrl+C
```

[Quickstart](quickstart.md){ .md-button .md-button--primary }
[Open the hosted demo](https://lowweilin.github.io/pydantic-ai-viz/demo/){ .md-button }

## What it shows

Blueprint from source

:   The agent as a diagram: model, instructions, toolsets, tools with JSON schemas, output type, retries and delegation to other agents, each with the file and line it is defined at. Read from your source code, across the files of a project, without importing or running any of it; a value only running the code could tell is shown as the code that computes it. [Visualize an agent](guides/visualize-an-agent.md), [How view works](concepts/how-view-works.md), [Real-world examples](real-world.md)

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

## Acknowledgements

The canvas is drawn with [React Flow](https://reactflow.dev) and laid out with [dagre](https://github.com/dagrejs/dagre). The viewer is also built on [React](https://react.dev), [Radix UI](https://www.radix-ui.com), [Zustand](https://github.com/pmndrs/zustand) and [Tailwind CSS](https://tailwindcss.com). The licence of every library bundled into the viewer is at the top of the viewer's HTML file, and of every export made from it.
