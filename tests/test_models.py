from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pydantic_ai_viz._models import (
    ENVELOPE_ADAPTER,
    AgentBlueprintEvent,
    AgentRunSpan,
    GraphBlueprintEvent,
    GraphNodeSpan,
    GraphRunSpan,
    ModelRequestSpan,
    SessionStarted,
    SpanEnded,
    SpanStarted,
    StreamDelta,
    ToolCallSpan,
    envelope_json_schema,
    parse_envelope,
    to_wire,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "spec_example.pviz.jsonl"
SCHEMA = ROOT / "schema" / "pviz.schema.json"


def _lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _line(index: int) -> dict[str, Any]:
    return json.loads(_lines()[index])


def test_spec_example_parses_to_the_expected_event_types() -> None:
    events = [parse_envelope(line) for line in _lines()]
    assert [type(e) for e in events] == [
        SessionStarted,
        AgentBlueprintEvent,
        GraphBlueprintEvent,
        SpanStarted,
        SpanStarted,
        StreamDelta,
        StreamDelta,
        SpanEnded,
        SpanStarted,
        SpanEnded,
        SpanEnded,
        SpanStarted,
        SpanStarted,
    ]
    assert [e.seq for e in events] == list(range(13))
    spans = [e.span for e in events if isinstance(e, SpanStarted | SpanEnded)]
    assert [type(s) for s in spans] == [
        AgentRunSpan,
        ModelRequestSpan,
        ModelRequestSpan,
        ToolCallSpan,
        ToolCallSpan,
        AgentRunSpan,
        GraphRunSpan,
        GraphNodeSpan,
    ]


def test_spec_example_round_trips_without_adding_or_dropping_fields() -> None:
    for line in _lines():
        assert to_wire(parse_envelope(line)) == json.loads(line)


def test_python_dicts_validate_like_json() -> None:
    for line in _lines():
        assert ENVELOPE_ADAPTER.validate_python(json.loads(line)) == parse_envelope(line)


def _paths(value: Any, prefix: tuple[str | int, ...] = ()) -> list[tuple[str | int, ...]]:
    """Every dict-key path in a JSON value, skipping free-form ``Json`` fields."""
    free_form = {
        "attributes",
        "parameters_schema",
        "return_schema",
        "json_schema",
        "model_settings",
        "details",
        "settings",
        "arguments",
        "result",
        "output",
        "inputs",
        "raw",
    }
    found: list[tuple[str | int, ...]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append((*prefix, key))
            event_data = key == "data" and prefix[-2:-1] == ("events",)  # a span's own ``data`` is not
            if (key not in free_form and not event_data) or prefix == ("agent",):
                found.extend(_paths(child, (*prefix, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_paths(child, (*prefix, index)))
    return found


def _delete(value: Any, path: tuple[str | int, ...]) -> None:
    for step in path[:-1]:
        value = value[step]
    del value[path[-1]]


def _insert(value: Any, path: tuple[str | int, ...]) -> None:
    for step in path[:-1]:
        value = value[step]
    value["surprise"] = 1


def test_every_field_is_required() -> None:
    checked = 0
    for index in range(len(_lines())):
        original = _line(index)
        for path in _paths(original):
            broken = copy.deepcopy(original)
            _delete(broken, path)
            with pytest.raises(ValidationError):
                parse_envelope(json.dumps(broken))
            checked += 1
    assert checked > 300


def test_unknown_fields_are_rejected_at_every_level() -> None:
    checked = 0
    for index in range(len(_lines())):
        original = _line(index)
        for path in [("seq",), *_paths(original)]:
            broken = copy.deepcopy(original)
            _insert(broken, path)
            with pytest.raises(ValidationError):
                parse_envelope(json.dumps(broken))
            checked += 1
    assert checked > 300


@pytest.mark.parametrize(
    ("index", "path", "value"),
    [
        (0, ("seq",), "0"),
        (0, ("seq",), -1),
        (0, ("ts",), "1795046400000.0"),
        (0, ("pviz",), 2),
        (0, ("session_id",), "not-hex"),
        (0, ("include_content",), 1),
        (0, ("type",), "session.stopped"),
        (0, ("mode",), "view"),
        (0, ("notices",), "one notice"),
        (1, ("agent", "source"), "guessed"),
        (1, ("agent", "source"), "static"),
        (1, ("agent", "location"), {"file": "agents.py", "line": 0}),
        (1, ("agent", "location"), {"file": "agents.py"}),
        (1, ("agent", "tools", 0, "origin"), "static"),
        (1, ("agent", "settings", "expressions"), {"tool_retries": 3}),
        (2, ("graph", "source"), "observed"),
        (1, ("agent", "label"), ""),
        (1, ("agent", "toolsets", 0, "kind"), "magic"),
        (1, ("agent", "tools", 0, "kind"), "builtin"),
        (1, ("agent", "delegates", 0, "evidence"), "rumour"),
        (1, ("agent", "capabilities", 0, "category"), "magic"),
        (1, ("agent", "capabilities", 0, "package"), "pydantic_ai_slim"),
        (3, ("span", "trace_id"), "0AF7651916CD43DD8448EB211C80319C"),
        (3, ("span", "span_id"), "b7ad6b71"),
        (3, ("span", "status"), "done"),
        (3, ("span", "kind"), "agent"),
        (4, ("span", "parent_id"), "b7ad"),
        (5, ("part_kind",), "image"),
        (5, ("part_index",), -1),
        (10, ("span", "data", "messages", 0, "role"), "developer"),
        (10, ("span", "data", "messages", 0, "parts", 0, "type"), "image"),
        (10, ("span", "data", "usage", "input_tokens"), "212"),
    ],
)
def test_invalid_values_are_rejected(index: int, path: tuple[str | int, ...], value: Any) -> None:
    broken = _line(index)
    target: Any = broken
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        parse_envelope(json.dumps(broken))


def test_a_blueprint_read_from_code_carries_locations_and_expressions() -> None:
    line = _line(1)
    agent = line["agent"]
    location = {"file": "shop/agents.py", "line": 42}
    agent["source"] = "code"
    agent["location"] = location
    agent["model"] = {"id": None, "name": "get_model()", "provider": None, "offline": False}
    agent["model"] |= {"expression": "get_model()", "model_class": "OpenAIChatModel"}
    agent["model"]["env"] = {"variable": "MODEL", "default": "gpt-5.2"}
    agent["instructions"][0]["location"] = location
    agent["instructions"][1] |= {"function": None, "expression": "PROMPTS[role]", "location": location}
    agent["toolsets"][0]["location"] = location
    agent["toolsets"][1] |= {"opaque": True, "expression": "build_toolset(env)"}
    agent["tools"][0] |= {"origin": "code", "location": location, "library_version": "2.70.0"}
    agent["output"]["location"] = location
    agent["settings"]["expressions"] = {"tool_retries": "RETRIES", "model_settings.temperature": "TEMP"}
    agent["delegates"][0]["evidence"] = "code"
    agent["capabilities"].append(
        {
            "name": "MCP",
            "builtin": False,
            "category": "tools",
            "package": "pydantic_ai",
            "deferred": True,
            "details": {"effort": "high"},
            "expressions": {"url": "os.environ['MCP_URL']"},
            "toolsets": ["ts1"],
            "native_tools": ["mcp_server"],
            "capability_id": "docs",
            "stack": None,
            "skills": [],
            "hooks": [],
        }
    )
    assert to_wire(parse_envelope(json.dumps(line))) == line

    graph = _line(2)
    graph["graph"] |= {"source": "code", "location": location}
    graph["graph"]["nodes"][1]["location"] = location
    assert to_wire(parse_envelope(json.dumps(graph))) == graph


def _with_skills() -> dict[str, Any]:
    """The blueprint line, with a ``Skills`` capability that offers one skill and one a stack put there."""
    line = _line(1)
    skill = {
        "name": "house-style",
        "description": "How to write a briefing.",
        "when_to_use": None,
        "model_invocable": False,
        "path": "_skills/house-style/SKILL.md",
        "body": "# House style",
    }
    hook = {
        "point": "before_tool_execute",
        "function": "app.guard",
        "tools": ["delete_file"],
        "timeout": 5.0,
        "location": {"file": "app.py", "line": 12},
    }
    base = {"builtin": False, "deferred": False, "details": {}, "expressions": {}, "toolsets": [], "native_tools": []}
    line["agent"]["capabilities"] += [
        {**base, "name": "Skills", "category": "knowledge", "package": "pydantic_ai_harness"}
        | {"capability_id": "skills", "stack": None, "skills": [skill], "hooks": []},
        {**base, "name": "Shell", "category": "environment", "package": "pydantic_ai_harness"}
        | {"capability_id": None, "stack": "Coder", "skills": [], "hooks": []},
        {**base, "name": "Hooks", "category": "custom", "package": "pydantic_ai"}
        | {"capability_id": None, "stack": None, "skills": [], "hooks": [hook]},
    ]
    line["agent"]["settings"]["defaults"] = ["end_strategy", "tool_retries"]
    return line


def test_skills_hooks_stacks_and_default_settings_round_trip_and_every_field_of_a_skill_and_hook_is_required() -> None:
    line = _with_skills()
    assert to_wire(parse_envelope(json.dumps(line))) == line
    skill = line["agent"]["capabilities"][-3]["skills"][0]
    hook = line["agent"]["capabilities"][-1]["hooks"][0]
    for field, item in (("skills", skill), ("hooks", hook)):
        for key in list(item):
            broken = copy.deepcopy(line)
            index = -3 if field == "skills" else -1
            del broken["agent"]["capabilities"][index][field][0][key]
            with pytest.raises(ValidationError):
                parse_envelope(json.dumps(broken))
    schema = envelope_json_schema()["$defs"]
    assert sorted(schema["SkillInfo"]["required"]) == sorted(skill)
    assert sorted(schema["HookInfo"]["required"]) == sorted(hook)
    assert {"capability_id", "stack", "skills", "hooks"} <= set(schema["CapabilityInfo"]["required"])
    assert "defaults" in schema["AgentSettings"]["required"]


@pytest.mark.parametrize("mode", ["capture", "blueprint"])
def test_a_session_says_whether_runs_can_appear_in_it(mode: str) -> None:
    line = _line(0)
    line["mode"] = mode
    line["notices"] = ["skipped broken.py: invalid syntax on line 3"]
    assert to_wire(parse_envelope(json.dumps(line))) == line


def test_span_data_must_match_span_kind() -> None:
    broken = _line(3)
    broken["span"]["kind"] = "tool_call"
    with pytest.raises(ValidationError):
        parse_envelope(json.dumps(broken))

    other = _line(3)
    other["span"]["kind"] = "other"
    other["span"]["data"] = None
    assert to_wire(parse_envelope(json.dumps(other))) == other


def test_integer_times_are_accepted_and_serialised_as_floats() -> None:
    line = _line(0)
    line["ts"] = 1795046400000
    event = parse_envelope(json.dumps(line))
    assert isinstance(event.ts, float)
    assert '"ts":1795046400000.0' in event.model_dump_json()


def test_redacted_content_is_null_not_missing() -> None:
    line = _line(10)
    data = line["span"]["data"]
    data["instructions"] = None
    data["output"] = None
    for message in data["messages"]:
        for part in message["parts"]:
            for key in ("content", "arguments", "result"):
                if key in part:
                    part[key] = None
    assert to_wire(parse_envelope(json.dumps(line))) == line


def _schema_objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            found.append(node)
        for child in node.values():
            found.extend(_schema_objects(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_schema_objects(child))
    return found


def test_schema_marks_every_property_required_and_closed() -> None:
    objects = _schema_objects(envelope_json_schema())
    assert len(objects) >= 39
    for obj in objects:
        assert sorted(obj["required"]) == sorted(obj["properties"]), obj["title"]
        assert obj["additionalProperties"] is False, obj["title"]


def test_schema_is_plain_json_schema_2020_12() -> None:
    schema = envelope_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(schema["oneOf"]) == 6
    assert "discriminator" not in json.dumps(schema)


def test_committed_schema_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location("gen_schema", ROOT / "scripts" / "gen_schema.py")
    assert spec is not None
    assert spec.loader is not None
    gen_schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_schema)
    assert SCHEMA.read_text(encoding="utf-8").replace("\r\n", "\n") == gen_schema.render()
    assert gen_schema.main(["--check"]) == 0
