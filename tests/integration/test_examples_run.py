"""The examples that serve, run the way their first line says: as real ``__main__`` scripts.

``tests/demos/test_examples.py`` loads each example without its main block. Here the main block
runs in a child process, the test reads the URL the example prints, and checks over HTTP that
the run the example made is there. The only thing changed is the browser: ``webbrowser.open``
is a no-op in the child, so no window opens on the machine running the tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .support import REPO, final_spans, finished_runs, follow, get, serving

EXAMPLES = REPO / "examples"

# Runs the example as `python examples/NAME` would, minus the browser window.
RUN_WITHOUT_A_BROWSER = (
    "import runpy, sys, webbrowser\n"
    "webbrowser.open = lambda *args, **kwargs: True\n"
    "sys.argv = [sys.argv[1]]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def example(name: str) -> list[str]:
    return [sys.executable, "-c", RUN_WITHOUT_A_BROWSER, str(EXAMPLES / name)]


def test_quickstart_serves_its_agent_and_the_run_it_made(tmp_path: Path) -> None:
    with serving(example("quickstart.py"), cwd=tmp_path) as (url, _):
        status, _, body = get(f"{url}/api/v1/health")
        assert status == 200
        assert b'"ok": true' in body or b'"ok":true' in body
        events = follow(url, lambda seen: finished_runs(seen) >= 1)
        status, headers, page = get(f"{url}/")
    assert (status, headers["content-type"].split(";")[0]) == (200, "text/html")
    assert b'"mode":"live"' in page
    (blueprint,) = (e["agent"] for e in events if e["type"] == "blueprint.agent")
    assert (blueprint["name"], blueprint["source"]) == ("greeter", "object")
    assert [tool["name"] for tool in blueprint["tools"]] == ["lookup_name"]
    spans = final_spans(events)
    assert [span["kind"] for span in spans.values()] == ["agent_run", "model_request", "tool_call", "model_request"]
    assert {span["status"] for span in spans.values()} == {"ok"}
    # stream is off in the examples: TestModel output is dull to watch token by token.
    assert not any(event["type"] == "stream.delta" for event in events)


def test_live_server_keeps_producing_runs(tmp_path: Path) -> None:
    with serving(example("live_server.py"), cwd=tmp_path) as (url, _):
        events = follow(url, lambda seen: finished_runs(seen) >= 2)
    roots = [span for span in final_spans(events).values() if span["parent_id"] is None]
    assert [root["name"] for root in roots[:2]] == ["forecaster", "forecaster"]
    assert len({root["trace_id"] for root in roots}) == len(roots)
    prompts = [root["data"]["messages"][0]["parts"][0]["content"] for root in roots[:2]]
    assert prompts == ["What is the weather in Lisbon?", "What is the weather in Nairobi?"]


def test_graph_example_runs_its_graph_where_the_viewer_can_see_it(tmp_path: Path) -> None:
    with serving(example("graph.py"), cwd=tmp_path) as (url, lines):
        events = follow(url, lambda seen: finished_runs(seen) >= 1)
        assert lines[0].startswith("Graphs You Can Watch - by the graph desk (see http://")
    (graph,) = (e["graph"] for e in events if e["type"] == "blueprint.graph")
    assert graph["name"] == "headline"
    assert [node["label"] for node in graph["nodes"] if node["kind"] == "step"] == ["Title case", "Add byline"]
    spans = list(final_spans(events).values())
    root = spans[0]
    assert (root["kind"], root["data"]["inputs"]) == ("graph_run", "graphs you can watch")
    assert root["data"]["output"] == "Graphs You Can Watch - by the graph desk"
    assert [span["name"] for span in spans[1:]] == ["title_case", "add_byline", "__end__"]
