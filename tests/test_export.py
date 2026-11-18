from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_graph import GraphBuilder, StepContext
from scenarios import PROMPT, Deps, build_team

import pydantic_ai_viz
from pydantic_ai_viz import _compat, _export, export_html, record
from pydantic_ai_viz._models import parse_envelope
from pydantic_ai_viz._session import active_session

LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
HOSTILE = f"</script><script>alert(1)</script> <!-- & --> ]]> {LINE_SEPARATOR}{PARAGRAPH_SEPARATOR} caf\xe9 \u2615"
"""Everything a tool result could contain that would break out of, or corrupt, a script element."""

BUILT_VIEWER = (
    "<!doctype html><html><head><title>built</title></head><body><div id='root'></div>"
    '<script id="pviz-boot" type="application/json">"__PVIZ_BOOT__"</script>'
    "<script>/* the app */</script></body></html>"
)


class _Page(HTMLParser):
    """Reads a page the way a browser would: where each script element really ends."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: dict[str | None, str] = {}
        self.text = ""
        self._script: str | None = None
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._in_script = True
            self._script = dict(attrs).get("id")
            self.scripts[self._script] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts[self._script] += data
        else:
            self.text += data


def parse_page(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    page.close()
    return page


def boot_of(html: str) -> Any:
    return json.loads(parse_page(html).scripts["pviz-boot"])


def hostile_agent() -> Agent[None, str]:
    agent = Agent(TestModel(), name="hostile", instructions=f"Instructions with {HOSTILE}")

    @agent.tool_plain
    def leak() -> str:  # pyright: ignore[reportUnusedFunction]
        """Returns markup."""
        return HOSTILE

    return agent


def tiny_graph() -> Any:
    builder = GraphBuilder(name="tiny", input_type=str, output_type=str)

    @builder.step
    async def shout(ctx: StepContext[None, None, str]) -> str:
        return ctx.inputs.upper()

    builder.add(builder.edge_from(builder.start_node).to(shout), builder.edge_from(shout).to(builder.end_node))
    return builder.build()


@pytest.fixture
def built_viewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "static" / "viewer.html"
    path.parent.mkdir()
    path.write_text(BUILT_VIEWER, encoding="utf-8")
    monkeypatch.setattr(_export, "VIEWER_PATH", path)
    return path


# ----------------------------------------------------------------- recordings


@pytest.mark.anyio
async def test_a_recording_round_trips_through_the_wire_models(tmp_path: Path) -> None:
    team = build_team()
    with record(team.main, stream=True, title="Caf\xe9 run") as session:
        await team.main.run(PROMPT, deps=Deps())
        path = session.save(tmp_path / "run.pviz.jsonl")
        events = session.events

    assert path == tmp_path / "run.pviz.jsonl"
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    lines = raw.decode("utf-8").split(chr(10))[:-1]
    assert len(lines) == len(events) == 49
    # Strict, closed models: nothing extra, nothing missing, on any line.
    parsed = [parse_envelope(line) for line in lines]
    assert parsed[0].type == "session.started"
    assert parsed[0].title == "Caf\xe9 run"
    assert "Caf\xe9 run" in lines[0]  # written as UTF-8, not as an escape
    assert {envelope.type for envelope in parsed} == {
        "session.started",
        "blueprint.agent",
        "span.started",
        "span.ended",
        "stream.delta",
    }
    assert [envelope.seq for envelope in parsed] == list(range(len(events)))
    # Models back to JSON give the lines back: the round trip loses nothing.
    assert [envelope.model_dump(mode="json") for envelope in parsed] == [json.loads(line) for line in lines]
    assert _export.read_jsonl(path) == events


def test_save_and_export_still_work_after_close(tmp_path: Path) -> None:
    agent = hostile_agent()
    with record(agent) as session:
        agent.run_sync("go")
    assert session.closed
    saved = session.save(str(tmp_path / "late.pviz.jsonl"))
    page = session.export_html(str(tmp_path / "late.html"))
    assert isinstance(saved, Path)
    assert isinstance(page, Path)
    assert _export.read_jsonl(saved) == session.events
    assert boot_of(page.read_text(encoding="utf-8")) == {"mode": "embedded", "events": session.events}


def test_save_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "run.pviz.jsonl"
    path.write_text("stale\n" * 500, encoding="utf-8")
    with record(Agent(TestModel(), name="a")) as session:
        session.save(path)
    assert [event["type"] for event in _export.read_jsonl(path)] == ["session.started", "blueprint.agent"]


def test_a_lone_surrogate_does_not_fail_the_export(tmp_path: Path) -> None:
    events = [{"seq": 0, "ts": 1.0, "type": "session.started", "title": "broken \ud83d text"}]
    path = _export.write_jsonl(events, tmp_path / "s.pviz.jsonl")
    assert _export.read_jsonl(path)[0]["title"] == "broken ? text"
    page = _export.write_html(events, tmp_path / "s.html")
    assert boot_of(page.read_text(encoding="utf-8"))["events"][0]["title"] == "broken ? text"


def write_lines(path: Path, *lines: str) -> Path:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


STARTED = '{"seq":0,"ts":1.0,"type":"session.started"}'


def test_reading_tolerates_what_the_contract_says_consumers_ignore(tmp_path: Path) -> None:
    path = tmp_path / "future.pviz.jsonl"
    path.write_bytes(
        b"\xef\xbb\xbf"  # a byte order mark, as some Windows editors add
        + STARTED.encode()
        + b"\r\n\r\n"
        + b'{"seq":1,"ts":2.0,"type":"from.the.future","anything":[1,2]}\n'
        + b'{"seq":2,"ts":3.0,"type":"blueprint.graph","graph":{},"extra":true}'  # no final newline
    )
    assert [event["type"] for event in _export.read_jsonl(path)] == [
        "session.started",
        "from.the.future",
        "blueprint.graph",
    ]


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ((STARTED, "{not json"), "line 2 is not valid JSON"),
        ((STARTED, "", "[1, 2]"), "line 3 is not a pviz event"),
        ((STARTED, '{"seq": 1}'), "line 2 is not a pviz event"),
        (('{"seq":0,"ts":1.0,"type":"span.started"}',), "first line must be a 'session.started' event"),
        ((), "not a pviz recording"),
    ],
)
def test_reading_something_else_names_the_file_and_the_line(
    tmp_path: Path, lines: tuple[str, ...], message: str
) -> None:
    path = write_lines(tmp_path / "bad.pviz.jsonl", *lines)
    with pytest.raises(ValueError, match=message) as exc_info:
        _export.read_jsonl(path)
    assert str(path) in str(exc_info.value)


def test_reading_a_missing_file_is_an_os_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _export.read_jsonl(tmp_path / "nope.pviz.jsonl")


# ---------------------------------------------------------------- html export


@pytest.mark.usefixtures("built_viewer")
def test_html_export_embeds_the_log_with_markup_in_tool_results_escaped(tmp_path: Path) -> None:
    agent = hostile_agent()
    with record(agent) as session:
        agent.run_sync("go")
        page = session.export_html(tmp_path / "run.html")
        events = session.events

    # The hostile text really is in the log, in a tool result and in the instructions.
    results = [
        e["span"]["data"]["result"] for e in events if e["type"] == "span.ended" and e["span"]["kind"] == "tool_call"
    ]
    assert results == [HOSTILE]

    html = page.read_text(encoding="utf-8")
    start = html.index('<script id="pviz-boot" type="application/json">') + len(
        '<script id="pviz-boot" type="application/json">'
    )
    payload = html[start : html.index("</script>", start)]
    for unsafe in ("<", ">", "&", LINE_SEPARATOR, PARAGRAPH_SEPARATOR):
        assert unsafe not in payload
    assert "\\u003c/script\\u003e" in payload
    assert "\\u003c!--" in payload
    assert "caf\xe9 \u2615" in payload  # other non-ASCII text is left readable
    # The escapes are JSON escapes, so the data is unchanged ...
    assert json.loads(payload) == {"mode": "embedded", "events": events}
    # ... and an HTML parser sees the same two script elements the template has, no injected third.
    parsed = parse_page(html)
    assert list(parsed.scripts) == ["pviz-boot", None]
    assert json.loads(parsed.scripts["pviz-boot"]) == {"mode": "embedded", "events": events}
    assert parsed.scripts[None] == "/* the app */"
    assert html.count("__PVIZ_BOOT__") == 0
    assert html.startswith("<!doctype html><html><head><title>built</title>")


def test_only_the_first_boot_token_is_replaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "viewer.html"
    path.write_text(BUILT_VIEWER + '<!-- "__PVIZ_BOOT__" -->', encoding="utf-8")
    monkeypatch.setattr(_export, "VIEWER_PATH", path)
    html = _export.render_viewer({"mode": "embedded", "events": []})
    assert html.count('"__PVIZ_BOOT__"') == 1
    assert html.endswith('<!-- "__PVIZ_BOOT__" -->')
    assert boot_of(html) == {"mode": "embedded", "events": []}


@pytest.mark.usefixtures("placeholder_viewer")
def test_the_placeholder_viewer_is_used_when_the_built_one_is_missing(tmp_path: Path) -> None:
    assert _export.viewer_template() == _export.PLACEHOLDER_VIEWER
    agent = hostile_agent()
    with record(agent) as session:
        agent.run_sync("go")
        html = session.export_html(tmp_path / "run.html").read_text(encoding="utf-8")
        events = session.events
    page = parse_page(html)
    assert "UI not built. Run: pnpm --dir web install && pnpm --dir web build" in page.text
    assert list(page.scripts) == ["pviz-boot"]
    assert json.loads(page.scripts["pviz-boot"]) == {"mode": "embedded", "events": events}


def test_the_placeholder_has_exactly_one_boot_slot_and_loads_nothing() -> None:
    placeholder = _export.PLACEHOLDER_VIEWER
    assert placeholder.count(_export.BOOT_TOKEN) == 1
    assert placeholder.count("__PVIZ_BOOT__") == 1
    assert '<script id="pviz-boot" type="application/json">"__PVIZ_BOOT__"</script>' in placeholder
    assert "http" not in placeholder  # no network: no CDN, no fonts
    assert placeholder.isascii()
    # Unfilled, the slot parses to a plain string: the "unconfigured" signal the frontend looks for.
    assert boot_of(placeholder) == "__PVIZ_BOOT__"


def test_a_built_viewer_without_the_slot_falls_back_to_the_placeholder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "viewer.html"
    path.write_text("<html><body>a truncated build</body></html>", encoding="utf-8")
    monkeypatch.setattr(_export, "VIEWER_PATH", path)
    assert _export.viewer_template() == _export.PLACEHOLDER_VIEWER


def test_the_built_viewer_is_looked_up_inside_the_package() -> None:
    package = Path(pydantic_ai_viz.__file__).parent
    assert package / "_static" / "viewer.html" == _export.VIEWER_PATH


# ------------------------------------------------------- export_html(source)


@pytest.mark.usefixtures("placeholder_viewer")
def test_export_html_from_a_session_and_from_a_recording_agree(tmp_path: Path) -> None:
    agent = hostile_agent()
    with record(agent) as session:
        agent.run_sync("go")
        recording = session.save(tmp_path / "run.pviz.jsonl")
        from_session = export_html(session, tmp_path / "a.html")
    from_path = export_html(recording, tmp_path / "b.html")
    from_str = export_html(str(recording), str(tmp_path / "c.html"))
    assert from_session == tmp_path / "a.html"
    assert from_session.read_bytes() == from_path.read_bytes() == from_str.read_bytes()
    assert from_session.read_bytes() == session.export_html(tmp_path / "d.html").read_bytes()


@pytest.mark.usefixtures("placeholder_viewer")
def test_export_html_from_a_bare_agent_is_blueprint_only_and_touches_nothing_global(tmp_path: Path) -> None:
    team = build_team()
    before = _compat.instrumentation_default()
    page = export_html(team.main, tmp_path / "blueprint.html")
    assert active_session() is None
    assert _compat.instrumentation_default() is before

    boot = boot_of(page.read_text(encoding="utf-8"))
    assert boot["mode"] == "embedded"
    for envelope in boot["events"]:
        parse_envelope(json.dumps(envelope))
    assert [event["type"] for event in boot["events"]] == ["session.started", "blueprint.agent", "blueprint.agent"]
    assert (boot["events"][0]["title"], boot["events"][0]["mode"]) == ("main", "blueprint")
    main, researcher = (event["agent"] for event in boot["events"][1:])
    assert (main["name"], researcher["name"]) == ("main", "researcher")
    assert main["delegates"] == [{"tool_name": "delegate_research", "agent_id": researcher["id"], "evidence": "object"}]


@pytest.mark.usefixtures("placeholder_viewer")
def test_export_html_from_a_bare_graph(tmp_path: Path) -> None:
    boot = boot_of(export_html(tiny_graph(), tmp_path / "graph.html").read_text(encoding="utf-8"))
    assert [event["type"] for event in boot["events"]] == ["session.started", "blueprint.graph"]
    assert boot["events"][1]["graph"]["name"] == "tiny"
    assert [node["id"] for node in boot["events"][1]["graph"]["nodes"]] == ["__start__", "shout", "__end__"]


def test_export_html_rejects_other_sources(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="export_html\\(\\) expects a Session, a recording path"):
        export_html(42, tmp_path / "x.html")  # pyright: ignore[reportArgumentType]
    assert not (tmp_path / "x.html").exists()
    with pytest.raises(FileNotFoundError):
        export_html(tmp_path / "missing.pviz.jsonl", tmp_path / "x.html")
    not_a_recording = write_lines(tmp_path / "notes.txt", "hello")
    with pytest.raises(ValueError, match="line 1 is not valid JSON"):
        export_html(not_a_recording, tmp_path / "x.html")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Support triage demo", "Support triage demo.pviz.jsonl"),
        ("pydantic-ai-viz", "pydantic-ai-viz.pviz.jsonl"),
        ('a/b\\c:"d"\r\nX-Evil: 1', "a_b_c_d_X-Evil_ 1.pviz.jsonl"),
        ("caf\xe9 \u2615", "caf.pviz.jsonl"),
        ("../..", "session.pviz.jsonl"),
        ("", "session.pviz.jsonl"),
    ],
)
def test_download_names_are_safe_in_a_header_and_on_disk(title: str, expected: str) -> None:
    assert _export.download_name(title, ".pviz.jsonl") == expected
