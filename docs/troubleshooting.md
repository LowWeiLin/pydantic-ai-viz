# Troubleshooting

## No runs appear { #no-runs-appear }

The viewer is up and shows blueprints, but running an agent adds nothing under **Runs**. Check these in order.

**The run happens in another process.** A session captures the process that called `serve` or `record`. A web server with several workers, a task queue or `multiprocessing` runs your agents elsewhere. Start the session in the process that runs the agent, or use `pydantic-ai-viz run your_script.py` for a single script.

**The session was closed first.** Leaving a `with pviz.serve(...)` block, or calling `session.close()`, ends capture. Run the agent inside the block.

**Something replaced the instrumentation setting.** `serve` installs a process-wide setting with `Agent.instrument_all(...)`. A later `Agent.instrument_all(...)` or `logfire.instrument_pydantic_ai()` call replaces it, and the viewer stops receiving spans. Follow [Use with Logfire](guides/logfire.md), which makes both work at once.

**You passed `instrument=False` and did not connect the processor.** In that mode nothing is captured until you add `session.span_processor` to the tracer provider your agents use.

**The agent has its own instrumentation setting.** See the next entry.

To confirm that capture works at all, run a demo in the same environment: `pydantic-ai-viz demo --speed 0`.

## An agent sets its own instrumentation { #an-agent-sets-its-own-instrumentation }

Pydantic AI lets a single agent override the process-wide setting. Such an agent reports to wherever *its* setting points, so its runs do not reach the viewer, while other agents' runs do. An agent overrides the setting if any of these is true:

- `agent.instrument` was assigned (to settings, or to `False`),
- it was created with an `Instrumentation` capability,
- its model is an `InstrumentedModel`, which is what `logfire.instrument_pydantic_ai(model)` produces.

Either remove the override while you are debugging locally, or keep it and send its spans to the viewer as well:

```python
import pydantic_ai_viz as pviz
from opentelemetry.sdk.trace import TracerProvider
from pydantic_ai.models.instrumented import InstrumentationSettings

session = pviz.serve(agent, instrument=False)

provider = TracerProvider()
provider.add_span_processor(session.span_processor)
agent.instrument = InstrumentationSettings(tracer_provider=provider)
```

## The port is in use

`serve` and the CLI default to port 8765. If something else has it, a free port is chosen and no error is raised, so **use the URL that is printed** (or `session.url`) and not a bookmark.

To choose the port yourself:

```bash
pydantic-ai-viz demo --port 9000
```

```python
pviz.serve(agent, port=9000)  # or port=0 for any free port
```

A second `pydantic-ai-viz` process is the usual culprit. An old browser tab pointing at the first port then still shows the first session.

## "a pydantic-ai-viz session is already active; close it first"

Only one instrumenting session can exist in a process, because the setting it installs is process-wide. Close the earlier session before starting a new one:

```python
session.close()
```

In a notebook, where a cell is often run twice, keep the session in a variable and close it at the top of the cell, or use a `with` block. In tests, use `with pviz.record(...)` so every test cleans up.

## The page says "UI not built"

You are running from a source checkout in which the viewer has not been built. The viewer is a build output and is not in the repository. Build it once:

```bash
pnpm --dir web install
pnpm --dir web build
```

Released packages on PyPI include the viewer, so this does not happen with `pip install pydantic-ai-viz`.

## A run fails with ``AssertionError: FunctionModel must receive a `stream_function` ``

Token streaming is on (`stream=True` or `--stream`) and the agent uses a model that cannot stream. Turning streaming on makes every model request of that agent a streaming request. Turn it off, or give the `FunctionModel` a `stream_function`. The reason is explained in [Token streaming](guides/token-streaming.md).

Because the listener stays on an agent for the life of the process, an agent that was streamed in one test can fail this way in a later test. Keep `stream=True` to agents whose models can stream.

## Agents with `defer_model_check=True`, or with no model

These are fine to **draw**. `view`, `analyze` and `blueprint` never call the model, never resolve a model string and never import a provider package, so this works without the OpenAI package or an API key:

```python
agent = Agent("openai:gpt-5.2", defer_model_check=True)
pviz.blueprint(agent).model  # id='openai:gpt-5.2', name='gpt-5.2', provider='openai'
```

An agent with no model is drawn with no model chip.

**Running** such an agent is a different matter and has nothing to do with the viewer: Pydantic AI needs the provider package and credentials at that point. To watch a run without them, override the model with a test model:

```python
from pydantic_ai.models.test import TestModel

with pviz.serve(agent, open_browser=True) as session:
    with agent.override(model=TestModel()):
        agent.run_sync("Hello")
    session.wait()
```

Note that without `defer_model_check=True`, `Agent("openai:gpt-5.2")` raises `ImportError` at construction when the provider package is missing. That happens when your module is imported, so it affects `view --import` and your own code, but not plain `view`, which reads the source and imports nothing.

## `view` fails with `ModuleNotFoundError` { #view-modulenotfounderror }

For example `pydantic-ai-viz: error: ModuleNotFoundError: No module named 'dotenv'`.

Plain `pydantic-ai-viz view` cannot raise this: it reads the source and imports nothing, so the packages your code imports do not have to be installed. The error comes from importing the code, which happens only with `view --import` (and with `run`, or in versions of pydantic-ai-viz before static analysis).

- Leave out `--import`: `pydantic-ai-viz view my_app/agents.py`. What the source computes when it runs is then shown as an expression; see [How view works](concepts/how-view-works.md).
- If you need `--import`, run it in the environment where your code's dependencies are installed, for example with `uv run pydantic-ai-viz view --import my_app.agents` from your project.

`pydantic-ai-viz --version` shows which version you have.

## `view` says a value is "decided at run time"

The source computes it instead of spelling it out: a model read from an environment variable or returned by a factory that takes arguments, a prompt built by a function call, a toolset made by a function. `view` shows the code that computes it rather than guessing, and does not take a default passed to such a call, such as the second argument of `os.getenv`, for the value. [How view works](concepts/how-view-works.md#what-shows-as-decided-at-run-time-and-why) lists what is followed and what is not. To see the real value, pass the agent to [`serve`](reference/python-api.md#serve) and run it, or use `view --import` on code you trust.

## `view` cannot find my agent

`view` reads source files; it does not import them.

- A target can be a file, a directory, a module name or a GitHub repository: `pydantic-ai-viz view my_app/`, `pydantic-ai-viz view my_app.agents`. A module name is looked for on disk, below the working directory, its `src` directory and the entries of `sys.path`.
- An agent is found where the source calls Pydantic AI's `Agent(...)`. An agent that comes out of a library function, or out of a class that subclasses `Agent`, is not visible in the source. Use `--import`, or pass the object to `pviz.serve(...)` or `pviz.export_html(...)` in Python.
- `file.py:name` selects a name assigned at the **top level** of the file. Without `:name`, agents made inside functions and classes are drawn too.
- A file read only because another one imports it adds no blueprints of its own. To see every agent of a project, give its directory.
- In a directory, virtual environments, `node_modules`, `site-packages`, `build`, `dist` and hidden directories are passed over. A `build` or `dist` that holds `.py` files is named in a note; give it as a target to read it.
- The message "no agents or graphs found in the source" means exactly that. If a file was skipped, the line before it says why.

With `--import`:

- `pydantic-ai-viz view --import my_app.agents:support_agent` imports `my_app.agents`. Run the command from the directory and environment where `python -c "import my_app.agents"` works.
- Only agents and graphs bound to a **top-level name** of the module or file are found. An agent created inside a function or stored in a dict is not. Name it explicitly (`module:attr`) if it is an attribute.
- A file target runs the file's top-level code but not its `if __name__ == "__main__":` block. An agent created only inside that block is not found.
- A target that calls `sys.exit()` in its top-level code (argument parsing outside the `__main__` block is the usual cause) cannot be loaded, and `view` says so. Move the exit, or the code that leads to it, into the `__main__` block.

## The agent is labelled `agent-1`

The agent has no `name`, and the viewer was not given a variable name to fall back on. Pydantic AI infers the name on the agent's first run, after which the label updates. To get a proper label from the start, pass `name=` when you create the agent, or pass the module that defines it as the target.

## Runs do not show up on an agent I registered

Runs are matched to blueprints by agent name. An agent created without `name=` gets a name from Pydantic AI on its first run, but only when it is called through a plain variable, as in `agent.run(...)`. Called through an attribute or a container (`self.agent.run(...)`, `agents[0].run(...)`), it stays unnamed, and its runs are reported under the placeholder name `agent`.

- With **one** unnamed registered agent, those runs are shown on it.
- With **several**, nothing says which of them ran. The runs are collected on a separate blueprint called `agent`, marked "Observed in runs", and each unnamed blueprint carries a warning.

The fix is to name each agent when you create it: `Agent(model, name="support")`.

## A blueprint shows a warnings badge

Part of the agent could not be read, most often because a Pydantic AI release changed an internal attribute. The inspector lists what is missing; everything else is unaffected. See [Compatibility](reference/compatibility.md#fields-read-from-private-attributes), and please report it with your `pydantic-ai-slim` version.

## A delegation edge is missing, or dashed

- **Missing:** reading the code only finds an agent that the tool runs itself and that can be traced to an `Agent(...)`; see [Evidence on delegation edges](concepts/blueprint-vs-run.md#evidence-on-delegation-edges). An agent run by a helper function, or reached in a way the code does not spell out, is found when the delegation first runs.
- **Dashed:** the edge was read from the code and has not been seen in a run yet.

Details in [Blueprint vs run](concepts/blueprint-vs-run.md#evidence-on-delegation-edges).

## Graph nodes do not light up

Agent runs inside a graph are captured automatically, but the graph's own nodes are only captured when the graph is run through `pviz.run_graph(...)`. See [Graphs](guides/graphs.md).

## Windows console

- **Garbled characters.** The classic Windows console uses the cp1252 code page. pydantic-ai-viz prints ASCII only, so its own output is safe, but text printed by your script or by other libraries may not be. Set `PYTHONUTF8=1`, or use Windows Terminal.
- **A large ASCII-art banner.** That is Pydantic AI's start-up banner. The CLI suppresses it; in your own scripts set `PYDANTIC_AI_NO_BANNER=1`.
- **++ctrl+c++ seems slow.** `session.wait()` polls so that ++ctrl+c++ works on Windows; it can take a fraction of a second to react.
- **`RuntimeError: This event loop is already running`.** This comes from calling `agent.run_sync()` inside a running event loop and is unrelated to the viewer. Use `await agent.run(...)` there.

## Old runs disappear

The event log is bounded. Past `max_events` (50,000 by default) the oldest finished runs are dropped. Pass a higher `max_events` to `serve` or `record`, or [save a recording](guides/export-and-share.md) of the runs you want to keep.

## A long tool result ends with "[truncated, ...]"

Strings longer than 32,768 characters are cut to keep the log and exports small. The note says how long the original was. The value your agent received was not affected.

## Still stuck

[Open an issue](https://github.com/LowWeiLin/pydantic-ai-viz/issues) with the output of `pydantic-ai-viz --version`, your `pydantic-ai-slim` version, your operating system, and the smallest script that shows the problem. If you can reproduce it with `TestModel`, the report needs no API key to investigate.
