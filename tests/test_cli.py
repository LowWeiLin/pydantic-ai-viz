"""The command line, driven through ``cli.main`` with a stub demo registry (spec section 4.6)."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import pydantic_ai_viz
from pydantic_ai_viz import Session, _compat, cli
from pydantic_ai_viz._export import read_jsonl
from pydantic_ai_viz._models import parse_envelope
from pydantic_ai_viz._session import active_session

pytestmark = pytest.mark.usefixtures("placeholder_viewer")

TIMEOUT = 10.0


# ------------------------------------------------------------- stub registry


@dataclass
class StubDemo:
    """Shaped like ``pydantic_ai_viz.demos.Demo``, with a TestModel agent and a record of how it was run."""

    name: str
    title: str
    description: str
    speeds: list[float] = field(default_factory=list[float])
    hold: threading.Event | None = None
    """When set, a run does not finish until the event is set."""
    fail: bool = False
    agent: Agent[None, str] = field(init=False)
    targets: tuple[Any, ...] = field(init=False)

    def __post_init__(self) -> None:
        self.agent = Agent(TestModel(), name=f"{self.name}-agent", instructions="Stub demo agent.")

        @self.agent.tool_plain
        def lookup(key: str) -> str:  # pyright: ignore[reportUnusedFunction]
            """Look something up."""
            return f"value of {key}"

        self.targets = (self.agent,)

    async def run(self, speed: float) -> object:
        self.speeds.append(speed)
        if self.fail:
            raise RuntimeError("the scenario broke")
        result = await self.agent.run("Go")
        while self.hold is not None and not self.hold.is_set():
            await asyncio.sleep(0.005)
        return result.output


@pytest.fixture
def demos(monkeypatch: pytest.MonkeyPatch) -> dict[str, StubDemo]:
    registry = {
        "alpha": StubDemo("alpha", "Alpha demo", "The first stub demo."),
        "beta": StubDemo("beta", "Beta demo", "The second stub demo, caf\xe9 style ☕."),
    }
    monkeypatch.setattr(cli, "_load_demos", lambda: registry)
    return registry


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the CLI asked the browser to open; no browser is started in tests."""
    urls: list[str] = []
    monkeypatch.setattr(cli, "open_in_browser", urls.append)
    return urls


Watch = Callable[[Session], None]


@pytest.fixture
def serving(monkeypatch: pytest.MonkeyPatch) -> Callable[[Watch], list[Session]]:
    """Replace "serve until Ctrl+C" with a callback that runs while the server is up, then returns."""

    def install(watch: Watch) -> list[Session]:
        served: list[Session] = []

        def wait(session: Session) -> None:
            served.append(session)
            assert not session.closed
            watch(session)

        monkeypatch.setattr(Session, "wait", wait)
        return served

    return install


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    # Rule for every command: whatever we print must survive a cp1252 console.
    assert captured.out.isascii()
    assert captured.err.isascii()
    return code, captured.out, captured.err


def fetch(session: Session, method: str, path: str) -> tuple[int, bytes]:
    assert session.url is not None
    connection = http.client.HTTPConnection(urlsplit(session.url).netloc, timeout=TIMEOUT)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def follow(session: Session, until: Callable[[list[dict[str, Any]]], bool]) -> list[dict[str, Any]]:
    """Connect to the event stream, as the viewer does, and read until ``until(events)`` holds."""
    assert session.url is not None
    connection = http.client.HTTPConnection(urlsplit(session.url).netloc, timeout=TIMEOUT)
    connection.request("GET", "/api/v1/events")
    response = connection.getresponse()
    events: list[dict[str, Any]] = []
    named = False
    try:
        while not until(events):
            line = response.readline().decode("utf-8")
            assert line, "the event stream ended early"
            if line.startswith("event: "):
                named = True  # an event with a name of its own (the backlog marker) is not an envelope
            elif line.strip() == "":
                named = False
            elif line.startswith("data: ") and not named:
                events.append(json.loads(line[6:]))
    finally:
        response.close()
        connection.close()
    return events


def finished_runs(events: list[dict[str, Any]]) -> int:
    return sum(e["type"] == "span.ended" and e["span"]["kind"] == "agent_run" for e in events)


def types_in(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").split("\n")[:-1]
    return {parse_envelope(line).type for line in lines}


def boot_of(path: Path) -> Any:
    match = re.search(
        r'<script id="pviz-boot" type="application/json">(.*?)</script>', path.read_text(encoding="utf-8"), re.DOTALL
    )
    assert match is not None
    return json.loads(match.group(1))


# -------------------------------------------------------------------- basics


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys)
    assert code == 0
    assert out.startswith("usage: pydantic-ai-viz")
    for command in ("demo", "view", "run", "open", "export"):
        assert f"\n    {command} " in out


@pytest.mark.parametrize("command", ["demo", "view", "run", "open", "export"])
def test_every_command_has_ascii_help(capsys: pytest.CaptureFixture[str], command: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([command, "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: pydantic-ai-viz {command}")
    assert out.isascii()


@pytest.mark.parametrize(
    "argv",
    [
        ["frobnicate"],
        ["view"],
        ["run"],
        ["open"],
        ["export", "x.pviz.jsonl"],  # -o is required
        ["demo", "--speed", "fast"],
        ["demo", "--port", "70000"],
        ["demo", "--port", "-1"],
        ["view", "x.py", "--bogus"],
    ],
)
def test_arguments_that_do_not_parse_exit_with_2(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "usage: pydantic-ai-viz" in err
    assert err.isascii()


def test_the_banner_is_suppressed_unless_the_user_chose_otherwise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo]
) -> None:
    monkeypatch.delenv("PYDANTIC_AI_NO_BANNER", raising=False)
    assert run_cli(capsys, "demo", "--list")[0] == 0
    assert os.environ["PYDANTIC_AI_NO_BANNER"] == "1"
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "0")
    assert run_cli(capsys, "demo", "--list")[0] == 0
    assert os.environ["PYDANTIC_AI_NO_BANNER"] == "0"


def test_python_dash_m_runs_the_same_program() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pydantic_ai_viz", "--version"], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"pydantic-ai-viz {pydantic_ai_viz.__version__}"


def test_an_unexpected_failure_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> Any:
        raise ImportError("demos are gone")

    monkeypatch.setattr(cli, "_load_demos", broken)
    code, out, err = run_cli(capsys, "demo", "--list")
    assert (code, out) == (1, "")
    assert err == "pydantic-ai-viz: error: ImportError: demos are gone\n"


# ---------------------------------------------------------------------- demo


def test_demo_list(capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo]) -> None:
    code, out, err = run_cli(capsys, "demo", "--list")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "alpha - The first stub demo.",
        "beta - The second stub demo, caf\\xe9 style \\u2615.",  # escaped, never mangled or fatal
    ]
    assert all(demo.speeds == [] for demo in demos.values())  # listing runs nothing
    assert active_session() is None


def test_demo_record_and_export_run_once_without_a_server(
    capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo], opened: list[str], tmp_path: Path
) -> None:
    before = _compat.instrumentation_default()
    recording, page = tmp_path / "out.pviz.jsonl", tmp_path / "out.html"
    code, out, err = run_cli(capsys, "demo", "beta", "--speed", "0", "--record", str(recording), "--export", str(page))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"Wrote recording {recording}", f"Wrote HTML export {page}"]
    assert "serving" not in out
    assert opened == []
    assert demos["beta"].speeds == [0.0]
    assert demos["alpha"].speeds == []

    # Every line is a valid envelope, and the run was captured with token streaming on.
    assert types_in(recording) == {"session.started", "blueprint.agent", "span.started", "span.ended", "stream.delta"}
    events = read_jsonl(recording)
    assert events[0]["title"] == "Beta demo"
    assert events[0]["stream"] is True
    assert events[1]["agent"]["name"] == "beta-agent"
    assert events[1]["agent"]["source"] == "object"
    assert finished_runs(events) == 1
    assert boot_of(page) == {"mode": "embedded", "events": events}
    # Nothing is left behind.
    assert active_session() is None
    assert _compat.instrumentation_default() is before


def test_demo_defaults_to_the_first_entry_and_speed_1(
    capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo], tmp_path: Path
) -> None:
    code, _, _ = run_cli(capsys, "demo", "--record", str(tmp_path / "a.pviz.jsonl"))
    assert code == 0
    assert demos["alpha"].speeds == [1.0]
    assert demos["beta"].speeds == []
    assert not (tmp_path / "a.html").exists()  # only what was asked for is written


def test_demo_usage_errors(capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo], tmp_path: Path) -> None:
    code, out, err = run_cli(capsys, "demo", "gamma", "--record", str(tmp_path / "x.pviz.jsonl"))
    assert (code, out) == (2, "")
    assert err == "pydantic-ai-viz: error: unknown demo 'gamma' (available: alpha, beta)\n"
    code, _, err = run_cli(capsys, "demo", "alpha", "--speed", "-1")
    assert code == 2
    assert err == "pydantic-ai-viz: error: --speed must be 0 or more\n"
    assert not list(tmp_path.iterdir())
    assert all(demo.speeds == [] for demo in demos.values())
    assert active_session() is None


def test_a_demo_that_needs_a_missing_package_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo]
) -> None:
    monkeypatch.setattr(cli, "_load_unavailable", lambda: {"gamma": "needs the gamma package"})
    code, out, err = run_cli(capsys, "demo", "gamma")
    assert (code, out) == (2, "")
    assert err == "pydantic-ai-viz: error: demo 'gamma' needs the gamma package\n"
    code, _, err = run_cli(capsys, "demo", "delta")
    assert (code, err) == (2, "pydantic-ai-viz: error: unknown demo 'delta' (available: alpha, beta)\n")


def test_demo_runtime_errors_exit_with_1_and_close_the_session(
    capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo], tmp_path: Path
) -> None:
    demos["alpha"].fail = True
    code, _, err = run_cli(capsys, "demo", "alpha", "--speed", "0", "--record", str(tmp_path / "x.pviz.jsonl"))
    assert code == 1
    assert err == "pydantic-ai-viz: error: RuntimeError: the scenario broke\n"
    assert active_session() is None

    demos["alpha"].fail = False
    code, _, err = run_cli(capsys, "demo", "--speed", "0", "--record", str(tmp_path / "no" / "such" / "dir.jsonl"))
    assert code == 1
    assert err.startswith("pydantic-ai-viz: error: ")
    assert "dir.jsonl" in err
    assert active_session() is None


def test_demo_serves_starts_on_the_first_viewer_and_reruns_on_request(
    capsys: pytest.CaptureFixture[str],
    demos: dict[str, StubDemo],
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
) -> None:
    demo = demos["alpha"]
    hold = demo.hold = threading.Event()
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        # The page was told it can rerun before the browser was pointed at it.
        seen["opened"] = list(opened)
        seen["boot"] = fetch(session, "GET", "/")[1]
        # Nobody is watching yet, so nothing has run.
        time.sleep(0.1)
        seen["speeds_before_a_viewer"] = list(demo.speeds)
        # The first viewer starts the scenario; it is held open, so a rerun is refused.
        first = follow(session, lambda events: any(e["type"] == "span.started" for e in events))
        seen["first_title"] = first[0]["title"]
        seen["while_running"] = fetch(session, "POST", "/api/v1/rerun")
        hold.set()
        follow(session, lambda events: finished_runs(events) == 1)
        deadline = time.monotonic() + TIMEOUT
        while (reply := fetch(session, "POST", "/api/v1/rerun"))[0] == 409 and time.monotonic() < deadline:
            time.sleep(0.01)  # the span has ended; the worker thread is a moment behind
        seen["after_finishing"] = reply
        seen["events"] = follow(session, lambda events: finished_runs(events) == 2)

    served = serving(watch)
    code, out, err = run_cli(capsys, "demo", "--speed", "4", "--port", "0")
    assert (code, err) == (0, "")
    (session,) = served
    assert out == f"pydantic-ai-viz {pydantic_ai_viz.__version__} - serving {session.url} (Ctrl+C to stop)\n"
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", session.url or "")
    assert seen["opened"] == [session.url]
    assert b'"rerun_url":"api/v1/rerun"' in seen["boot"]
    assert seen["speeds_before_a_viewer"] == []
    assert seen["first_title"] == "Alpha demo"
    assert seen["while_running"] == (409, b'{"accepted": false, "reason": "running"}')
    assert seen["after_finishing"] == (202, b'{"accepted": true}')
    assert demo.speeds == [4.0, 4.0]
    assert any(e["type"] == "stream.delta" for e in seen["events"])  # demos stream tokens
    assert len({e["span"]["trace_id"] for e in seen["events"] if e["type"] == "span.started"}) == 2
    # Leaving the command closes the session and the server.
    assert session.closed
    assert active_session() is None


def test_demo_no_open_and_a_failing_scenario_keeps_serving(
    capsys: pytest.CaptureFixture[str],
    demos: dict[str, StubDemo],
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
) -> None:
    demos["alpha"].fail = True
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        follow(session, lambda events: len(events) >= 2)
        deadline = time.monotonic() + TIMEOUT
        while (reply := fetch(session, "POST", "/api/v1/rerun"))[0] == 409 and time.monotonic() < deadline:
            time.sleep(0.01)
        seen["rerun"] = reply[0]
        # A third rerun is only accepted once the second run has failed and been reported.
        while fetch(session, "POST", "/api/v1/rerun")[0] == 409 and time.monotonic() < deadline:
            time.sleep(0.01)
        seen["health"] = fetch(session, "GET", "/api/v1/health")[0]

    serving(watch)
    code, out, err = run_cli(capsys, "demo", "--no-open", "--port", "0")
    assert code == 0
    assert opened == []
    assert "serving http://127.0.0.1:" in out
    assert seen == {"rerun": 202, "health": 200}
    assert err.count("RuntimeError: the scenario broke") >= 2  # each failed run is reported, none is fatal


def test_the_demos_package_is_imported_only_by_the_demo_command() -> None:
    probe = "import sys, pydantic_ai_viz.cli as cli; cli.main([]); print('pydantic_ai_viz.demos' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0
    assert result.stdout.splitlines()[-1] == "False"


# ---------------------------------------------------------------------- view

AGENTS = '''
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.function import FunctionModel


def never(messages, info):
    raise AssertionError("view must not call the model")


helper = Agent(FunctionModel(never), name="helper")
triage = Agent(FunctionModel(never), name="triage", instructions="Triage tickets.")
unnamed = Agent("openai:gpt-5.2", defer_model_check=True)


@triage.tool
async def ask_helper(ctx: RunContext[None], question: str) -> str:
    """Ask the helper."""
    return (await helper.run(question)).output


if __name__ == "__main__":
    raise SystemExit("view must not run the main block")
'''


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "viz_cli_agents.py").write_text(AGENTS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        del sys.modules[name]


def blueprint_labels(events: list[dict[str, Any]]) -> list[str]:
    return [e["agent"]["label"] for e in events if e["type"] == "blueprint.agent"]


READ_ONE_FILE = "Read 1 file without running any code: "


def test_view_reads_the_source_and_runs_none_of_it(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    # A file that cannot be imported: it needs a package that is not installed, and it fails when run.
    source = (
        "import a_package_that_is_not_installed\n"
        "from pathlib import Path\n"
        "Path('ran.txt').write_text('the file was executed')\n"
        "raise RuntimeError('boom at import')\n" + AGENTS
    )
    (project / "unimportable.py").write_text(source, encoding="utf-8")
    before = _compat.instrumentation_default()
    modules = set(sys.modules)

    code, out, err = run_cli(capsys, "view", "unimportable.py", "--export", "all.html")

    assert (code, out) == (0, "Wrote HTML export all.html\n")
    assert err == READ_ONE_FILE + "3 agents, 0 graphs\n"
    assert not (project / "ran.txt").exists()
    assert not {name for name in set(sys.modules) - modules if "unimportable" in name or "not_installed" in name}
    events = boot_of(project / "all.html")["events"]
    assert [e["type"] for e in events] == ["session.started", *["blueprint.agent"] * 3]
    assert (events[0]["title"], events[0]["mode"], events[0]["notices"]) == ("unimportable", "blueprint", [])
    helper, triage, unnamed = (e["agent"] for e in events[1:])
    assert [(a["label"], a["source"]) for a in (helper, triage, unnamed)] == [
        ("helper", "code"),
        ("triage", "code"),
        ("unnamed", "code"),
    ]
    assert triage["location"] == {"file": "unimportable.py", "line": 15}
    assert triage["instructions"][0]["text"] == "Triage tickets."
    assert triage["delegates"] == [{"tool_name": "ask_helper", "agent_id": "a1", "evidence": "code"}]
    assert [(t["name"], t["description"], t["origin"]) for t in triage["tools"]] == [
        ("ask_helper", "Ask the helper.", "code")
    ]
    assert unnamed["model"]["id"] == "openai:gpt-5.2"
    # A model built by a call is shown as the call; that it is Pydantic AI's offline FunctionModel is in the source.
    assert helper["model"] == {
        "id": None,
        "name": "FunctionModel(never)",
        "provider": "function",
        "offline": True,
        "expression": "FunctionModel(never)",
        "model_class": "FunctionModel",
        "env": None,
    }
    # Nothing ran and nothing global was touched.
    assert _compat.instrumentation_default() is before
    assert active_session() is None


def test_view_on_a_name_in_a_file_shows_that_agent_and_its_delegates(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    code, out, err = run_cli(capsys, "view", "viz_cli_agents.py:triage", "--export", "one.html")
    assert (code, out, err) == (0, "Wrote HTML export one.html\n", READ_ONE_FILE + "2 agents, 0 graphs\n")
    events = boot_of(project / "one.html")["events"]
    assert blueprint_labels(events) == ["triage", "helper"]  # the delegate comes along
    assert events[1]["agent"]["delegates"] == [{"tool_name": "ask_helper", "agent_id": "a2", "evidence": "code"}]
    assert events[0]["title"] == "viz_cli_agents"


def test_view_serves_what_it_read_and_says_that_no_run_will_come(
    capsys: pytest.CaptureFixture[str],
    project: Path,
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
) -> None:
    (project / "more.py").write_text("from pydantic_ai import Agent\nextra = Agent(pick_model())\n", encoding="utf-8")
    before = _compat.instrumentation_default()
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        seen["events"] = follow(session, lambda events: len(events) == len(session.events))
        seen["rerun"] = fetch(session, "POST", "/api/v1/rerun")[0]
        seen["instrumentation"] = _compat.instrumentation_default()

    served = serving(watch)
    code, out, err = run_cli(capsys, "view", "viz_cli_agents.py:unnamed", "more.py", "--port", "0")
    assert (code, err) == (
        0,
        "Read 2 files without running any code: 2 agents, 0 graphs; 1 warning about what the source does not "
        "spell out (see the viewer)\n",
    )
    (session,) = served
    assert out == f"pydantic-ai-viz {pydantic_ai_viz.__version__} - serving {session.url} (Ctrl+C to stop)\n"
    assert opened == [session.url]
    assert seen["events"][0]["mode"] == "blueprint"
    assert blueprint_labels(seen["events"]) == ["unnamed", "extra"]
    assert seen["rerun"] == 404  # not a demo
    assert seen["instrumentation"] is before  # viewing instruments nothing
    assert session.closed


def test_view_says_on_stderr_what_it_skipped_and_puts_it_in_the_page(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    (project / "broken.py").write_text("def (:\n", encoding="utf-8")
    code, out, err = run_cli(capsys, "view", "broken.py", "viz_cli_agents.py", "--export", "x.html")
    assert (code, out) == (0, "Wrote HTML export x.html\n")
    assert err == (
        "pydantic-ai-viz: note: skipped broken.py: it is not valid Python (invalid syntax on line 1)\n"
        + READ_ONE_FILE
        + "3 agents, 0 graphs\n"
    )
    started = boot_of(project / "x.html")["events"][0]
    assert started["notices"] == ["skipped broken.py: it is not valid Python (invalid syntax on line 1)"]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing.py", "file not found: missing.py"),
        ("viz_cli_agents.py:nope", "no agent or graph is assigned to 'nope' at the top level of viz_cli_agents.py"),
        ("viz_cli_agents.py:never", "no agent or graph is assigned to 'never'"),
        # A module name is looked for on disk. One that is not there is not imported to find out more.
        ("no_such_module:triage", "no module named 'no_such_module'"),
        ("no_such_module", "or use --import to import it by name (this runs its code)"),
        ("https://example.com/owner/repo", "of remote code only GitHub repositories are fetched"),
    ],
)
def test_view_target_that_cannot_be_read_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], project: Path, target: str, message: str
) -> None:
    code, out, err = run_cli(capsys, "view", "viz_cli_agents.py", target, "--export", "x.html")
    assert (code, out) == (2, "")
    assert err.startswith("pydantic-ai-viz: error: ")
    assert message in err
    assert not (project / "x.html").exists()
    assert active_session() is None


def test_view_on_source_without_agents_says_so_and_points_at_import(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    (project / "plain.py").write_text("agents = build_agents_somehow()\n", encoding="utf-8")
    (project / "broken.py").write_text("def (:\n", encoding="utf-8")
    code, out, err = run_cli(capsys, "view", "plain.py", "broken.py", "--export", "x.html")
    assert (code, out) == (2, "")
    assert err.splitlines() == [
        "pydantic-ai-viz: note: skipped broken.py: it is not valid Python (invalid syntax on line 1)",
        "pydantic-ai-viz: error: no agents or graphs found in the source of plain.py, broken.py (1 file read). "
        "The code is read, not run: an agent built in a way that cannot be seen in the source is not found. "
        "Use --import to import the code instead (this runs it)",
    ]
    assert not (project / "x.html").exists()


def test_view_on_a_directory_without_python_does_not_point_at_import(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    (project / "empty").mkdir()
    code, _, err = run_cli(capsys, "view", "empty", "--export", "x.html")
    assert code == 2
    assert "(0 files read)" in err
    assert "--import" not in err


def test_say_writes_typographic_punctuation_as_plain_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    cli._say("install it \u2014 \u201cquoted\u201d, it\u2019s 1\u20132\u2026 caf\xe9")  # pyright: ignore[reportPrivateUsage]
    assert capsys.readouterr().out == 'install it - "quoted", it\'s 1-2... caf\\xe9\n'


def test_a_busy_port_is_named_when_another_is_taken(
    capsys: pytest.CaptureFixture[str],
    project: Path,
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        served = serving(lambda session: None)
        code, out, err = run_cli(capsys, "view", "viz_cli_agents.py", "--port", str(port), "--no-open")
    (session,) = served
    assert code == 0
    assert session.url is not None
    assert not session.url.endswith(f":{port}")
    assert f"pydantic-ai-viz: note: port {port} is in use, so a free port was taken\n" in err
    assert out == f"pydantic-ai-viz {pydantic_ai_viz.__version__} - serving {session.url} (Ctrl+C to stop)\n"
    assert opened == []


def test_view_on_a_directory_reads_every_file_below_it(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    (project / "app" / "deep").mkdir(parents=True)
    (project / "app" / "deep" / "more.py").write_text(
        "from pydantic_ai import Agent\nfrom viz_cli_agents import triage\nextra = Agent('test', name='extra')\n",
        encoding="utf-8",
    )
    (project / ".venv" / "lib").mkdir(parents=True)
    (project / ".venv" / "lib" / "installed.py").write_text(AGENTS, encoding="utf-8")
    code, out, err = run_cli(capsys, "view", ".", "--export", "all.html")
    assert (code, out) == (0, "Wrote HTML export all.html\n")
    assert err == "Read 2 files without running any code: 4 agents, 0 graphs\n"
    boot = boot_of(project / "all.html")
    assert blueprint_labels(boot["events"]) == ["helper", "triage", "unnamed", "extra"]
    assert boot["events"][0]["title"] == project.name
    files = [e["agent"]["location"]["file"] for e in boot["events"] if e["type"] == "blueprint.agent"]
    assert files == [*["viz_cli_agents.py"] * 3, "app/deep/more.py"]

    # A subdirectory alone, in what is now recognisably a project: the file it imports is read too,
    # and the agent imported by name is shown with the agent it delegates to.
    (project / "pyproject.toml").write_text("", encoding="utf-8")
    code, out, err = run_cli(capsys, "view", "app", "--export", "app.html")
    assert (code, out) == (0, "Wrote HTML export app.html\n")
    assert err.splitlines()[-1] == "Read 2 files without running any code: 3 agents, 0 graphs"
    events = boot_of(project / "app.html")["events"]
    assert blueprint_labels(events) == ["extra", "helper", "triage"]
    assert events[2]["agent"]["location"] == {"file": "../viz_cli_agents.py", "line": 10}


def test_view_on_a_module_name_finds_the_file_without_importing_it(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    (project / "viz_cli_agents.py").write_text("raise RuntimeError('imported')\n" + AGENTS, encoding="utf-8")
    code, out, err = run_cli(capsys, "view", "viz_cli_agents:triage", "--export", "one.html")
    assert (code, out, err) == (0, "Wrote HTML export one.html\n", READ_ONE_FILE + "2 agents, 0 graphs\n")
    boot = boot_of(project / "one.html")
    assert blueprint_labels(boot["events"]) == ["triage", "helper"]
    assert boot["events"][0]["title"] == "viz_cli_agents"
    assert "viz_cli_agents" not in sys.modules


@pytest.mark.parametrize(
    "target", ["gh:owner/repo", "https://github.com/owner/repo/tree/main/src", "https://example.com/x.py"]
)
def test_view_import_is_refused_for_code_that_would_be_fetched(
    capsys: pytest.CaptureFixture[str], project: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    def no_fetching(*args: object, **kwargs: object) -> None:
        raise AssertionError("nothing may be fetched for a target that is refused")

    monkeypatch.setattr(subprocess, "run", no_fetching)
    code, out, err = run_cli(capsys, "view", "--import", "viz_cli_agents.py", target, "--export", "x.html")
    assert (code, out) == (2, "")
    assert err == (
        f"pydantic-ai-viz: error: --import is refused for {target}: code fetched from the network is only ever "
        "read, never run. Leave out --import, or clone the repository yourself and decide whether you trust it\n"
    )
    assert not (project / "x.html").exists()
    assert "viz_cli_agents" not in sys.modules  # refused before any target was imported


def test_view_help_says_that_import_executes_the_code(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["view", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "The code is parsed, never imported or executed" in text
    assert "--import" in text
    assert "This EXECUTES the target code, so use it only on code you trust" in text


# ------------------------------------------------------------- view --import


def test_view_import_on_a_module_attribute_and_on_a_file(capsys: pytest.CaptureFixture[str], project: Path) -> None:
    before = _compat.instrumentation_default()
    code, out, err = run_cli(capsys, "view", "--import", "viz_cli_agents:triage", "--export", "one.html")
    assert (code, out, err) == (0, "Wrote HTML export one.html\n", "")
    events = boot_of(project / "one.html")["events"]
    assert [e["type"] for e in events] == ["session.started", "blueprint.agent", "blueprint.agent"]
    assert blueprint_labels(events) == ["triage", "helper"]  # the delegate comes along
    assert events[1]["agent"]["delegates"] == [{"tool_name": "ask_helper", "agent_id": "a2", "evidence": "object"}]
    assert (events[0]["title"], events[0]["mode"]) == ("triage", "blueprint")

    code, out, _ = run_cli(capsys, "view", "viz_cli_agents.py", "--import", "--export", "all.html")
    assert code == 0
    events = boot_of(project / "all.html")["events"]
    assert sorted(blueprint_labels(events)) == ["helper", "triage", "unnamed"]
    assert events[0]["title"] == "viz_cli_agents"
    assert {e["agent"]["source"] for e in events[1:]} == {"object"}  # read from the live objects
    # Blueprints only: no agent ran, and nothing global was touched.
    assert {e["type"] for e in events} == {"session.started", "blueprint.agent"}
    assert _compat.instrumentation_default() is before
    assert active_session() is None


def test_view_import_serves_the_blueprints_of_several_targets(
    capsys: pytest.CaptureFixture[str],
    project: Path,
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
) -> None:
    before = _compat.instrumentation_default()
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        seen["events"] = follow(session, lambda events: len(events) == len(session.events))
        seen["rerun"] = fetch(session, "POST", "/api/v1/rerun")[0]
        seen["instrumentation"] = _compat.instrumentation_default()

    served = serving(watch)
    code, out, err = run_cli(
        capsys, "view", "--import", "viz_cli_agents.py:unnamed", "viz_cli_agents:triage", "--port", "0"
    )
    assert (code, err) == (0, "")
    (session,) = served
    assert out == f"pydantic-ai-viz {pydantic_ai_viz.__version__} - serving {session.url} (Ctrl+C to stop)\n"
    assert opened == [session.url]
    assert seen["events"][0]["mode"] == "blueprint"
    assert blueprint_labels(seen["events"]) == ["unnamed", "triage", "helper"]
    assert seen["rerun"] == 404  # not a demo
    assert seen["instrumentation"] is before  # viewing instruments nothing
    assert session.closed


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing.py", "file not found: missing.py"),
        ("no_such_module_anywhere:agent", "cannot import module 'no_such_module_anywhere'"),
        ("viz_cli_agents:nope", "viz_cli_agents has no attribute 'nope'"),
        ("viz_cli_agents.py:never", "viz_cli_agents.py:never is a function, not a pydantic_ai.Agent"),
    ],
)
def test_view_import_target_not_found_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], project: Path, target: str, message: str
) -> None:
    code, out, err = run_cli(capsys, "view", "--import", "viz_cli_agents.py", target, "--export", "x.html")
    assert (code, out) == (2, "")
    assert err.startswith("pydantic-ai-viz: error: ")
    assert message in err
    assert not (project / "x.html").exists()
    assert active_session() is None


def test_view_import_reports_an_error_inside_the_target_as_a_runtime_error(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    (project / "broken.py").write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
    code, _, err = run_cli(capsys, "view", "--import", "broken.py", "--export", "x.html")
    assert code == 1
    assert err == "pydantic-ai-viz: error: RuntimeError: boom at import\n"


def test_view_import_on_a_file_that_exits_while_loading_says_so(
    capsys: pytest.CaptureFixture[str], project: Path
) -> None:
    source = "from viz_cli_agents import triage\nimport sys\nsys.exit(7)\n"
    (project / "exits.py").write_text(source, encoding="utf-8")
    code, out, err = run_cli(capsys, "view", "--import", "exits.py", "--export", "x.html")
    assert (code, out) == (2, "")
    assert err == (
        "pydantic-ai-viz: error: exits.py called sys.exit(7) while being loaded; a target is loaded without "
        'being run as a script, so keep exits under `if __name__ == "__main__":`\n'
    )
    assert not (project / "x.html").exists()
    assert active_session() is None
    # The same for a module target, which is imported.
    code, _, err = run_cli(capsys, "view", "--import", "exits:triage")
    assert code == 2
    assert "exits called sys.exit(7) while being loaded" in err


# ----------------------------------------------------------------------- run

SCRIPT = """
import json
import sys
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import sibling_of_the_script  # importable because the script's directory is on sys.path, as for `python script.py`

agent = Agent(TestModel())  # no name: Pydantic AI infers "agent" on the first run
Path("seen.json").write_text(json.dumps({"argv": sys.argv, "name": __name__}), encoding="utf-8")

if __name__ == "__main__":
    agent.run_sync("Hello")
    MODE
"""


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], Path]]:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "sibling_of_the_script.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def write(mode: str) -> Path:
        path = tmp_path / "app" / "my_script.py"
        path.write_text(SCRIPT.replace("MODE", mode), encoding="utf-8")
        return path

    yield write
    sys.modules.pop("sibling_of_the_script", None)


def test_run_captures_the_script_then_registers_what_it_left_behind(
    capsys: pytest.CaptureFixture[str],
    script: Callable[[str], Path],
    opened: list[str],
    serving: Callable[[Watch], list[Session]],
    tmp_path: Path,
) -> None:
    path = script("pass")
    argv_before, path_before = list(sys.argv), list(sys.path)
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        seen["events"] = follow(session, lambda events: len(events) == len(session.events))
        seen["files_at_wait"] = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    served = serving(watch)
    code, out, err = run_cli(
        capsys,
        "run",
        "--stream",
        "--record",
        "out.pviz.jsonl",
        "--export",
        "out.html",
        "--port",
        "0",
        str(path),
        "--stream",
        "--customer",
        "42",
        "-o",
        "x",
    )
    assert (code, err) == (0, "")
    (session,) = served
    assert out.splitlines() == [
        f"pydantic-ai-viz {pydantic_ai_viz.__version__} - serving {session.url} (Ctrl+C to stop)",
        "Wrote recording out.pviz.jsonl",
        "Wrote HTML export out.html",
    ]
    assert opened == [session.url]
    # The script ran as `python my_script.py --stream --customer 42 -o x` would have.
    assert json.loads((tmp_path / "seen.json").read_text(encoding="utf-8")) == {
        "argv": [str(path), "--stream", "--customer", "42", "-o", "x"],
        "name": "__main__",
    }
    assert (sys.argv, sys.path) == (argv_before, path_before)

    events = seen["events"]
    started = events[0]
    assert (started["title"], started["stream"], started["include_content"]) == ("my_script", True, True)
    # The run was captured live, before the script's globals could be inspected: first an
    # observed blueprint, then the static one taking over the same id once the script ended.
    blueprints = [(e["agent"]["id"], e["agent"]["name"], e["agent"]["source"]) for e in events if "agent" in e]
    assert blueprints[0] == ("a1", "agent", "observed")
    assert blueprints[-1] == ("a1", "agent", "object")
    assert finished_runs(events) == 1
    assert any(e["type"] == "stream.delta" for e in events)
    # Files were written when the script ended, before the wait, with the full log.
    assert seen["files_at_wait"] == ["out.html", "out.pviz.jsonl", "seen.json"]
    assert read_jsonl(tmp_path / "out.pviz.jsonl") == events
    assert session.closed


def test_run_no_content_keeps_prompts_and_tokens_out_even_with_stream(
    capsys: pytest.CaptureFixture[str],
    script: Callable[[str], Path],
    serving: Callable[[Watch], list[Session]],
) -> None:
    path = script("pass")
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        seen["events"] = follow(session, lambda events: len(events) == len(session.events))

    serving(watch)
    code, _, err = run_cli(capsys, "run", "--stream", "--no-content", "--no-open", "--port", "0", str(path))
    assert (code, err) == (0, "")
    events = seen["events"]
    started = events[0]
    # Token deltas are content, so --no-content wins over --stream.
    assert (started["stream"], started["include_content"]) == (False, False)
    assert finished_runs(events) == 1
    assert not any(e["type"] == "stream.delta" for e in events)
    # Structure without the prompt or the model's answer (TestModel answers "success (no tool calls)").
    dumped = json.dumps(events)
    assert "Hello" not in dumped
    assert "success" not in dumped


def test_run_on_a_script_that_raises_prints_the_traceback_and_keeps_serving(
    capsys: pytest.CaptureFixture[str],
    script: Callable[[str], Path],
    serving: Callable[[Watch], list[Session]],
    tmp_path: Path,
) -> None:
    path = script("raise ValueError('boom ☕')")
    seen: dict[str, Any] = {}

    def watch(session: Session) -> None:
        seen["health"] = fetch(session, "GET", "/api/v1/health")[0]
        seen["events"] = follow(session, lambda events: len(events) == len(session.events))

    serving(watch)
    code, out, err = run_cli(capsys, "run", "--no-open", "--port", "0", "--record", "out.pviz.jsonl", str(path))
    assert code == 0  # the failure is the script's; the viewer did its job
    assert "serving http://127.0.0.1:" in out
    assert "Wrote recording out.pviz.jsonl" in out
    # The traceback as `python my_script.py` would show it: the script's frame, none of ours.
    assert err.startswith("Traceback (most recent call last):\n")
    assert f'File "{path}", line ' in err
    assert "ValueError: boom \\u2615" in err
    assert "runpy" not in err
    assert "cli.py" not in err
    assert err.rstrip().endswith("my_script.py raised ValueError; still serving what was captured.")
    # The run that led to the failure is there to inspect.
    assert seen["health"] == 200
    assert finished_runs(seen["events"]) == 1
    assert {e["agent"]["source"] for e in seen["events"] if e["type"] == "blueprint.agent"} == {"observed"}
    assert finished_runs(read_jsonl(tmp_path / "out.pviz.jsonl")) == 1


@pytest.mark.parametrize(("mode", "note"), [("sys.exit(3)", "my_script.py exited with code 3"), ("sys.exit(0)", "")])
def test_run_on_a_script_that_exits_keeps_serving(
    capsys: pytest.CaptureFixture[str],
    script: Callable[[str], Path],
    serving: Callable[[Watch], list[Session]],
    mode: str,
    note: str,
) -> None:
    served = serving(lambda session: None)
    code, _, err = run_cli(capsys, "run", "--no-open", "--port", "0", str(script(mode)))
    assert code == 0
    assert len(served) == 1
    assert note in err
    assert ("exited" in err) == bool(note)


def test_run_interrupted_still_writes_the_outputs_then_stops(
    capsys: pytest.CaptureFixture[str],
    script: Callable[[str], Path],
    serving: Callable[[Watch], list[Session]],
    tmp_path: Path,
) -> None:
    # Ctrl+C is the only way to end a script that loops (a chat loop, a worker), so it must not
    # cost the recording. One Ctrl+C still ends the command: there is no wait afterwards.
    served = serving(lambda session: None)
    code, out, err = run_cli(
        capsys,
        "run",
        "--no-open",
        "--port",
        "0",
        "--record",
        "out.pviz.jsonl",
        "--export",
        "out.html",
        str(script("raise KeyboardInterrupt")),
    )
    assert code == 0
    assert err == "my_script.py interrupted.\n"
    assert out.splitlines()[1:] == ["Wrote recording out.pviz.jsonl", "Wrote HTML export out.html"]
    assert finished_runs(read_jsonl(tmp_path / "out.pviz.jsonl")) == 1
    assert (tmp_path / "out.html").is_file()
    assert served == []
    assert active_session() is None


def test_run_usage_errors(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, err = run_cli(capsys, "run", str(tmp_path / "missing.py"))
    assert (code, out) == (2, "")
    assert err == f"pydantic-ai-viz: error: script not found: {tmp_path / 'missing.py'}\n"
    assert active_session() is None


# ----------------------------------------------------------- open and export


@pytest.fixture
def recording(capsys: pytest.CaptureFixture[str], demos: dict[str, StubDemo], tmp_path: Path) -> Path:
    path = tmp_path / "alpha run.pviz.jsonl"
    assert run_cli(capsys, "demo", "--speed", "0", "--record", str(path))[0] == 0
    return path


def test_export_converts_a_recording(capsys: pytest.CaptureFixture[str], recording: Path, tmp_path: Path) -> None:
    out_path = tmp_path / "page.html"
    code, out, err = run_cli(capsys, "export", str(recording), "-o", str(out_path))
    assert (code, out, err) == (0, f"Wrote HTML export {out_path}\n", "")
    assert boot_of(out_path) == {"mode": "embedded", "events": read_jsonl(recording)}
    assert run_cli(capsys, "export", str(recording), "--output", str(tmp_path / "long.html"))[0] == 0
    assert (tmp_path / "long.html").read_bytes() == out_path.read_bytes()


def test_open_writes_a_temporary_export_and_opens_it_without_a_server(
    capsys: pytest.CaptureFixture[str],
    recording: Path,
    opened: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    code, out, err = run_cli(capsys, "open", str(recording))
    assert (code, err) == (0, "")
    (directory,) = scratch.iterdir()
    assert directory.name.startswith("pydantic-ai-viz-")
    page = directory / "alpha run.html"
    assert out == f"Opening {page}\n"
    assert opened == [page.as_uri()]
    assert opened[0].startswith("file:///")
    assert boot_of(page) == {"mode": "embedded", "events": read_jsonl(recording)}
    assert active_session() is None
    assert not [thread for thread in threading.enumerate() if thread.name == "pydantic-ai-viz-server"]


@pytest.mark.parametrize("command", [("open",), ("export", "-o", "out.html")])
def test_open_and_export_file_errors(
    capsys: pytest.CaptureFixture[str],
    opened: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: tuple[str, ...],
) -> None:
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "missing.pviz.jsonl"
    code, out, err = run_cli(capsys, *command, str(missing))
    assert (code, out) == (2, "")
    assert err == f"pydantic-ai-viz: error: recording not found: {missing}\n"

    not_a_recording = tmp_path / "notes.pviz.jsonl"
    not_a_recording.write_text('{"type": "span.started"}\n', encoding="utf-8")
    code, out, err = run_cli(capsys, *command, str(not_a_recording))
    assert (code, out) == (1, "")
    assert "not a pviz recording" in err
    assert opened == []
    assert not Path("out.html").exists()
