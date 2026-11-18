# Export and share

A session lives in memory and ends with your process. To keep a run, or to show it to someone, write it to a file. There are two formats.

| | Recording | Export |
|---|---|---|
| File | `name.pviz.jsonl` | `name.html` |
| Contains | The session's event log, one JSON object per line | The viewer plus the event log, in one file |
| Open it with | `pydantic-ai-viz open name.pviz.jsonl`, or drop it on any viewer | A double-click. Any browser, offline, no install |
| Good for | Keeping, diffing, test fixtures, converting later | Sending to someone, attaching to an issue or a pull request |

Both hold the same data, and a recording can be turned into an export at any time. Both replay in the viewer exactly as a live session does: the canvas, the timeline, the inspector and the playback bar all work.

!!! warning "These files contain your data"

    A recording or an export holds the prompts, instructions, tool arguments, tool results and model output of every captured run, in plain text, readable by anyone who has the file. Treat it like a log file that may contain secrets or personal data. To capture a run without its content, see [Privacy](privacy.md).

## Save from Python

```python
import pydantic_ai_viz as pviz
from my_app.agents import support_agent

with pviz.record(support_agent, title="Refund flow") as session:
    support_agent.run_sync("My order arrived broken.")

    session.save("refund-flow.pviz.jsonl")   # a recording
    session.export_html("refund-flow.html")  # an export
```

[`record`](../reference/python-api.md#record) captures exactly like `serve` but starts no server, which is what you want in a script, a notebook cell or a test. A session started with `serve` has the same two methods.

Both methods return the `Path` they wrote, and both can be called at any time, as often as you like. Each call writes everything captured so far.

The module-level [`export_html`](../reference/python-api.md#export_html) converts without a session:

```python
pviz.export_html("refund-flow.pviz.jsonl", "refund-flow.html")  # recording -> export
pviz.export_html(support_agent, "blueprint.html")               # blueprint only, no runs
```

## Save from the command line

```bash
# Run a script, then write the files once it has finished
pydantic-ai-viz run --record out.pviz.jsonl --export out.html my_script.py

# Blueprint only
pydantic-ai-viz view my_app.agents --export blueprint.html

# Record a bundled demo without starting a server
pydantic-ai-viz demo support --speed 0 --record support.pviz.jsonl

# Convert a recording to an export
pydantic-ai-viz export out.pviz.jsonl -o out.html
```

With `run`, the files are written when the script ends, and the viewer then stays up until ++ctrl+c++. With `demo`, giving `--record` or `--export` plays the scenario once, writes the files and exits.

## Save from the viewer

In a live session, the **Export** menu in the top bar downloads the current session as a recording or as an export. It is the same data `session.save` and `session.export_html` write.

## Open a recording

```bash
pydantic-ai-viz open refund-flow.pviz.jsonl
```

This converts the recording to an export in a temporary directory and opens it in your browser. No server is started.

You can also drop a `.pviz.jsonl` file onto a viewer that is showing its gallery or its empty state, including the [hosted demo](https://lowweilin.github.io/pydantic-ai-viz/demo/). The file is read by your browser and is not uploaded anywhere.

## Share a run

**Send the export.** It is one self-contained HTML file. It loads no scripts, fonts or styles from the network and makes no requests, so it works from a file share, an email attachment or an air-gapped machine. A deep link to a span (`refund-flow.html#/runs/<trace_id>?span=<span_id>`) opens with that span selected.

**Or host recordings as a gallery.** The viewer is a static page that looks for a `recordings/index.json` next to itself. Put these on any static host:

```text
index.html                   # a copy of the viewer
recordings/index.json
recordings/refund-flow.pviz.jsonl
```

```json title="recordings/index.json"
{
  "recordings": [
    {
      "id": "refund-flow",
      "title": "Refund flow",
      "description": "Triage hands a damaged order to the refunds agent.",
      "src": "recordings/refund-flow.pviz.jsonl"
    }
  ]
}
```

The page then shows a card per recording. `index.html?src=recordings/refund-flow.pviz.jsonl` opens one directly; `src` must be on the same origin as the page. The [hosted demo](../demos.md) is built exactly this way.

## Before you share

1. **Decide whether the content may leave your machine.** If not, capture with `include_content=False` (or `pydantic-ai-viz run --no-content`). The file then holds structure, timing and token counts, with every prompt, argument and result left out. This is decided at capture time; a file cannot be redacted afterwards.
2. **Remember what else is in there.** Instructions and system prompts, the JSON schemas of your tools, the names of your functions and modules, model names, and the stack trace of any error.
3. **Open the file yourself first** and read a few conversations. What the model was told is easy to forget.

## Using recordings in tests

A recording is line-delimited JSON, and `session.events` gives you the same events as Python dictionaries without touching the disk:

```python
import pydantic_ai_viz as pviz


def test_refund_is_delegated(support_agent):
    with pviz.record(support_agent) as session:
        support_agent.run_sync("My order arrived broken.")

    tool_calls = [
        event["span"]["name"]
        for event in session.events
        if event["type"] == "span.ended" and event["span"]["kind"] == "tool_call"
    ]
    assert "escalate_refund" in tool_calls
```

The event shapes are specified in the [wire format reference](../reference/wire-format.md).
