# Use with Logfire

[Pydantic Logfire](https://pydantic.dev/logfire) is the production tracing backend for Pydantic AI. pydantic-ai-viz is a local viewer for development. They read the same spans, so they can run side by side: Logfire keeps sending traces as it does today, and the viewer gets a copy.

The same recipe applies if you configure Pydantic AI's instrumentation yourself with `Agent.instrument_all(...)` and your own OpenTelemetry tracer provider.

## The short version

```python
import logfire
import pydantic_ai_viz as pviz

from my_app.agents import support_agent

session = pviz.serve(support_agent, instrument=False)

logfire.configure(additional_span_processors=[session.span_processor])
logfire.instrument_pydantic_ai()

support_agent.run_sync("Where is my order?")
session.wait()
```

Two things are different from the usual `pviz.serve(agent)`:

1. **`instrument=False`** tells pydantic-ai-viz to leave Pydantic AI's instrumentation alone. It sets nothing process-wide.
2. **`session.span_processor`** is an ordinary OpenTelemetry `SpanProcessor`. You hand it to whatever tracer provider your agents report to. Here that is Logfire's.

## Why not just call `serve(agent)`?

By default, `serve` and `record` call `Agent.instrument_all(...)` with a private tracer provider. That setting is process-wide and there is only one of it. If Logfire (or your own code) has set it already, the default `serve` **replaces** it: the viewer works, and your traces stop reaching Logfire until the session closes. In the other order, Logfire replaces the viewer's setting and the viewer shows no runs.

With `instrument=False` nobody replaces anybody. One set of spans is produced, by your configuration, and both destinations receive it.

## With your own tracer provider

```python
from opentelemetry.sdk.trace import TracerProvider
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

import pydantic_ai_viz as pviz

session = pviz.record(instrument=False)

provider = TracerProvider()
provider.add_span_processor(session.span_processor)
# ... add your own exporters to the same provider ...

Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
```

## What changes in this mode

- **Your settings decide what is captured.** Whether prompts and tool results are recorded is governed by the `include_content` of *your* instrumentation settings (or Logfire's), because that is what produces the spans. The `include_content` argument of `serve` and `record` only applies when pydantic-ai-viz does the instrumenting.
- **Use the current instrumentation version.** The viewer understands the span names and attributes of instrumentation versions 3 to 6. Version 5 is Pydantic AI's default, so you only need to think about this if you pinned `version=2`, whose spans appear as unrecognised rows.
- **Other spans come along.** Logfire instruments more than agents (HTTP clients, databases, your own `logfire.span(...)` blocks). Spans that are not agent runs, model requests or tool calls are kept and shown as plain rows in the timeline, in their place in the tree. That is often useful context.
- **Logfire's graph spans are plain rows.** `pydantic-graph` reports to Logfire with its own spans. The viewer does not turn those into graph nodes; use [`run_graph`](graphs.md) for that.
- **Token streaming is independent.** `stream=True` works the same way with `instrument=False`, with the same [caveat](token-streaming.md).

## Checking that it works

Run an agent once and look at the viewer. If no run appears:

- Make sure the processor was added to the provider your agents actually use. With Logfire, it must be in the `additional_span_processors` of the `logfire.configure(...)` call that takes effect; a later `configure` call without it drops it.
- Make sure instrumentation is on at all: `logfire.instrument_pydantic_ai()` or `Agent.instrument_all(...)`.
- Create the session **before** configuring, as in the examples, so that `session.span_processor` exists when you need it.

More causes are listed under [No runs appear](../troubleshooting.md#no-runs-appear).

A runnable version of the first example is in [`examples/with_logfire.py`](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/examples/with_logfire.py). It needs the `logfire` package, which pydantic-ai-viz does not install.
