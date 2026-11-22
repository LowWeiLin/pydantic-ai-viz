"""Live end to end: ``pydantic-ai-viz demo`` as a child process, watched by a real client.

Two clients. The first is plain HTTP and runs everywhere: it follows the event stream the way
the viewer does. The second is the built viewer in a browser, driven by the Playwright spec
``web/e2e/live.spec.ts``; it needs the web build, node and a Chromium, and is skipped without
them unless ``PVIZ_REQUIRE_BROWSER_TESTS=1`` says they must be there (the CI e2e job).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from pydantic_ai_viz import __version__
from pydantic_ai_viz._export import VIEWER_PATH

from .support import (
    STRICT,
    WEB,
    assert_log_guarantees,
    child_env,
    final_spans,
    finished_runs,
    follow,
    get,
    need,
    post,
    serving,
    validated,
)

DEMO = [sys.executable, "-m", "pydantic_ai_viz", "demo", "--no-open", "--speed", "0", "--port", "0"]


def test_the_demo_plays_for_its_first_viewer_and_again_on_rerun(tmp_path: Path) -> None:
    with serving(DEMO, cwd=tmp_path) as (url, lines):
        assert lines[0] == f"pydantic-ai-viz {__version__} - serving {url} (Ctrl+C to stop)"
        status, headers, page = get(f"{url}/")
        assert (status, headers["cache-control"]) == (200, "no-store")
        # The page is told where its events are and that this session can run again.
        assert b'"mode":"live"' in page
        assert b'"rerun_url":"api/v1/rerun"' in page

        # Nobody is watching yet, so nothing has run: an export is not a viewer.
        _, _, before = get(f"{url}/api/v1/export.jsonl")
        assert finished_runs(validated(before.decode("utf-8").splitlines(), "export.jsonl")) == 0

        # The first viewer starts the scenario and sees it from its first span to its last.
        events = follow(url, lambda seen: finished_runs(seen) == 1)
        (root,) = (span for span in final_spans(events).values() if span["parent_id"] is None)
        assert (root["name"], root["status"]) == ("triage", "ok")
        assert any(event["type"] == "stream.delta" for event in events)

        # "Run again". The server frees the run a moment after its last span, so ask until it agrees.
        deadline = time.monotonic() + 15
        while (answer := post(f"{url}/api/v1/rerun"))[0] == 409:
            assert answer[1] == {"accepted": False, "reason": "running"}
            assert time.monotonic() < deadline, "the first run never ended on the server"
            time.sleep(0.05)
        assert answer == (202, {"accepted": True})

        # A viewer that connects now gets the whole session: both runs, one log.
        events = follow(url, lambda seen: finished_runs(seen) == 2)
        assert_log_guarantees(events)
        roots = [span for span in final_spans(events).values() if span["parent_id"] is None]
        assert [root["name"] for root in roots] == ["triage", "triage"]
        assert len({root["trace_id"] for root in roots}) == 2
        # Blueprints are session state: the second run does not re-announce what has not changed.
        second = next(
            e["seq"]
            for e in events
            if e["type"] == "span.started" and e["span"] is not None and e["span"]["span_id"] == roots[1]["span_id"]
        )
        assert [e["type"] for e in events if e["seq"] > second and e["type"].startswith("blueprint.")] == []


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_viewer_in_a_browser_follows_the_live_demo(tmp_path: Path) -> None:
    node = shutil.which("node")
    playwright = WEB / "node_modules" / "@playwright" / "test" / "cli.js"
    need(node and playwright.is_file(), "node or web/node_modules is missing (pnpm --dir web install)")
    need(
        VIEWER_PATH.is_file() and (WEB / "dist" / "viewer.html").is_file(),
        "the viewer is not built (pnpm --dir web build)",
    )
    assert node is not None

    def run_spec(attempt: int) -> tuple[int, str]:
        spec = [node, str(playwright), "test", "--project", "live", "--reporter", "list", "--retries", "0"]
        # Each attempt keeps its own traces: the next one does not clear them away.
        output_dir = f"test-results/live-attempt-{attempt}"
        with serving(DEMO, cwd=tmp_path) as (url, _):
            # Its own port for the fixture server the Playwright config starts, so this can run next to `pnpm e2e`.
            env = child_env(PVIZ_LIVE_URL=url, PVIZ_E2E_PORT=str(free_port()))
            # Playwright sets FORCE_COLOR for its children, and Node warns about a NO_COLOR next to it.
            env.pop("NO_COLOR", None)
            result = subprocess.run(
                [*spec, "--output", output_dir],
                cwd=WEB,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
        return result.returncode, result.stdout + result.stderr

    code, output = run_spec(1)
    if not STRICT and "playwright install" in output:
        need(False, "no Playwright browser is installed (pnpm --dir web exec playwright install chromium)")
    # The spec needs a demo nobody has watched yet: its first viewer starts the run. So a retry gets
    # a server of its own here, not Playwright's retry on the used one. One retry on CI, as the
    # Playwright config gives its other specs.
    if code != 0 and os.environ.get("CI"):
        print("the live spec failed, retrying on a fresh server:", output[-6000:], sep="\n")
        code, output = run_spec(2)
    assert code == 0, output[-6000:]
    assert "1 passed" in output, output[-6000:]
