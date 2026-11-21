"""The hosted static demo, as ``scripts/build_demo_site.py`` writes it (spec section 6)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic_ai import Agent

from pydantic_ai_viz import demos
from pydantic_ai_viz._export import BOOT_TOKEN, VIEWER_PATH
from pydantic_ai_viz.demos import DEMOS

from .support import REPO, assert_log_guarantees, child_env, finished_runs, need, read_recording

SCRIPT = REPO / "scripts" / "build_demo_site.py"


def build(out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *extra],
        cwd=REPO,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def test_the_demo_site_is_the_viewer_a_gallery_index_and_one_recording_per_demo(tmp_path: Path) -> None:
    need(VIEWER_PATH.is_file(), "the viewer is not built (pnpm --dir web build)")
    out = tmp_path / "site" / "demo"
    result = build(out, "--speed", "0")
    assert result.returncode == 0, result.stderr
    assert sorted(path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()) == sorted(
        [
            "index.html",
            "recordings/index.json",
            "recordings/source.pviz.jsonl",
            *(f"recordings/{name}.pviz.jsonl" for name in DEMOS),
        ]
    )

    # The page is the built viewer, untouched: its empty boot slot is what makes it look for the gallery.
    page = (out / "index.html").read_bytes()
    assert page == VIEWER_PATH.read_bytes()
    assert page.decode("utf-8").count(BOOT_TOKEN) == 1

    # The gallery lists the demos in their display order, then their source as a blueprint to open;
    # `src` is relative to the recordings directory.
    index = json.loads((out / "recordings" / "index.json").read_text(encoding="utf-8"))
    *runs, source = index["recordings"]
    assert runs == [
        {"id": demo.name, "title": demo.title, "description": demo.description, "src": f"{demo.name}.pviz.jsonl"}
        for demo in DEMOS.values()
    ]
    assert source["src"] == "source.pviz.jsonl"
    assert source["kind"] == "blueprint"

    for demo in DEMOS.values():
        events = read_recording(out / "recordings" / f"{demo.name}.pviz.jsonl")
        assert_log_guarantees(events)
        assert (events[0]["title"], events[0]["stream"]) == (demo.title, True)
        assert finished_runs(events) == 1
        assert any(event["type"] == "stream.delta" for event in events)

    # The same demos as `view` reads their source: every agent and graph, from code, and no run.
    events = read_recording(out / "recordings" / "source.pviz.jsonl")
    assert_log_guarantees(events)
    assert (events[0]["title"], events[0]["mode"], events[0]["notices"]) == (
        "The demos, read from source",
        "blueprint",
        [],
    )
    assert {event["type"] for event in events[1:]} == {"blueprint.agent", "blueprint.graph"}
    agents = [event["agent"] for event in events if event["type"] == "blueprint.agent"]
    modules = (demos.support, demos.research, demos.pipeline)
    live = [str(value.name) for module in modules for value in vars(module).values() if isinstance(value, Agent)]
    # The capabilities demo is read from source even when pydantic-ai-harness (needed to import it) is missing.
    assert sorted(agent["label"] for agent in agents) == sorted([*live, "briefing"])
    assert {agent["source"] for agent in agents} == {"code"}
    # Where each one is defined, relative to the demos: no path of the machine that built the site.
    assert {agent["location"]["file"] for agent in agents} == {
        "support.py",
        "research.py",
        "pipeline.py",
        "capabilities.py",
    }
    assert [event["graph"]["label"] for event in events if event["type"] == "blueprint.graph"] == ["pipeline"]


def test_a_negative_speed_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    result = build(tmp_path / "demo", "--speed", "-1")
    assert result.returncode == 2
    assert "--speed must be 0 or more" in result.stderr
    assert not (tmp_path / "demo").exists()
