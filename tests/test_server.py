"""The viewer server over real sockets, on ephemeral ports (spec section 3.6)."""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from scenarios import PROMPT, Deps, build_team

import pydantic_ai_viz
from pydantic_ai_viz import Session, _compat, _server, analyze, serve
from pydantic_ai_viz._models import parse_envelope
from pydantic_ai_viz._server import ViewerServer, is_loopback, serve_session, server_of
from pydantic_ai_viz._session import active_session, blueprint_session

pytestmark = pytest.mark.usefixtures("placeholder_viewer")

TIMEOUT = 10.0


def agent_named(name: str = "greeter") -> Agent[None, str]:
    agent = Agent(TestModel(), name=name, instructions="Greet.")

    @agent.tool_plain
    def lookup_name(user_id: int) -> str:  # pyright: ignore[reportUnusedFunction]
        """Look up a name."""
        return "Ada"

    return agent


@pytest.fixture
def session() -> Iterator[Session]:
    with serve(agent_named(), port=0, title="Server test") as session:
        yield session


def address(session: Session) -> tuple[str, int]:
    assert session.url is not None
    parts = urlsplit(session.url)
    assert parts.hostname is not None
    assert parts.port is not None
    return parts.hostname, parts.port


class Response:
    def __init__(self, raw: http.client.HTTPResponse) -> None:
        self.status = raw.status
        self.headers = {name.lower(): value for name, value in raw.getheaders()}
        self.body = raw.read()

    def json(self) -> Any:
        return json.loads(self.body)


def request(
    session: Session, method: str, path: str, headers: dict[str, str] | None = None, body: bytes | None = None
) -> Response:
    connection = http.client.HTTPConnection(*address(session), timeout=TIMEOUT)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        return Response(connection.getresponse())
    finally:
        connection.close()


def get(session: Session, path: str, headers: dict[str, str] | None = None) -> Response:
    return request(session, "GET", path, headers)


class EventStream:
    """A minimal server-sent events client: enough to check framing, ids and order."""

    def __init__(self, session: Session, last_event_id: str | None = None) -> None:
        self._connection = http.client.HTTPConnection(*address(session), timeout=TIMEOUT)
        self._session_id = session.session_id
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        self._connection.request("GET", "/api/v1/events", headers=headers)
        self.response = self._connection.getresponse()
        self.headers = {name.lower(): value for name, value in self.response.getheaders()}
        self.preamble: list[str] = []
        """Every line that is not part of an event: the retry field and comments."""
        self.ended = False

    def read(self, count: int) -> list[dict[str, Any]]:
        """The next ``count`` events; fails the test if the stream ends or stalls first."""
        events = self.read_until_end(limit=count)
        assert len(events) == count, f"stream ended after {len(events)} of {count} events"
        return events

    def read_until_end(self, limit: int | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        event_id: str | None = None
        data: str | None = None
        while limit is None or len(events) < limit:
            line = self.response.readline().decode("utf-8")
            if not line:
                self.ended = True
                break
            line = line.rstrip("\n")
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                assert data is None, "one data line per event"
                data = line[6:]
            elif line == "":
                if data is not None:
                    envelope = json.loads(data)
                    assert event_id == event_id_of(self._session_id, envelope["seq"])
                    events.append(envelope)
                event_id = data = None
            else:
                self.preamble.append(line)
        return events

    def read_comment(self) -> str:
        """Skip to the next comment line."""
        while True:
            line = self.response.readline().decode("utf-8")
            assert line, "stream ended before a comment arrived"
            if line.startswith(":"):
                return line.rstrip("\n")

    def close(self) -> None:
        self.response.close()  # the response keeps the socket open until it is closed too
        self._connection.close()


def event_id_of(session: Session | str, seq: int) -> str:
    """The SSE id of an event: the session it belongs to, then its ``seq``."""
    return f"{session if isinstance(session, str) else session.session_id}:{seq}"


def wait_until(condition: Any, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def boot_of(html: str) -> Any:
    match = re.search(r'<script id="pviz-boot" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def can_connect(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=1).close()
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- endpoints


def test_serve_returns_an_open_session_with_a_loopback_url(session: Session) -> None:
    host, port = address(session)
    assert host == "127.0.0.1"
    assert port > 0
    assert session.url == f"http://127.0.0.1:{port}"
    assert active_session() is session
    assert [event["type"] for event in session.events] == ["session.started", "blueprint.agent"]
    assert session.title == "Server test"


def test_root_is_the_viewer_booted_in_live_mode(session: Session) -> None:
    response = get(session, "/")
    assert response.status == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    html = response.body.decode("utf-8")
    assert "UI not built" in html
    assert boot_of(html) == {
        "mode": "live",
        "events_url": "api/v1/events",
        "export_html_url": "api/v1/export.html",
        "export_jsonl_url": "api/v1/export.jsonl",
        "rerun_url": None,
    }
    # A query string (the viewer's own, or a cache buster) does not change the route.
    assert get(session, "/?live").body == response.body


def test_health(session: Session) -> None:
    response = get(session, "/api/v1/health")
    assert response.status == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "ok": True,
        "pviz": 1,
        "version": pydantic_ai_viz.__version__,
        "session_id": session.session_id,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/nope"),
        ("GET", "/api/v1"),
        ("GET", "/api/v2/health"),
        ("GET", "/index.html"),
        ("GET", "/api/v1/rerun"),
        ("POST", "/api/v1/rerun"),  # not in demo mode
        ("POST", "/api/v1/health"),
        ("POST", "/"),
        ("PUT", "/api/v1/health"),
        ("DELETE", "/api/v1/events"),
        ("OPTIONS", "/api/v1/events"),
    ],
)
def test_anything_else_is_a_json_404(session: Session, method: str, path: str) -> None:
    response = request(session, method, path)
    assert response.status == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "not found"}


@pytest.mark.parametrize(
    "path", ["/", "/api/v1/health", "/api/v1/export.jsonl", "/api/v1/export.html", "/api/v1/events", "/nope"]
)
def test_nothing_is_cacheable_and_there_are_no_cors_headers(session: Session, path: str) -> None:
    if path == "/api/v1/events":
        stream = EventStream(session)
        headers = stream.headers
        stream.close()
    else:
        headers = get(session, path, {"Origin": "http://evil.example"}).headers
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["connection"] == "close"
    assert headers["server"] == f"pydantic-ai-viz/{pydantic_ai_viz.__version__}"
    assert not [name for name in headers if name.startswith("access-control-")]


def test_head_sends_the_headers_without_a_body(session: Session) -> None:
    for path in ("/", "/api/v1/health", "/api/v1/events"):
        response = request(session, "HEAD", path)
        assert response.status == 200
        assert response.body == b""
    assert int(request(session, "HEAD", "/").headers["content-length"]) == len(get(session, "/").body)


def test_export_jsonl_is_the_log_as_a_download(session: Session) -> None:
    agent_named("other").run_sync("Hello")
    response = get(session, "/api/v1/export.jsonl")
    assert response.status == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    assert response.headers["content-disposition"] == 'attachment; filename="Server test.pviz.jsonl"'
    lines = response.body.decode("utf-8").split("\n")
    assert lines.pop() == ""
    for line in lines:
        parse_envelope(line)
    assert [json.loads(line) for line in lines] == session.events
    assert any('"agent_run"' in line for line in lines)


def test_export_html_is_a_single_file_export_of_the_log_as_a_download(session: Session) -> None:
    agent_named("other").run_sync("Hello")
    response = get(session, "/api/v1/export.html")
    assert response.status == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="Server test.html"'
    assert boot_of(response.body.decode("utf-8")) == {"mode": "embedded", "events": session.events}


def test_a_title_cannot_inject_into_the_download_header() -> None:
    with serve(port=0, title='x"\r\nSet-Cookie: a=b') as session:
        headers = get(session, "/api/v1/export.jsonl").headers
    assert headers["content-disposition"] == 'attachment; filename="x_Set-Cookie_ a_b.pviz.jsonl"'
    assert "set-cookie" not in headers


# ---------------------------------------------------------------------- sse


def test_events_sends_the_whole_backlog_then_tails(session: Session) -> None:
    agent = agent_named("other")
    agent.run_sync("Hello")
    backlog = session.events
    assert len(backlog) > 8

    stream = EventStream(session)
    assert stream.response.status == 200
    assert stream.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert "content-length" not in stream.headers
    received = stream.read(len(backlog))
    assert stream.preamble == ["retry: 1000"]  # first thing on the wire, before any event
    assert received == backlog
    assert [event["seq"] for event in received] == list(range(len(backlog)))
    assert received[0]["type"] == "session.started"

    # Live from here on: a new run arrives while the connection stays open.
    agent.run_sync("Again")
    fresh = session.events[len(backlog) :]
    assert len(fresh) > 5
    assert stream.read(len(fresh)) == fresh
    for envelope in received + fresh:
        parse_envelope(json.dumps(envelope))
    stream.close()


@pytest.mark.anyio
async def test_a_streamed_run_arrives_live_and_in_order(session: Session) -> None:
    session.close()
    team = build_team()
    with serve(team.main, port=0, stream=True) as live:
        stream = EventStream(live)
        stream.read(len(live.events))
        before = len(live.events)
        await team.main.run(PROMPT, deps=Deps())
        fresh = live.events[before:]
        received = stream.read(len(fresh))
        stream.close()
    assert received == fresh
    assert {event["type"] for event in received} == {"span.started", "span.ended", "stream.delta", "blueprint.agent"}
    assert [event["seq"] for event in received] == list(range(before, before + len(fresh)))


def test_resume_with_last_event_id_sends_only_newer_events(session: Session) -> None:
    agent_named("other").run_sync("Hello")
    events = session.events
    middle = events[len(events) // 2]["seq"]

    stream = EventStream(session, last_event_id=event_id_of(session, middle))
    received = stream.read(len(events) - middle - 1)
    assert received == events[middle + 1 :]
    assert received[0]["seq"] == middle + 1
    stream.close()

    # Fully caught up: nothing is resent, the next thing on the stream is the next event.
    stream = EventStream(session, last_event_id=event_id_of(session, events[-1]["seq"]))
    session.register(agent_named("late"))
    (late,) = stream.read(1)
    assert late["seq"] == events[-1]["seq"] + 1
    assert late["agent"]["name"] == "late"
    stream.close()


# "\xb2" is a superscript two: a digit to str.isdigit(), but not a number to int().
@pytest.mark.parametrize(
    "last_event_id", ["999999", "1", "0" * 32 + ":1", ":1", "not-a-number", "-1", "", "\xb2", "9" * 5000]
)
def test_an_id_from_another_server_gets_everything_from_seq_0(
    session: Session, last_event_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    events = session.events
    stream = EventStream(session, last_event_id=last_event_id)
    assert stream.read(len(events)) == events
    stream.close()
    assert capsys.readouterr().err == ""  # no traceback on the terminal of the program being watched


def test_a_client_that_reconnects_after_a_restart_sees_the_new_session_from_the_start() -> None:
    first = serve(agent_named(), port=0)
    port = address(first)[1]
    for name in ("a", "b", "c"):
        first.register(agent_named(name))
    stream = EventStream(first)
    last_seen = stream.read(len(first.events))[-1]["seq"]
    first.close()
    assert stream.read_until_end() == []
    assert stream.ended  # the old server hung up, as a browser's EventSource would notice

    # The "restarted process": a new session on the same port. Its log is shorter than what the
    # client saw, then as long, then longer; the client's id is from another session every time.
    with serve(agent_named(), port=port) as second:
        assert address(second)[1] == port
        assert last_seen > second.events[-1]["seq"]
        for extra in ("a", "b", "c", "d", "e"):
            resumed = EventStream(second, last_event_id=event_id_of(first, last_seen))
            received = resumed.read(len(second.events))
            resumed.close()
            assert received == second.events
            assert received[0]["type"] == "session.started"
            assert received[0]["session_id"] == second.session_id != first.session_id
            second.register(agent_named(extra))
        assert second.events[-1]["seq"] > last_seen


def test_an_idle_stream_gets_ping_comments(session: Session) -> None:
    server = server_of(session)
    assert server is not None
    assert server.ping_interval == 15.0
    server.ping_interval = 0.05
    stream = EventStream(session)
    stream.read(len(session.events))
    assert stream.read_comment() == ": ping"
    assert stream.read_comment() == ": ping"
    # A ping is not an event: the next real event still follows with the next seq.
    session.register(agent_named("late"))
    (late,) = stream.read(1)
    assert late["type"] == "blueprint.agent"
    stream.close()


def test_a_client_that_goes_away_frees_its_subscription(session: Session) -> None:
    server = server_of(session)
    assert server is not None
    server.ping_interval = 0.02
    log = session._log  # pyright: ignore[reportPrivateUsage]
    streams = [EventStream(session) for _ in range(3)]
    for stream in streams:
        stream.read(len(session.events))
    assert log.subscriber_count == 3
    for stream in streams:
        stream.close()
    wait_until(lambda: log.subscriber_count == 0)
    assert get(session, "/api/v1/health").status == 200  # and the server is none the worse


def test_a_long_backlog_arrives_complete_and_in_order() -> None:
    with serve(port=0) as session:
        for index in range(1000):
            session.register(Agent(TestModel(), name=f"agent-{index}"))
        stream = EventStream(session)
        received = stream.read(1001)
        stream.close()
    assert [event["seq"] for event in received] == list(range(1001))
    assert received[-1]["agent"]["name"] == "agent-999"


def test_several_clients_each_get_every_event(session: Session) -> None:
    streams = [EventStream(session) for _ in range(4)]
    results: list[list[dict[str, Any]]] = [[] for _ in streams]
    agent = agent_named("other")
    for _ in range(3):
        agent.run_sync("Hello")
    total = len(session.events)

    def drain(index: int) -> None:
        results[index] = streams[index].read(total)

    threads = [threading.Thread(target=drain, args=(index,)) for index in range(len(streams))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)
    for stream in streams:
        stream.close()
    assert all(result == session.events for result in results)


# ----------------------------------------------------------------- security


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example", "localhost.evil.example", "10.0.0.5", ""]
)
@pytest.mark.parametrize("path", ["/", "/api/v1/health", "/api/v1/events", "/api/v1/export.jsonl", "/nope"])
def test_a_foreign_host_header_is_forbidden_everywhere(session: Session, host: str, path: str) -> None:
    response = get(session, path, {"Host": host})
    assert response.status == 403
    assert response.json() == {"error": "forbidden: the Host header is not a loopback name"}
    assert session._log.subscriber_count == 0  # pyright: ignore[reportPrivateUsage]


def test_post_from_a_foreign_host_is_forbidden_before_anything_runs(session: Session) -> None:
    calls: list[int] = []
    server = server_of(session)
    assert server is not None
    server.rerun = lambda: calls.append(1) is None
    assert request(session, "POST", "/api/v1/rerun", {"Host": "evil.example"}).status == 403
    assert calls == []


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "LOCALHOST:5173",
        "[::1]",
        "[::1]:8765",
        "127.0.0.1:1",
        "127.0.0.2:8765",
        "[0:0:0:0:0:0:0:1]",
    ],
)
def test_loopback_host_names_are_accepted_with_any_port(session: Session, host: str) -> None:
    # Any port: a dev proxy in front of the server forwards its own Host header.
    assert get(session, "/api/v1/health", {"Host": host}).status == 200


def test_a_server_on_another_loopback_address_answers_at_its_own_url() -> None:
    try:
        session = serve(port=0, host="127.0.0.2", instrument=False)
    except OSError:
        pytest.skip("127.0.0.2 cannot be bound here (macOS only routes 127.0.0.1 by default)")
    with session:
        assert session.url is not None
        assert session.url.startswith("http://127.0.0.2:")
        assert get(session, "/api/v1/health").status == 200  # the client sends Host: 127.0.0.2:<port>


def test_a_request_without_a_host_header_is_forbidden(session: Session) -> None:
    with socket.create_connection(address(session), timeout=TIMEOUT) as raw:
        raw.sendall(b"GET /api/v1/health HTTP/1.0\r\n\r\n")
        reply = b""
        while chunk := raw.recv(65536):
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 403 ")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.8.9.1", True),
        ("localhost", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.20", False),
        ("example.com", False),
        ("", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected


def test_a_non_loopback_host_warns_and_skips_the_host_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Binding a real external address would trip firewalls on a developer machine, so the
    # address stays 127.0.0.1 and only the classification is overridden.
    monkeypatch.setattr(_server, "is_loopback", lambda host: False)
    with pytest.warns(UserWarning, match="not a loopback address.*There is no authentication") as caught:
        session = serve(port=0)
    with session:
        assert caught[0].filename == __file__  # points at the caller's serve() line
        assert get(session, "/api/v1/health", {"Host": "my-laptop.lan:8765"}).status == 200


def test_the_default_host_does_not_warn(recwarn: pytest.WarningsRecorder) -> None:
    with serve(port=0):
        pass
    assert [str(warning.message) for warning in recwarn if "loopback" in str(warning.message)] == []


@pytest.mark.parametrize(
    ("host", "url_host"), [("0.0.0.0", "127.0.0.1"), ("::", "[::1]"), ("::1", "[::1]"), ("localhost", "localhost")]
)
def test_the_url_is_one_a_local_browser_can_open(monkeypatch: pytest.MonkeyPatch, host: str, url_host: str) -> None:
    server = ViewerServer(Session(instrument=False), host, 0)
    monkeypatch.setattr(ViewerServer, "port", 4321)
    assert server.url == f"http://{url_host}:4321"


def test_a_server_on_the_ipv6_wildcard_answers_at_its_own_url() -> None:
    # An IPv6 socket may or may not also take IPv4 connections (on Windows it does not), so the
    # address reported for "::" has to be the IPv6 loopback.
    try:
        with pytest.warns(UserWarning, match="not a loopback address"):
            session = serve(port=0, host="::", instrument=False)
    except OSError:
        pytest.skip("IPv6 is not available here")
    with session:
        assert session.url is not None
        assert session.url.startswith("http://[::1]:")
        assert get(session, "/api/v1/health").status == 200


# -------------------------------------------------------------------- rerun


class Scenario:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0

    def start(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.starts += 1
        return True


@pytest.fixture
def demo(session: Session) -> Scenario:
    scenario = Scenario()
    server = server_of(session)
    assert server is not None
    server.rerun = scenario.start
    return scenario


def test_rerun_is_accepted_then_refused_while_running(session: Session, demo: Scenario) -> None:
    assert boot_of(get(session, "/").body.decode())["rerun_url"] == "api/v1/rerun"

    accepted = request(session, "POST", "/api/v1/rerun")
    assert accepted.status == 202
    assert accepted.headers["content-type"] == "application/json"
    assert accepted.json() == {"accepted": True}
    assert demo.starts == 1

    refused = request(session, "POST", "/api/v1/rerun", body=b"{}")
    assert refused.status == 409
    assert refused.json() == {"accepted": False, "reason": "running"}
    assert demo.starts == 1

    demo.running = False
    assert request(session, "POST", "/api/v1/rerun").status == 202
    assert demo.starts == 2


def test_rerun_outside_demo_mode_is_a_404(session: Session) -> None:
    response = request(session, "POST", "/api/v1/rerun")
    assert response.status == 404
    assert response.json() == {"error": "not found"}


def test_rerun_requires_an_absent_or_same_origin_origin(session: Session, demo: Scenario) -> None:
    host, port = address(session)
    for origin in ("http://evil.example", "null", f"https://{host}:{port}", f"http://{host}:{port + 1}"):
        response = request(session, "POST", "/api/v1/rerun", {"Origin": origin})
        assert response.status == 403, origin
        assert response.json() == {"error": "forbidden: cross-origin request"}
    assert demo.starts == 0

    assert request(session, "POST", "/api/v1/rerun", {"Origin": f"http://{host}:{port}"}).status == 202
    demo.running = False
    # The page opened as http://localhost:PORT is same-origin with itself too.
    same = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
    assert request(session, "POST", "/api/v1/rerun", same).status == 202
    assert demo.starts == 2


def test_rerun_is_post_only(session: Session, demo: Scenario) -> None:
    for method in ("GET", "PUT", "DELETE"):
        assert request(session, method, "/api/v1/rerun").status == 404
    assert demo.starts == 0


@pytest.mark.parametrize("size", [70_000, 300_000, 3_000_000])
def test_an_oversized_request_body_is_refused(session: Session, demo: Scenario, size: int) -> None:
    # The refusal must arrive as a response. If the server closed with the body unread, the
    # close would be a reset, and on Windows the client then sees an aborted connection.
    # Past the server's discard bound (the last size) a reset is allowed; a 2xx never is.
    try:
        response = request(session, "POST", "/api/v1/rerun", body=b"x" * size)
    except OSError:
        assert size > 1_048_576
    else:
        assert response.status == 413
    assert demo.starts == 0


@pytest.mark.parametrize("length", ["\xb2", "9" * 5000, "-1", "ten", ""])
def test_a_content_length_that_is_not_a_number_is_a_400(
    session: Session, length: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.create_connection(address(session), timeout=TIMEOUT) as raw:
        head = f"GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\nContent-Length: {length}\r\n\r\n"
        raw.sendall(head.encode("latin-1"))
        reply = b""
        while chunk := raw.recv(65536):
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 400 ")
    assert reply.endswith(b'{"error": "bad Content-Length"}')
    assert capsys.readouterr().err == ""


# -------------------------------------------------------- ports and lifecycle


def test_a_busy_port_falls_back_to_a_free_one() -> None:
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        busy = squatter.getsockname()[1]
        with serve(port=busy) as session:
            port = address(session)[1]
            assert port not in (0, busy)
            assert get(session, "/api/v1/health").json()["session_id"] == session.session_id


def test_two_servers_asked_for_the_same_port_do_not_share_it() -> None:
    with serve(port=0) as first, serve(port=address(first)[1], instrument=False) as second:
        assert address(first)[1] != address(second)[1]
        assert get(first, "/api/v1/health").json()["session_id"] == first.session_id
        assert get(second, "/api/v1/health").json()["session_id"] == second.session_id


def test_close_ends_open_streams_frees_the_port_and_stops_the_thread() -> None:
    before = _compat.instrumentation_default()
    session = serve(agent_named(), port=0)
    host, port = address(session)
    stream = EventStream(session)
    stream.read(len(session.events))
    assert any(thread.name == "pydantic-ai-viz-server" for thread in threading.enumerate())

    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 2

    assert stream.read_until_end() == []  # a clean end of stream, not a reset or a hang
    assert stream.ended
    stream.close()
    assert not can_connect(host, port)
    wait_until(lambda: not any(thread.name == "pydantic-ai-viz-server" for thread in threading.enumerate()))
    assert session.closed
    assert active_session() is None
    assert _compat.instrumentation_default() is before
    # What was captured stays readable, and the address is still reported.
    assert session.url == f"http://{host}:{port}"
    assert [event["type"] for event in session.events] == ["session.started", "blueprint.agent"]

    session.close()  # idempotent
    # The port can be bound again at once.
    with serve(port=port, instrument=False) as again:
        assert address(again)[1] == port


def test_events_logged_just_before_close_still_reach_the_client(session: Session) -> None:
    stream = EventStream(session)
    for index in range(50):
        session.register(Agent(TestModel(), name=f"agent-{index}"))
    total = len(session.events)
    session.close()
    assert len(stream.read_until_end()) == total
    stream.close()


def test_a_session_from_record_has_no_url_and_closes_without_a_server() -> None:
    with pydantic_ai_viz.record() as session:
        assert session.url is None
        assert server_of(session) is None
    assert session.closed


def test_serve_closes_the_session_when_registration_or_binding_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(TypeError, match="register\\(\\) expects"):
        serve("not an agent", port=0)  # pyright: ignore[reportArgumentType]
    assert active_session() is None

    def refuse(self: ViewerServer) -> None:
        raise OSError("cannot bind")

    with monkeypatch.context() as patch:
        patch.setattr(ViewerServer, "start", refuse)
        with pytest.raises(OSError, match="cannot bind"):
            serve(port=0)
    assert active_session() is None
    with serve(port=0, instrument=True):  # the failed attempts left no instrumenting session behind
        pass


def test_serve_session_starts_the_viewer_for_a_session_that_is_already_open(tmp_path: Path) -> None:
    source = tmp_path / "agents.py"
    source.write_text("from pydantic_ai import Agent\nreader = Agent('test', name='reader')\n", encoding="utf-8")
    opened = blueprint_session(analyze(source))
    assert opened.url is None
    session = serve_session(opened, port=0)
    assert session is opened
    with session:
        assert server_of(session) is not None
        assert get(session, "/api/v1/health").status == 200
        lines = get(session, "/api/v1/export.jsonl").body.decode("utf-8").split("\n")[:-1]
        started, reader = (json.loads(line) for line in lines)
        assert (started["mode"], started["title"]) == ("blueprint", "agents")
        assert (reader["agent"]["name"], reader["agent"]["source"]) == ("reader", "code")
    assert session.closed
    assert active_session() is None


def test_serve_session_closes_the_session_when_the_server_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: ViewerServer) -> None:
        raise OSError("cannot bind")

    monkeypatch.setattr(ViewerServer, "start", refuse)
    opened = blueprint_session()
    with pytest.raises(OSError, match="cannot bind"):
        serve_session(opened, port=0)
    assert opened.closed


def test_serve_takes_an_analysis_and_still_captures_runs(tmp_path: Path) -> None:
    source = tmp_path / "agents.py"
    source.write_text("from pydantic_ai import Agent\ngreeter = Agent('test', name='greeter')\n", encoding="utf-8")
    with serve(analyze(source), port=0) as session:
        agent_named("greeter").run_sync("Hello")
        events = session.events
    assert (events[0]["mode"], events[0]["title"]) == ("capture", "agents")
    blueprints = [event["agent"] for event in events if event["type"] == "blueprint.agent"]
    assert {(b["id"], b["name"], b["source"]) for b in blueprints} == {("a1", "greeter", "code")}
    # The run added the tool the live agent has; what was read from source is still the base.
    assert [(t["name"], t["origin"]) for t in blueprints[-1]["tools"]] == [("lookup_name", "observed")]
    assert any(event["type"] == "span.ended" and event["span"]["kind"] == "agent_run" for event in events)


def test_only_one_instrumenting_server_at_a_time(session: Session) -> None:
    with pytest.raises(RuntimeError, match="a pydantic-ai-viz session is already active; close it first"):
        serve(port=0)
    assert active_session() is session


def test_open_browser_opens_the_real_url(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(_server.webbrowser, "open", lambda url: opened.append(url) or True)
    with serve(port=0):
        assert opened == []
    with serve(port=0, open_browser=True) as session:
        assert opened == [session.url]


def test_a_machine_without_a_browser_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_browser(url: str) -> bool:
        raise RuntimeError("no display")

    monkeypatch.setattr(_server.webbrowser, "open", no_browser)
    with serve(port=0, open_browser=True) as session:
        assert get(session, "/api/v1/health").status == 200


def test_wait_returns_on_ctrl_c_and_leaves_the_session_open(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    naps: list[float] = []

    def interrupted_sleep(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("pydantic_ai_viz._session.time.sleep", interrupted_sleep)
    session.wait()
    assert naps == [0.2, 0.2, 0.2]  # short polls: a long blocking wait ignores Ctrl+C on Windows
    assert not session.closed
    assert get(session, "/api/v1/health").status == 200


def test_wait_returns_when_the_session_is_closed_from_another_thread(session: Session) -> None:
    waiter = threading.Thread(target=session.wait)
    waiter.start()
    time.sleep(0.05)
    assert waiter.is_alive()
    session.close()
    waiter.join(TIMEOUT)
    assert not waiter.is_alive()


def test_serve_defaults_match_the_documented_signature() -> None:
    import inspect

    parameters = inspect.signature(serve).parameters
    assert {name: parameter.default for name, parameter in parameters.items() if name != "targets"} == {
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": False,
        "include_content": True,
        "stream": False,
        "instrument": True,
        "title": None,
        "max_events": 50_000,
    }
