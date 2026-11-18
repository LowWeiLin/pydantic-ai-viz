"""The bundled demos, end to end through the real command line.

Each demo is recorded once by ``python -m pydantic_ai_viz demo NAME --speed 0 --record ... --export ...``
in a child process. Every line must pass the strict wire models, and each recording must show
what the demo promises a visitor: nothing here looks at Pydantic AI objects, only at the log a
viewer would be given.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from pydantic_ai_viz.demos import DEMOS

from .support import (
    Events,
    Spans,
    assert_log_guarantees,
    children,
    cli,
    final_spans,
    last_blueprints,
    model_request_usage,
    read_recording,
)

NAMES = ["support", "research", "pipeline"]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Events, Path]]:
    """Each demo's validated recording and its HTML export, made by the real CLI."""
    out = tmp_path_factory.mktemp("demo-recordings")
    made: dict[str, tuple[Events, Path]] = {}
    for name in NAMES:
        result = cli(
            "demo", name, "--speed", "0", "--record", f"{name}.pviz.jsonl", "--export", f"{name}.html", cwd=out
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert result.stdout.splitlines() == [
            f"Wrote recording {name}.pviz.jsonl",
            f"Wrote HTML export {name}.html",
        ]
        made[name] = (read_recording(out / f"{name}.pviz.jsonl"), out / f"{name}.html")
    return made


def root_of(spans: Spans) -> dict[str, Any]:
    (root,) = (span for span in spans.values() if span["parent_id"] is None)
    return root


def tool_calls_of(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls a model request answered with."""
    return [
        part
        for message in request["data"]["output_messages"]
        for part in message["parts"]
        if part["type"] == "tool_call"
    ]


def streamed_text(events: Events, span_id: str) -> dict[int, str]:
    """The deltas of one model request put back together, per response part."""
    parts: dict[int, str] = {}
    for event in events:
        if event["type"] == "stream.delta" and event["span_id"] == span_id:
            parts[event["part_index"]] = parts.get(event["part_index"], "") + event["delta"]
    return parts


def test_the_cli_lists_the_demos_it_ships() -> None:
    result = cli("demo", "--list", cwd=Path.cwd())
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.splitlines() == [f"{demo.name} - {demo.description}" for demo in DEMOS.values()]
    assert list(DEMOS) == NAMES
    assert result.stdout.isascii()


@pytest.mark.parametrize("name", NAMES)
def test_a_demo_recording_is_a_complete_valid_session_log(recorded: dict[str, tuple[Events, Path]], name: str) -> None:
    events, _ = recorded[name]
    assert_log_guarantees(events)
    started = events[0]
    assert (started["title"], started["stream"], started["include_content"]) == (DEMOS[name].title, True, True)
    spans = final_spans(events)
    assert root_of(spans)["status"] == "ok"
    # Introspection understood every agent and graph of the demo: nothing degraded.
    assert all(e["agent"]["warnings"] == [] and e["agent"]["source"] == "object" for e in events if "agent" in e)
    assert all(e["graph"]["warnings"] == [] for e in events if "graph" in e)
    # No span fell through the normaliser as an unknown kind.
    assert "other" not in {span["kind"] for span in spans.values()}


@pytest.mark.parametrize("name", NAMES)
def test_tokens_stream_and_add_up_to_each_response(recorded: dict[str, tuple[Events, Path]], name: str) -> None:
    events, _ = recorded[name]
    requests = [span for span in final_spans(events).values() if span["kind"] == "model_request"]
    assert requests
    for request in requests:
        streamed = streamed_text(events, request["span_id"])
        (message,) = request["data"]["output_messages"]
        assert len(streamed) == len(message["parts"])
        for index, part in enumerate(message["parts"]):
            if part["type"] == "tool_call":
                assert json.loads(streamed[index]) == part["arguments"]
            else:
                assert streamed[index] == part["content"]


@pytest.mark.parametrize("name", NAMES)
def test_run_totals_are_the_sum_over_model_requests(recorded: dict[str, tuple[Events, Path]], name: str) -> None:
    """Each agent run reports its own requests only, so nothing is counted twice (spec 3.4)."""
    spans = final_spans(recorded[name][0])
    total = model_request_usage(spans)
    assert total[0] > 0
    assert total[1] > 0
    runs = [span for span in spans.values() if span["kind"] == "agent_run"]
    assert total == (
        sum(run["data"]["usage"]["input_tokens"] for run in runs),
        sum(run["data"]["usage"]["output_tokens"] for run in runs),
    )
    for run in runs:
        own = model_request_usage({s["span_id"]: s for s in children(spans, run, "model_request")})
        assert own == (run["data"]["usage"]["input_tokens"], run["data"]["usage"]["output_tokens"])


@pytest.mark.parametrize("name", NAMES)
def test_the_html_export_embeds_the_same_events(recorded: dict[str, tuple[Events, Path]], name: str) -> None:
    events, page = recorded[name]
    html = page.read_text(encoding="utf-8")
    marker = '<script id="pviz-boot" type="application/json">'
    assert html.count(marker) == 1
    boot = json.loads(html.split(marker, 1)[1].split("</script>", 1)[0])
    assert boot == {"mode": "embedded", "events": events}


def test_support_shows_parallel_tools_a_retry_a_delegation_and_structured_output(
    recorded: dict[str, tuple[Events, Path]],
) -> None:
    events, _ = recorded["support"]
    spans = final_spans(events)
    root = root_of(spans)
    assert (root["kind"], root["name"]) == ("agent_run", "triage")

    # Turn one: text, then three tool calls in a single response, each with its own sibling span.
    first, *_ = children(spans, root, "model_request")
    calls = tool_calls_of(first)
    assert [call["name"] for call in calls] == ["get_order", "recent_tickets", "search_kb"]
    tools = children(spans, root, "tool_call")
    by_call_id = {tool["data"]["call_id"]: tool for tool in tools}
    assert all(by_call_id[call["id"]]["data"]["arguments"] == call["arguments"] for call in calls)

    # Exactly one retry: amber, not a failure. The same tool then succeeds.
    (retried,) = (tool for tool in tools if tool["data"]["retry"] is not None)
    assert (retried["name"], retried["status"]) == ("get_order", "error")
    assert retried["data"]["retry"] == {"cause": "model_retry", "attempt": 1, "max_retries": 2}
    assert retried["error"]["type"].endswith("ToolRetryError")
    assert [tool["status"] for tool in tools if tool is not retried] == ["ok"] * (len(tools) - 1)
    assert Counter(tool["name"] for tool in tools)["get_order"] == 2

    # The delegation: found in code first, then seen in the run, under the tool that made it.
    edges = [
        e["agent"]["delegates"] for e in events if e["type"] == "blueprint.agent" and e["agent"]["name"] == "triage"
    ]
    refunds_id = last_blueprints(events)["refunds"]["id"]
    assert edges[0] == [{"tool_name": "escalate_refund", "agent_id": refunds_id, "evidence": "object"}]
    assert edges[-1] == [{"tool_name": "escalate_refund", "agent_id": refunds_id, "evidence": "observed"}]
    (escalate,) = (tool for tool in tools if tool["name"] == "escalate_refund")
    (refunds,) = children(spans, escalate, "agent_run")
    assert (refunds["name"], refunds["status"]) == ("refunds", "ok")
    assert [tool["name"] for tool in children(spans, refunds, "tool_call")] == ["issue_refund"]

    # Structured output, and the blueprint knows its shape.
    assert set(root["data"]["output"]) == {"status", "refund_eur", "reply"}
    triage = last_blueprints(events)["triage"]
    assert triage["output"]["type_name"] == "Resolution"
    assert triage["output"]["tool_names"] == ["final_result"]
    assert {toolset["label"] for toolset in triage["toolsets"]} == {"Agent tools", "FunctionToolset 'kb'"}
    assert any(instruction["dynamic"] for instruction in triage["instructions"])


def test_research_shows_one_sub_agent_run_three_times_then_a_writer(recorded: dict[str, tuple[Events, Path]]) -> None:
    events, _ = recorded["research"]
    spans = final_spans(events)
    root = root_of(spans)
    assert (root["kind"], root["name"]) == ("agent_run", "planner")
    assert isinstance(root["data"]["output"], str)
    assert root["data"]["output"]

    # Three searches asked for in one response; each is a tool call with a searcher run inside.
    first, *_ = children(spans, root, "model_request")
    assert [call["name"] for call in tool_calls_of(first)] == ["search"] * 3
    assert first["data"]["output_messages"][0]["parts"][0]["type"] == "thinking"
    searches = [tool for tool in children(spans, root, "tool_call") if tool["name"] == "search"]
    assert len(searches) == 3
    for search in searches:
        (searcher,) = children(spans, search, "agent_run")
        assert (searcher["name"], searcher["status"]) == ("searcher", "ok")
        assert [tool["name"] for tool in children(spans, searcher, "tool_call")] == ["search_library"]
    (report,) = (tool for tool in children(spans, root, "tool_call") if tool["name"] == "write_report")
    (writer,) = children(spans, report, "agent_run")
    assert writer["name"] == "writer"
    assert Counter(span["name"] for span in spans.values() if span["kind"] == "agent_run") == {
        "planner": 1,
        "searcher": 3,
        "writer": 1,
    }

    blueprints = last_blueprints(events)
    assert sorted(blueprints) == ["planner", "searcher", "writer"]
    assert sorted(
        (edge["tool_name"], edge["agent_id"], edge["evidence"]) for edge in blueprints["planner"]["delegates"]
    ) == [
        ("search", blueprints["searcher"]["id"], "observed"),
        ("write_report", blueprints["writer"]["id"], "observed"),
    ]


def test_pipeline_shows_a_graph_run_with_a_loop_and_agents_inside_its_steps(
    recorded: dict[str, tuple[Events, Path]],
) -> None:
    events, _ = recorded["pipeline"]
    spans = final_spans(events)
    root = root_of(spans)
    (graph,) = (e["graph"] for e in events if e["type"] == "blueprint.graph")
    assert (root["kind"], root["name"], root["agent_name"]) == ("graph_run", "pipeline", None)
    assert root["data"]["graph_id"] == graph["id"]
    assert isinstance(root["data"]["inputs"], str)
    assert isinstance(root["data"]["output"], str)
    assert root["data"]["output"]

    # One node span per task, in the order the graph took them: the loop back to write, once.
    nodes = children(spans, root, "graph_node")
    assert [node["name"] for node in nodes] == [
        "write",
        "review",
        "decision",
        "write",
        "review",
        "decision",
        "publish",
        "__end__",
    ]
    assert all(node["data"]["graph_id"] == graph["id"] and node["status"] == "ok" for node in nodes)
    assert {node["name"] for node in nodes} <= {node["id"] for node in graph["nodes"]}

    # Agent runs nest under the step that made them.
    inside = {"write": "writer", "review": "critic"}
    for node in nodes:
        runs = children(spans, node, "agent_run")
        assert [run["name"] for run in runs] == ([inside[node["name"]]] if node["name"] in inside else [])
    assert children(spans, root, "agent_run") == []

    # The blueprint has the loop the run took.
    edges = {(edge["source"], edge["target"], edge["label"]) for edge in graph["edges"]}
    assert ("decision", "write", "needs work") in edges
    assert ("decision", "publish", "approved") in edges
    assert graph["mermaid"]
    assert sorted(last_blueprints(events)) == ["critic", "writer"]
