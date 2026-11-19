from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph

import pydantic_ai_viz

EXAMPLES = Path(__file__).parents[2] / "examples"
OFFLINE = ["quickstart.py", "live_server.py", "export_html.py", "graph.py"]
ALL = [*OFFLINE, "with_logfire.py"]

pytestmark = pytest.mark.anyio


def test_the_examples_directory_is_exactly_what_the_readme_lists() -> None:
    assert sorted(path.name for path in EXAMPLES.glob("*.py")) == sorted(ALL)
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    assert all(f"`{name}`" in readme for name in ALL)


@pytest.mark.parametrize("name", ALL)
def test_example_is_short_and_guarded(name: str) -> None:
    source = (EXAMPLES / name).read_text(encoding="utf-8")
    assert len(source.splitlines()) < 30
    tree = ast.parse(source)
    assert ast.get_docstring(tree)
    # Everything with side effects sits under `if __name__ == "__main__":`, so `view --import FILE` runs none of it.
    last = tree.body[-1]
    assert isinstance(last, ast.If)
    assert ast.unparse(last.test) == "__name__ == '__main__'"


def test_only_the_logfire_example_needs_an_extra_package() -> None:
    for name in ALL:
        tree = ast.parse((EXAMPLES / name).read_text(encoding="utf-8"))
        imported = {
            (node.module if isinstance(node, ast.ImportFrom) else node.names[0].name) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
        }
        assert ("logfire" in imported) == (name == "with_logfire.py")
        assert "pydantic_ai_viz" in imported


@pytest.mark.parametrize("name", OFFLINE)
async def test_example_target_runs_offline(name: str) -> None:
    """Import each example the way `pydantic-ai-viz view --import FILE` does and exercise what it defines."""
    namespace = runpy.run_path(str(EXAMPLES / name), run_name="__pydantic_ai_viz__")
    agents = [value for value in namespace.values() if isinstance(value, Agent)]
    graphs = [value for value in namespace.values() if isinstance(value, Graph)]
    assert len(agents) + len(graphs) == 1
    for agent in agents:
        result = await agent.run("Hello")
        assert result.usage.tool_calls == 1
    for graph in graphs:
        assert await graph.run(inputs="graphs you can watch") == "Graphs You Can Watch - by the graph desk"


@pytest.mark.skipif(not hasattr(pydantic_ai_viz, "record"), reason="pydantic_ai_viz.record is not available yet")
def test_export_example_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(EXAMPLES / "export_html.py"), run_name="__main__")
    recording = (tmp_path / "run.pviz.jsonl").read_text(encoding="utf-8")
    assert '"session.started"' in recording.splitlines()[0]
    assert "search_catalogue" in recording
    assert "search_catalogue" in (tmp_path / "run.html").read_text(encoding="utf-8")
