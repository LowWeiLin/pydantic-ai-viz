"""The local viewer server (spec section 3.6) and ``serve()``.

Standard library only: a ``ThreadingHTTPServer`` in a daemon thread, one thread per
request, no asyncio. It serves the viewer page, the session log as a server-sent
event stream, and the two exports. Every response closes its connection, so the
only long-lived threads are event streams, and those end when the log is closed.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import socket
import sys
import threading
import warnings
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from ._export import download_name, dumps, encode, render_export, render_viewer, to_jsonl
from ._models import WIRE_VERSION
from ._session import DEFAULT_MAX_EVENTS, Session, Target, record
from ._version import __version__

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PING_INTERVAL = 15.0
"""Seconds of silence on an event stream before a ``: ping`` comment is sent."""

EVENTS_URL = "api/v1/events"
EXPORT_HTML_URL = "api/v1/export.html"
EXPORT_JSONL_URL = "api/v1/export.jsonl"
RERUN_URL = "api/v1/rerun"

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::"})
_MAX_REQUEST_BODY = 65_536
_BACKLOG_CHUNK = 256
"""Events per socket write when a stream starts with a long backlog."""

Rerun = Callable[[], bool]
"""Start the demo scenario again; ``False`` when it is still running."""


def is_loopback(host: str) -> bool:
    """Whether binding ``host`` keeps the server reachable from this machine only."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_name(header: str | None) -> str | None:
    """The name in a ``Host`` header, lower-cased and without its port; ``None`` if there is none."""
    if not header:
        return None
    value = header.strip().lower()
    if value.startswith("["):
        return value[: value.find("]") + 1] or None
    return value.rsplit(":", 1)[0] if ":" in value else value


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is in use, which would
    # hide "port busy" from us. Elsewhere it only skips the wait after a recent close.
    allow_reuse_address = os.name != "nt"
    viewer: ViewerServer

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that goes away mid-response is routine, not something to print a traceback for.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class _HTTPServer6(_HTTPServer):
    address_family = socket.AF_INET6


class _Handler(BaseHTTPRequestHandler):
    server: _HTTPServer  # pyright: ignore[reportIncompatibleVariableOverride]
    protocol_version = "HTTP/1.1"
    server_version = f"pydantic-ai-viz/{__version__}"
    sys_version = ""
    timeout = 60
    """Socket timeout: frees the thread of a client that connects and then says or reads nothing."""

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the user's terminal belongs to their program

    # -------------------------------------------------------------- routing

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    def _dispatch(self) -> None:
        viewer = self.server.viewer
        length = self.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > 0:
            # Unread request bytes would turn our close into a reset and could lose the response.
            if int(length) > _MAX_REQUEST_BODY:
                return self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body too large"})
            self.rfile.read(int(length))
        if viewer.loopback and _host_name(self.headers.get("Host")) not in _LOOPBACK_NAMES:
            # DNS rebinding: a web page can point its own host name at 127.0.0.1, but it
            # cannot make the browser send a loopback name in the Host header.
            return self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden: the Host header is not a loopback name"})

        path = urlsplit(self.path).path
        reading = self.command in ("GET", "HEAD")
        if reading and path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", encode(viewer.page()))
        elif reading and path == "/api/v1/health":
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "pviz": WIRE_VERSION, "version": __version__, "session_id": viewer.session.session_id},
            )
        elif reading and path == "/api/v1/events":
            self._events()
        elif reading and path == "/api/v1/export.jsonl":
            self._download("application/x-ndjson", ".pviz.jsonl", to_jsonl(viewer.session.events))
        elif reading and path == "/api/v1/export.html":
            self._download("text/html; charset=utf-8", ".html", render_export(viewer.session.events))
        elif self.command == "POST" and path == "/api/v1/rerun" and viewer.rerun is not None:
            self._rerun(viewer.rerun)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ------------------------------------------------------------ responses

    def _head(self, status: HTTPStatus, content_type: str, headers: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()

    def _send(self, status: HTTPStatus, content_type: str, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._head(status, content_type, {"Content-Length": str(len(body)), **(headers or {})})
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        self._send(status, "application/json", json.dumps(body).encode())

    def _download(self, content_type: str, extension: str, text: str) -> None:
        name = download_name(self.server.viewer.session.title, extension)
        self._send(HTTPStatus.OK, content_type, encode(text), {"Content-Disposition": f'attachment; filename="{name}"'})

    def _rerun(self, rerun: Rerun) -> None:
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() != f"http://{self.headers.get('Host', '').lower()}":
            # Another site's page may not start runs here; our own page and non-browser clients may.
            return self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden: cross-origin request"})
        if rerun():
            self._send_json(HTTPStatus.ACCEPTED, {"accepted": True})
        else:
            self._send_json(HTTPStatus.CONFLICT, {"accepted": False, "reason": "running"})

    def _events(self) -> None:
        """The session log as server-sent events: the retained backlog first, then live."""
        viewer = self.server.viewer
        last_event_id = self.headers.get("Last-Event-ID", "").strip()
        # No Content-Length: the stream ends when the connection does.
        self._head(HTTPStatus.OK, "text/event-stream; charset=utf-8", {"X-Accel-Buffering": "no"})
        if self.command == "HEAD":
            return
        with viewer.session._log.subscribe(int(last_event_id) if last_event_id.isdigit() else None) as stream:  # pyright: ignore[reportPrivateUsage]
            self.wfile.write(b"retry: 1000\n\n")
            while True:
                events = stream.get(timeout=viewer.ping_interval)
                if events:
                    for start in range(0, len(events), _BACKLOG_CHUNK):
                        frames = [
                            f"id: {envelope['seq']}\ndata: {dumps(envelope)}\n\n"
                            for envelope in events[start : start + _BACKLOG_CHUNK]
                        ]
                        self.wfile.write(encode("".join(frames)))
                elif stream.closed or viewer.stopping:
                    break  # over, and everything logged up to here has been sent
                else:
                    self.wfile.write(b": ping\n\n")


class ViewerServer:
    """One session's HTTP server. ``start()`` binds and serves in a daemon thread."""

    def __init__(self, session: Session, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.session = session
        self.host = host
        self.loopback = is_loopback(host)
        self.rerun: Rerun | None = None
        """Set in demo mode; enables ``POST /api/v1/rerun`` and the viewer's "Run again" button."""
        self.ping_interval = PING_INTERVAL
        self.stopping = False
        self._requested_port = port
        self._httpd: _HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind and start serving. A busy port falls back to a free one.

        Raises:
            OSError: If the address cannot be bound at all.
        """
        server_class = _HTTPServer6 if ":" in self.host else _HTTPServer
        try:
            httpd = server_class((self.host, self._requested_port), _Handler)
        except OSError:
            if self._requested_port == 0:
                raise
            httpd = server_class((self.host, 0), _Handler)
        httpd.viewer = self
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="pydantic-ai-viz-server", daemon=True
        )
        self._thread.start()

    @property
    def port(self) -> int:
        """The port really bound."""
        if self._httpd is None:
            raise RuntimeError("the server has not been started")
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        """Where a browser on this machine reaches the viewer."""
        host = DEFAULT_HOST if self.host in _WILDCARD_HOSTS else self.host
        return f"http://{f'[{host}]' if ':' in host else host}:{self.port}"

    def page(self) -> str:
        """The viewer page, told to follow this server's event stream."""
        return render_viewer(
            {
                "mode": "live",
                "events_url": EVENTS_URL,
                "export_html_url": EXPORT_HTML_URL,
                "export_jsonl_url": EXPORT_JSONL_URL,
                "rerun_url": None if self.rerun is None else RERUN_URL,
            }
        )

    def stop(self) -> None:
        """Stop accepting requests and release the port. Safe to call twice.

        Event streams end at their next wake-up: at once if the session log is
        closed first (as ``Session.close`` does), else within one ping interval.
        """
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        self.stopping = True
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def server_of(session: Session) -> ViewerServer | None:
    """The server ``serve()`` started for ``session``, if any."""
    server = session._server  # pyright: ignore[reportPrivateUsage]
    return server if isinstance(server, ViewerServer) else None


def wait_for_client(session: Session, timeout: float | None = None) -> bool:
    """Block until a viewer follows the session's event stream; ``False`` on timeout or close."""
    return session._log.wait_for_subscriber(timeout)  # pyright: ignore[reportPrivateUsage]


def open_in_browser(url: str) -> None:
    """Open ``url`` in the default browser; a machine without one is not an error."""
    with contextlib.suppress(Exception):
        webbrowser.open(url)


def serve(
    *targets: Target,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    include_content: bool = True,
    stream: bool = False,
    instrument: bool = True,
    title: str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> Session:
    """Capture agent runs in this process and show them live in a local viewer.

    Starts a small HTTP server in a background thread and returns at once. Every
    agent run that happens while the session is open appears in the viewer as it
    happens, whether or not the agent was passed here; passing it adds its full
    static blueprint before the first run.

    ```python
    import pydantic_ai_viz as pviz

    with pviz.serve(agent, open_browser=True) as session:
        print(session.url)
        agent.run_sync("Hello")
        session.wait()  # keep serving until Ctrl+C
    ```

    Args:
        *targets: Agents, graphs, or modules whose module-level agents and graphs
            are all registered.
        host: Address to bind. The default is reachable from this machine only.
            Any other address serves prompts and tool results to the network with
            no authentication, and a warning says so.
        port: Port to bind. If it is busy a free port is used instead; `0` always
            picks a free port. The address really used is `session.url`.
        open_browser: Open the viewer in the default browser.
        include_content: Capture prompts, tool arguments and results. With `False`
            the structure of each run is kept and the content is `null`.
        stream: Capture token deltas. This switches model requests of the registered
            agents to streaming, so a model that cannot stream will fail.
        instrument: Call `Agent.instrument_all` with this session's own tracer
            provider. With `False` nothing global is touched; add
            `session.span_processor` to your own provider instead.
        title: Session title, shown in the viewer and used for download file names.
            Defaults to the name of the first named target.
        max_events: Size of the event log before whole completed runs are dropped.

    Returns:
        The open session. Its `url` is the viewer's address.

    Raises:
        RuntimeError: If `instrument` is true and another instrumenting session is open.
        TypeError: If a target is not an agent, a graph or a module.
        OSError: If the server cannot bind `host` at all.
    """
    session = record(
        *targets,
        include_content=include_content,
        stream=stream,
        instrument=instrument,
        title=title,
        max_events=max_events,
    )
    try:
        server = ViewerServer(session, host, port)
        server.start()
    except BaseException:
        session.close()
        raise
    session._server = server  # pyright: ignore[reportPrivateUsage]
    session.url = server.url
    if not server.loopback:
        warnings.warn(
            f"pydantic-ai-viz is serving on {host!r}, which is not a loopback address: anyone who can reach "
            f"port {server.port} can read the captured prompts and tool results. There is no authentication.",
            stacklevel=2,
        )
    if open_browser:
        open_in_browser(server.url)
    return session
