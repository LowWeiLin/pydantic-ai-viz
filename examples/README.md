# Examples

Small, copyable scripts, each under 30 lines. All of them run offline with Pydantic AI's
`TestModel` (no API key, no network) except `with_logfire.py`, which needs the `logfire` package.

Run any of them from the repository root:

```
uv run python examples/quickstart.py
```

| File | What it shows | API used |
|---|---|---|
| [`quickstart.py`](quickstart.py) | The shortest useful start: one agent with one tool, served in the browser, one run. | `pviz.serve(agent)`, `session.wait()` |
| [`live_server.py`](live_server.py) | A long-lived process that produces a new run every three seconds, so you can watch runs arrive live. | `pviz.serve(...)` as a context manager |
| [`export_html.py`](export_html.py) | Capture without a server, then write a `.pviz.jsonl` recording and a single self-contained HTML file you can send to someone. | `pviz.record(...)`, `session.save()`, `session.export_html()` |
| [`graph.py`](graph.py) | A two-step `pydantic-graph` graph traced node by node. The only change to your code is the call site. | `pviz.run_graph(graph, inputs=...)` |
| [`with_logfire.py`](with_logfire.py) | Running next to Logfire: Logfire keeps ownership of instrumentation and the viewer receives the same spans. Not run in CI. | `pviz.serve(..., instrument=False)`, `session.span_processor` |

## Notes

- The scripts that call `serve` keep running until you press Ctrl+C; the viewer URL is printed
  on start (default `http://127.0.0.1:8765`).
- `export_html.py` writes `run.pviz.jsonl` and `run.html` into the current directory. Both files
  contain the prompts and tool results of the run, so treat them like logs.
- `with_logfire.py` needs an extra package: `uv run --with logfire python examples/with_logfire.py`.
  Without a Logfire token nothing is sent anywhere.
- Every script guards its run with `if __name__ == "__main__":`, so you can also look at the
  agent or graph without running it: `uv run pydantic-ai-viz view examples/quickstart.py`.
- Token streaming (`stream=True`) is left off here because `TestModel` output is not interesting
  to watch token by token. The bundled demos show it: `uv run pydantic-ai-viz demo`.

## Bigger scenarios

The three bundled demos are richer and equally offline. Their source is a good place to copy
patterns from (parallel tools, retries, delegation, a graph with a review loop):

```
uv run pydantic-ai-viz demo --list
uv run pydantic-ai-viz demo support
```

They live in [`src/pydantic_ai_viz/demos/`](../src/pydantic_ai_viz/demos/).

pydantic-ai-viz is an unofficial community project, not affiliated with or endorsed by Pydantic.
