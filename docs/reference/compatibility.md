# Compatibility

## Supported versions

| Dependency | Declared range |
|---|---|
| Python | `>=3.11` |
| Pydantic AI | `pydantic-ai-slim>=2.60,<3` |
| OpenTelemetry SDK | `opentelemetry-sdk>=1.30` |

These two packages are the only runtime dependencies. `pydantic-ai-slim` is the core of Pydantic AI without any model provider; if you have `pydantic-ai` installed, you already have it. The viewer needs a current desktop browser (Chromium, Firefox or Safari).

Pydantic AI 1.x is not supported. The 2.0 release renamed and removed much of what this tool reads.

## What was actually tested

A declared range is a promise; this section is the evidence. It lists only combinations that were run.

The version sweep below ran the full Python test suite (`pytest`, 507 tests at the time) on Windows 11 on 18 November 2026. The suite includes the three demos recorded through the real command line and a live demo server followed over HTTP.

| Python | pydantic-ai-slim | opentelemetry-sdk | Result |
|---|---|---|---|
| 3.12.9 | 2.70.0 (the locked version, and the newest release) | 1.45.2 | 507 passed |
| 3.11.14 | 2.70.0 | 1.45.2 | 507 passed |
| 3.13.9 | 2.70.0 | 1.45.2 | 507 passed |
| 3.14.0 | 2.70.0 | 1.45.2 | 507 passed |
| 3.11.14 | 2.60.0 | 1.30.0 | 506 passed, 1 skipped. This is the lowest-direct resolution: every direct dependency at its declared floor. |
| 3.12.9 | 2.65.0 | 1.45.2 | 507 passed |
| 3.12.9 | 2.62.0 | 1.45.2 | 507 passed |
| 3.12.9 | 2.61.0 | 1.45.2 | 507 passed |
| 3.12.9 | 2.60.0 | 1.45.2 | 507 passed |
| 3.12.9 | 2.59.0, 2.58.0, 2.57.0, 2.55.0 | 1.45.2 | **Fails**: 7 tests each, all about tool retries |
| 3.12.9 | 2.50.0 | 1.45.2 | **Fails**: 8 tests |
| 3.12.9 | 2.40.0 | 1.45.2 | **Fails**: 10 tests |
| 3.12.9 | 2.20.0 | 1.45.2 | **Fails**: 30 tests, and 20 more stop with an error |
| 3.12.9 | 2.0.0 | 1.45.2 | **Fails**: 34 tests, and 23 more stop with an error |

The skipped test opens the viewer in a browser, which needs a built viewer.

The suite has grown since, mostly with tests of static analysis, and was run again in full on 19 November 2026 with both ends of the range: 2.70.0 and 2.60.0 pass. With 2.60.0, two more tests are skipped, because they need `GraphBuilder.add_reachable`, which its pydantic-graph does not have. The 12 tests of the real-world set, which fetch from GitHub, pass as well.

Releases between the ones named were not run.

### Why the floor is 2.60 { #why-the-floor-is-2-60 }

The floor is the oldest release on which the whole suite passes, found by running it against older releases, not by reading changelogs.

- **2.60 is where Pydantic AI started to report tool retries on the span** (`pydantic_ai.tool.retry.cause`, `.attempt`, `.max_retries`). Before that, a tool that raised `ModelRetry` produces a span that looks exactly like a tool that failed. The viewer would show a retry in red, as an error, which is the one thing it promises not to do. The message history also represents a retry differently before 2.60.
- **On 2.40.0 (but no longer on 2.50.0), an agent run's reported token usage includes the runs of agents it delegated to.** The viewer's totals do not depend on it (they are summed over model requests), but the per-run number would mean something else.
- **2.20 and 2.0 have no `Agent.on_event`,** which token streaming is built on, and do not accept instrumentation version 6.

The package still imports on 2.0.0 and most of it works there, but "most" is not what a declared range promises, so `pip` will refuse anything older than 2.60.

**Operating systems.** The suite passes in CI on Linux (Python 3.11 to 3.14), Windows and macOS (Python 3.12), and the viewer's browser tests run on Linux.

CI is set up to keep this honest going forward:

| CI job | What it checks |
|---|---|
| Test matrix | The suite passes on Python 3.11 to 3.14 on Linux, and on 3.12 on Windows and macOS, with the locked dependency versions |
| Lowest direct dependencies | The suite passes with the *lowest* versions the declared ranges allow. If this job fails, the floor is raised. |
| Latest Pydantic AI | The suite passes with the newest `pydantic-ai-slim` 2.x, ahead of the lock file |
| Weekly canary | The same check against the newest release, on a schedule, opening an issue when it breaks |

## How breakage is contained

Pydantic AI moves fast. pydantic-ai-viz depends on it in two ways with very different risk, and treats them differently.

**Runs rely on documented behaviour.** Capture uses `Agent.instrument_all(...)`, an OpenTelemetry `SpanProcessor`, and the span attributes Pydantic AI emits following the OpenTelemetry GenAI conventions. If a release adds a span the tool does not recognise, it is shown as a plain row with its raw attributes, not dropped.

**Blueprints rely partly on private attributes.** Some facts about an agent have no public accessor. Those are read from underscore-prefixed attributes, through one guarded helper. When an attribute is missing or has changed shape:

1. the affected field is left empty or `null`,
2. a sentence is added to the blueprint's `warnings`, for example `instructions unavailable (pydantic-ai internals changed)`,
3. the viewer shows a warnings badge on the agent card,
4. everything else keeps working, and no exception reaches your code.

So the failure mode of a Pydantic AI upgrade is a less detailed blueprint with a visible note, not a crash.

## Fields read from private attributes { #fields-read-from-private-attributes }

| Blueprint field | Read from | If it is unavailable |
|---|---|---|
| `instructions` (kind `instructions`) | `agent._instructions` | Empty list and a warning. The resolved instructions of each run are still shown, from spans. |
| `instructions` (kind `system_prompt`) | `agent._system_prompts`, `agent._system_prompt_functions` | Same. |
| `settings.tool_retries`, `settings.output_retries` | `agent._max_tool_retries`, `agent._max_output_retries` | `null` and a warning. |
| `settings.tool_timeout` | `agent._tool_timeout` | `null` and a warning. |
| `output.mode`, `output.allows_text` | `agent._output_schema` | `"text"` when the output schema is a plain string, else `"unknown"`. Filled in from the first observed run. |
| `output.tool_names` | `agent._output_toolset`, then its `_tool_defs` | Empty list and a warning. Filled in from observed runs. |
| `native_tools` | `agent._cap_native_tools` | Empty list. |
| Graph `nodes` and `edges` | `pydantic_graph.graph_builder.build_mermaid_graph` (importable, but not exported) | The graph is walked through its public `nodes` and `edges_by_source` instead. |

Everything else in a blueprint comes from public API: `agent.name`, `.description`, `.model`, `.deps_type`, `.output_type`, `.model_settings`, `.end_strategy`, `.toolsets`, `.root_capability`, `agent.output_json_schema()`, the public fields of toolsets and tools, and `graph.render()`.

Outside the blueprint, one more private attribute is read: the class attribute `Agent._instrument_default`, which holds what `Agent.instrument_all` was last given. A session reads it so that closing can put the previous instrumentation setting back. If it cannot be read, closing a session switches instrumentation off instead.

If you see a warning of this kind, please [open an issue](https://github.com/LowWeiLin/pydantic-ai-viz/issues) with your `pydantic-ai-slim` version. It is usually a small fix.

## What introspection never does

To stay safe on any agent, including one configured for a provider you have no credentials for, building a blueprint never:

- calls a model,
- runs your dynamic instruction or system-prompt functions,
- builds a `RunContext` or asks a toolset for its tools,
- imports `pydantic_ai.mcp` (which needs an optional extra). MCP toolsets are recognised by class and module name and shown as one opaque node.

## Instrumentation versions

Pydantic AI's instrumentation has a `version` setting that changes span names and attributes.

| Version | Status |
|---|---|
| 5 | The Pydantic AI default, and what `serve` and `record` use. Fully supported. |
| 6 | Supported. It differs from 5 in the role given to tool results, which is normalised. |
| 3, 4 | Same names and attributes as 5 in what was tested. Deprecated by Pydantic AI. |
| 2 | Not supported: its span names differ, so its spans appear as unrecognised rows. Deprecated by Pydantic AI. |

This only matters with `instrument=False`, where your own settings produce the spans. See [Use with Logfire](../guides/logfire.md).

## Not supported in this version

- **MCP tool listing.** MCP toolsets are opaque until a run uses them.
- **Logfire's `pydantic-graph` spans.** They are shown as plain rows; use [`run_graph`](../guides/graphs.md) to see graph nodes.
- **Capture across processes.** One session captures one process.
- **Jupyter inline rendering.** Use `record` and `export_html`, or `serve` and a browser tab.

## Stability of this package

The version is 0.x: a minor release may change the Python API or the CLI, and such changes are listed in the [changelog](../changelog.md). The [wire format](wire-format.md#versioning) is versioned on its own and only grows within a version, so recordings stay readable.
