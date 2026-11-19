"""Helpers shared by the integration tests: real processes, real files, strict validation."""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pydantic_ai_viz._models import parse_envelope, to_wire

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"

Events = list[dict[str, Any]]
Spans = dict[str, dict[str, Any]]

STRICT = os.environ.get("PVIZ_REQUIRE_BROWSER_TESTS") == "1"
"""Set in the CI job that has the built viewer and a browser: there a missing piece is a failure."""


def need(condition: object, reason: str) -> None:
    """Skip the test when a build product is missing; fail instead where it must be present."""
    if condition:
        return
    if STRICT:
        pytest.fail(f"PVIZ_REQUIRE_BROWSER_TESTS=1 but {reason}")
    pytest.skip(reason)


def child_env(**extra: str) -> dict[str, str]:
    """The environment for a child process: unbuffered and UTF-8, so its output can be read line by line."""
    return {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYDANTIC_AI_NO_BANNER": "1", **extra}


def cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real command line in a child process, as a user would."""
    return subprocess.run(
        [sys.executable, "-m", "pydantic_ai_viz", *args],
        cwd=cwd,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def validated(lines: Iterable[str], source: object) -> Events:
    """Every line as a dict, after it passed the strict Python models and dumped back unchanged."""
    events: Events = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            model = parse_envelope(line)
        except ValidationError as exc:
            raise AssertionError(f"{source}: line {number} is not a valid pviz envelope\n{exc}") from None
        raw = json.loads(line)
        assert to_wire(model) == raw, f"{source}: line {number} does not round-trip through the models"
        events.append(raw)
    return events


def read_recording(path: Path) -> Events:
    return validated(path.read_text(encoding="utf-8").splitlines(), path.name)


# ------------------------------------------------------------------ reading a log


def final_spans(events: Events) -> Spans:
    """Each span in its last reported state, in the order the spans started."""
    spans: Spans = {}
    for event in events:
        if event["type"] in ("span.started", "span.ended"):
            spans[event["span"]["span_id"]] = event["span"]
    return spans


def finished_runs(events: Events) -> int:
    """Root spans that ended without an error."""
    return sum(1 for span in final_spans(events).values() if span["parent_id"] is None and span["status"] == "ok")


def children(spans: Spans, parent: dict[str, Any], kind: str | None = None) -> list[dict[str, Any]]:
    return [
        span
        for span in spans.values()
        if span["parent_id"] == parent["span_id"] and (kind is None or span["kind"] == kind)
    ]


def last_blueprints(events: Events) -> dict[str, dict[str, Any]]:
    """Agent blueprints by name, each in its last emitted state."""
    return {e["agent"]["name"]: e["agent"] for e in events if e["type"] == "blueprint.agent"}


def model_request_usage(spans: Spans) -> tuple[int, int]:
    """The run total the viewer shows: usage summed over model requests only."""
    usages = [span["data"]["usage"] for span in spans.values() if span["kind"] == "model_request"]
    return sum(u["input_tokens"] for u in usages), sum(u["output_tokens"] for u in usages)


def assert_log_guarantees(events: Events) -> None:
    """The ordering and identity guarantees of the wire contract (spec 3.3) on a finished log."""
    assert events[0]["type"] == "session.started"
    assert events[0]["pviz"] == 1
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert all(a["ts"] <= b["ts"] for a, b in itertools.pairwise(events))
    recording_runs = events[0]["mode"] == "capture"  # names matter only where runs are matched to them
    started: set[str] = set()
    ended: set[str] = set()
    blueprint_ids: dict[str, str] = {}
    for event in events:
        if event["type"] == "span.started":
            span = event["span"]
            assert span["span_id"] not in started
            assert (span["status"], span["end_ms"]) == ("running", None)
            # A parent is announced before its children.
            assert span["parent_id"] is None or span["parent_id"] in started
            started.add(span["span_id"])
        elif event["type"] == "span.ended":
            span = event["span"]
            assert span["span_id"] in started
            assert span["span_id"] not in ended
            assert span["status"] in ("ok", "error")
            assert span["end_ms"] >= span["start_ms"]
            ended.add(span["span_id"])
        elif event["type"] == "stream.delta":
            # A delta lies between its request's start and end.
            assert event["span_id"] in started
            assert event["span_id"] not in ended
        elif event["type"] == "blueprint.agent" and event["agent"]["name"] is not None and recording_runs:
            # At most one blueprint per name: a name that comes again comes under the same id.
            agent = event["agent"]
            assert blueprint_ids.setdefault(agent["name"], agent["id"]) == agent["id"]
    assert started == ended, "a span was left running"


# -------------------------------------------------------------- a serving process

_URL = re.compile(r"http://[^\s,)]+")


@contextlib.contextmanager
def serving(argv: list[str], *, cwd: Path) -> Iterator[tuple[str, list[str]]]:
    """Start a process that serves a viewer; yield its URL and its output lines so far.

    The URL is the first one the process prints (``... serving http://host:port ...``), which
    is how a person finds it too. The process is stopped on the way out.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    lines: list[str] = []
    found: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            match = _URL.search(line)
            if match:
                found.put(match.group(0).rstrip("/"))

    threading.Thread(target=pump, daemon=True).start()
    try:
        try:
            url = found.get(timeout=60)
        except queue.Empty:
            raise AssertionError(f"{argv} printed no serving URL within 60 s:\n" + "\n".join(lines)) from None
        yield url, lines
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def get(url: str) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()


def post(url: str) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def follow(url: str, until: Callable[[Events], bool], *, timeout: float = 60) -> Events:
    """Read the server's event stream from the start until ``until(events)`` holds."""
    deadline = time.monotonic() + timeout
    events: Events = []
    with urllib.request.urlopen(f"{url}/api/v1/events", timeout=timeout) as stream:
        for raw in stream:
            line = raw.decode("utf-8")
            if line.startswith("data: "):
                events.extend(validated([line[len("data: ") :]], "event stream"))
                if until(events):
                    return events
            if time.monotonic() > deadline:
                break
    raise AssertionError(f"the event stream did not get there in {timeout} s; {len(events)} events received")
