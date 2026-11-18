# Python API

Everything public is importable from the top-level package. The examples in these docs use the alias `pviz`:

```python
import pydantic_ai_viz as pviz
```

| Name | Use it to |
|---|---|
| [`serve`](#serve) | Capture runs and show them live in a browser |
| [`record`](#record) | Capture runs without a server, to save or inspect them |
| [`analyze`](#analyze) | Read agents and graphs out of source files, without importing or running them |
| [`blueprint`](#blueprint) | Get the structure of a live agent or graph as data |
| [`export_html`](#export_html) | Write a single-file HTML export |
| [`run_graph`](#run_graph) | Run a `pydantic-graph` graph so its nodes are captured |
| [`Session`](#session) | The object `serve` and `record` return |
| [`AgentBlueprint`](#agentblueprint), [`GraphBlueprint`](#graphblueprint) | The models `blueprint` and `analyze` return |
| [`Analysis`](#analysis), [`Limits`](#limits) | What `analyze` returns, and the bounds on its work |
| `__version__` | The installed version, as a string |

Names that start with an underscore, and modules other than `pydantic_ai_viz` itself, are internal and can change in any release.

A **target** is something to draw: an `Agent`, a `Graph`, a module whose top-level agents and graphs should all be drawn, or an [`Analysis`](#analysis) of source files.

```python
Target = Agent[Any, Any] | Graph[Any, Any, Any, Any] | ModuleType | Analysis
```

## serve { #serve }

```python
def serve(
    *targets: Target,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    include_content: bool = True,
    stream: bool = False,
    instrument: bool = True,
    title: str | None = None,
    max_events: int = 50_000,
) -> Session
```

Start a session, start the local viewer server in a background thread, and return at once.

```python
with pviz.serve(agent, open_browser=True) as session:
    agent.run_sync("Where is my order?")
    session.wait()
```

| Parameter | Meaning |
|---|---|
| `*targets` | Agents, graphs and modules to draw before any run. Capture covers every agent in the process whether or not it is listed. |
| `host` | Address to bind. A non-loopback address issues a `UserWarning` and exposes the session to the network without authentication. |
| `port` | Port to bind. If it is busy, a free port is used instead. `0` always picks a free port. The real address is `session.url`. |
| `open_browser` | Open the viewer in the default browser. |
| `include_content` | Capture prompts, instructions, tool arguments, tool results and outputs. `False` captures structure, timing and token counts only. Applies when `instrument=True`. |
| `stream` | Capture tokens as they arrive. This switches the agents' model requests to streaming; see [Token streaming](../guides/token-streaming.md). Has no effect with `include_content=False`, because tokens are content. |
| `instrument` | `True`: call `Agent.instrument_all(...)` with a private tracer provider. `False`: touch nothing global; you add `session.span_processor` to your own provider. See [Use with Logfire](../guides/logfire.md). |
| `title` | Session title, shown in the viewer and used for download file names. |
| `max_events` | Upper bound on the in-memory event log. Past it, the oldest finished runs are dropped whole. |

**Raises** `RuntimeError("a pydantic-ai-viz session is already active; close it first")` if another instrumenting session is active.

Guide: [Watch live runs](../guides/live-runs.md).

## record { #record }

```python
def record(
    *targets: Target,
    include_content: bool = True,
    stream: bool = False,
    instrument: bool = True,
    title: str | None = None,
    max_events: int = 50_000,
) -> Session
```

Like [`serve`](#serve), but with no server: `session.url` is `None`. Use it in scripts, notebooks and tests, then read `session.events`, or call `session.save(...)` or `session.export_html(...)`.

```python
with pviz.record(agent) as session:
    agent.run_sync("Where is my order?")
    session.save("run.pviz.jsonl")
```

The parameters mean the same as for `serve`, and the same rule applies: one instrumenting session at a time. Sessions opened with `instrument=False` are not limited.

Guide: [Export and share](../guides/export-and-share.md).

## analyze { #analyze }

```python
def analyze(
    *targets: str | os.PathLike[str],
    limits: Limits | None = None,
) -> Analysis
```

Describe the agents and graphs in Python source files **without importing or running them**. The files are parsed: nothing they import has to be installed, no model is called and none of their code runs. This is what `pydantic-ai-viz view` does.

```python
found = pviz.analyze("my_app/agents.py")
for agent in found.agents:
    print(agent.label, agent.location, [tool.name for tool in agent.tools])
    print(agent.warnings)  # what the source computes at run time

pviz.export_html(found, "blueprint.html")
```

| Parameter | Meaning |
|---|---|
| `*targets` | Paths of `.py` files. `"path/to/file.py:name"` selects the agent or graph assigned to `name` at the top level of that file, together with the agents it delegates to. Files given together are read together, so imports between them are followed. |
| `limits` | A [`Limits`](#limits); the defaults suit ordinary projects. |

What the source spells out is reported as a value. What it computes at run time, such as `Agent(get_model())`, is reported as the source text that computes it and named in the blueprint's `warnings`; nothing is guessed. A file that cannot be read or parsed is skipped and named in `Analysis.notices`.

**Raises** `ValueError` if a target is not an existing `.py` file, or selects a name that no agent or graph is assigned to.

The result can be passed to [`export_html`](#export_html), [`serve`](#serve), [`record`](#record) and `Session.register` like any other target. Runs of an agent are then drawn on the blueprint read from source that carries its name.

Concept: [How static analysis works](../concepts/how-static-analysis-works.md).

### Analysis { #analysis }

```python
@dataclass(frozen=True)
class Analysis:
    agents: list[AgentBlueprint]
    graphs: list[GraphBlueprint]
    notices: list[str]
    files: list[str]

    @property
    def blueprints(self) -> list[AgentBlueprint | GraphBlueprint]: ...
```

| Field | Meaning |
|---|---|
| `agents` | One blueprint per `Agent(...)` in the source, with ids `a1`, `a2`, ... in the order they appear. `delegates` refer to these ids. |
| `graphs` | One blueprint per `GraphBuilder(...)` or `Graph(nodes=[...])`, with ids `g1`, `g2`, ... |
| `notices` | Messages about the analysis as a whole, such as a file that was skipped because it is not valid Python. |
| `files` | The files that were read, relative to their common directory. |
| `blueprints` | The agents, then the graphs. |

Every blueprint from `analyze` has `source == "code"` and a `location` (file and line).

### Limits { #limits }

```python
@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 2_000_000
    max_files: int = 2_000
    max_seconds: float = 30.0
```

Bounds that keep the analysis of unknown code quick and finite. A file larger than `max_file_bytes` is skipped, no more than `max_files` files are read, and the analysis stops once `max_seconds` have passed. Each of these leaves a notice.

## blueprint { #blueprint }

```python
def blueprint(
    target: Agent[Any, Any] | Graph[Any, Any, Any, Any],
) -> AgentBlueprint | GraphBlueprint
```

Return the structure of a live agent or graph object. (To read source files without importing them, use [`analyze`](#analyze).) This is a pure function: it makes no model call, starts no server and needs no session. It works on an agent whose model is a string that cannot be resolved here (`defer_model_check=True`) and on an agent with no model at all.

```python
bp = pviz.blueprint(agent)
print([tool.name for tool in bp.tools])
print(bp.warnings)
```

Anything that could not be read is reported in `bp.warnings`; the function does not raise for it.

Guide: [Visualize an agent](../guides/visualize-an-agent.md).

## export_html { #export_html }

```python
def export_html(
    source: Session | Agent[Any, Any] | Graph[Any, Any, Any, Any] | Analysis | str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> Path
```

Write a self-contained HTML export to `path` and return the path.

| `source` | The export contains |
|---|---|
| A `Session` | Everything the session has captured so far |
| A path to a `.pviz.jsonl` recording | That recording |
| An `Agent` or a `Graph` | Its blueprint only, with no runs |
| An [`Analysis`](#analysis) | The blueprints read from source, with no runs |

```python
pviz.export_html("run.pviz.jsonl", "run.html")
pviz.export_html(agent, "blueprint.html")
pviz.export_html(pviz.analyze("agents.py"), "blueprint.html")
```

## run_graph { #run_graph }

```python
async def run_graph(
    graph: Graph[Any, Any, Any, Any],
    *,
    state: Any = None,
    deps: Any = None,
    inputs: Any = None,
    session: Session | None = None,
) -> Any
```

Run a graph and capture it: one `graph_run` span around the execution and one `graph_node` span per task. Returns the graph's output, as `graph.run(...)` would.

The session used is `session` if given, else the active session. With neither, the graph is run with `graph.run(state=state, deps=deps, inputs=inputs)` and nothing is captured, so the call is safe to leave in production code. The graph is registered with the session if it was not already.

An exception raised by the graph marks the graph run as an error and is re-raised unchanged.

```python
output = await pviz.run_graph(graph, state=Draft(), inputs="Announce dark mode")
```

Guide: [Graphs](../guides/graphs.md), including the limit for parallel steps.

## Session { #session }

The object returned by [`serve`](#serve) and [`record`](#record). It owns the event log. A session is a context manager; leaving the `with` block calls `close()`.

```python
class Session:
    url: str | None
    title: str
    session_id: str
    include_content: bool
    stream: bool

    @property
    def events(self) -> list[dict[str, Any]]: ...
    @property
    def span_processor(self) -> SpanProcessor: ...
    @property
    def closed(self) -> bool: ...

    def register(self, *targets: Target) -> None: ...
    def save(self, path: str | os.PathLike[str]) -> Path: ...
    def export_html(self, path: str | os.PathLike[str]) -> Path: ...
    def wait(self) -> None: ...
    def close(self) -> None: ...
```

| Member | Description |
|---|---|
| `url` | Address of the viewer, for example `"http://127.0.0.1:8765"`. `None` for a session from `record`. |
| `title` | The session title. |
| `session_id` | 32 hexadecimal characters, new for every session. It is the `session_id` of the `session.started` event. |
| `include_content`, `stream` | What the session captures. `stream` is `False` whenever `include_content` is `False`. |
| `events` | A snapshot of the event log: a new list of JSON-ready dictionaries, one per event, in order. Still readable after `close()`. Treat the dictionaries as read-only. The shapes are in the [wire format](wire-format.md). |
| `span_processor` | An OpenTelemetry `SpanProcessor` that feeds this session. Only needed with `instrument=False`: add it to your own tracer provider. |
| `register(*targets)` | Draw more agents, graphs or modules. An agent's delegates, as far as its code shows, are registered with it. A module contributes its top-level agents and graphs, labelled by variable name when they have no `name`. Registering an object twice does nothing. |
| `save(path)` | Write the event log as a `.pviz.jsonl` recording. Returns the path. |
| `export_html(path)` | Write the event log as a single-file HTML export. Returns the path. |
| `wait()` | Block until ++ctrl+c++, or until the session is closed from another thread. Use it to keep a script alive while you look at the viewer. |
| `close()` | Stop the server and end the session. Safe to call more than once. If the session installed its instrumentation setting, the setting that was there before is put back (off, if there was none). |
| `closed` | `True` once `close()` has run. |

## AgentBlueprint { #agentblueprint }

A Pydantic model describing one agent. Returned by [`blueprint`](#blueprint) for an `Agent`, and carried by `blueprint.agent` events.

```python
bp = pviz.blueprint(agent)
bp.model_dump()  # a plain dict
bp.model_dump_json()  # JSON, the same shape as on the wire
```

Main fields: `id`, `name`, `label`, `description`, `source`, `model`, `deps_type`, `instructions`, `toolsets`, `tools`, `native_tools`, `output`, `capabilities`, `settings`, `delegates` and `warnings`. Each is specified under [Blueprints](wire-format.md#blueprints) in the wire format reference.

## GraphBlueprint { #graphblueprint }

A Pydantic model describing one graph. Returned by [`blueprint`](#blueprint) for a `Graph`, and carried by `blueprint.graph` events.

Fields: `id`, `name`, `label`, `state_type`, `input_type`, `output_type`, `nodes`, `edges`, `mermaid` and `warnings`. See [Blueprints](wire-format.md#blueprints).

## Docstrings

The section below is generated from the docstrings of the installed source.

::: pydantic_ai_viz
    options:
      heading_level: 3
      show_root_heading: true
      show_root_full_path: true
      show_if_no_docstring: false
