from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_graph import Graph

from pydantic_ai_viz import record
from pydantic_ai_viz._targets import TargetError, namespace_module, resolve_target, resolve_targets, script_environment

SOURCE = """
import sys

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_graph import GraphBuilder, StepContext

import helper_next_to_the_target  # importable only if the file's directory is on sys.path

NOT_A_TARGET = {"some": "config"}
named = Agent(TestModel(), name="triage")
unnamed = Agent("openai:gpt-5.2", defer_model_check=True)

builder = GraphBuilder(name="flow", input_type=str, output_type=str)


@builder.step
async def shout(ctx: StepContext[None, None, str]) -> str:
    return ctx.inputs.upper()


builder.add(builder.edge_from(builder.start_node).to(shout), builder.edge_from(shout).to(builder.end_node))
graph = builder.build()

seen_name = __name__
if __name__ == "__main__":
    raise SystemExit("the main block must not run when a file is only viewed")
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory holding ``agents_file.py``, the same code as module ``viz_target_mod``, and a helper."""
    (tmp_path / "helper_next_to_the_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "agents_file.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "viz_target_mod.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "viz_empty_mod.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "viz_broken_mod.py").write_text("import a_package_that_is_not_installed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        del sys.modules[name]


def contents(target: Any) -> dict[str, str]:
    assert isinstance(target, ModuleType)
    return {name: type(value).__name__ for name, value in vars(target).items() if not name.startswith("__")}


def labels(*targets: Any) -> list[str]:
    with record(*targets, instrument=False) as session:
        blueprints = [event.get("agent") or event["graph"] for event in session.events[1:]]
    return [blueprint["label"] for blueprint in blueprints]


def test_a_file_contributes_its_top_level_agents_and_graphs(project: Path) -> None:
    path_before = list(sys.path)
    # SOURCE exits under __main__ and needs its own directory on sys.path: loading it proves both are handled.
    target = resolve_target("agents_file.py")
    assert contents(target) == {"named": "Agent", "unnamed": "Agent", "graph": "Graph"}
    assert target.__name__ == "agents_file"  # pyright: ignore[reportAttributeAccessIssue]
    # Unnamed objects are labelled with their variable name.
    assert labels(target) == ["triage", "unnamed", "flow"]
    assert sys.path == path_before  # the file's directory was importable only while it loaded
    assert "__pydantic_ai_viz__" not in sys.modules


def test_a_file_attribute(project: Path) -> None:
    named = resolve_target("agents_file.py:named")
    assert isinstance(named, Agent)
    assert named.name == "triage"
    graph = resolve_target("agents_file.py:graph")
    assert isinstance(graph, Graph)
    # No name to show: it comes wrapped so that its variable name becomes the label.
    unnamed = resolve_target("agents_file.py:unnamed")
    assert contents(unnamed) == {"unnamed": "Agent"}
    assert labels(unnamed) == ["unnamed"]


def test_absolute_paths_with_a_drive_colon_are_not_split_at_the_drive(project: Path) -> None:
    absolute = str(project / "agents_file.py")
    assert contents(resolve_target(absolute)) == {"named": "Agent", "unnamed": "Agent", "graph": "Graph"}
    assert isinstance(resolve_target(f"{absolute}:named"), Agent)


def test_a_module_and_a_module_attribute(project: Path) -> None:
    (module, named, unnamed) = resolve_targets(["viz_target_mod", "viz_target_mod:named", "viz_target_mod:unnamed"])
    assert contents(module) == {"named": "Agent", "unnamed": "Agent", "graph": "Graph"}
    assert module.__name__ == "viz_target_mod"  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(named, Agent)
    assert contents(unnamed) == {"unnamed": "Agent"}
    # A real import: the module ran under its own name, and the objects are the module's own.
    imported = sys.modules["viz_target_mod"]
    assert imported.seen_name == "viz_target_mod"
    assert named is imported.named
    # All three name the same objects, so registering them together draws each once.
    assert labels(module, named, unnamed) == ["triage", "unnamed", "flow"]


def test_resolving_makes_the_working_directory_importable_and_puts_sys_path_back(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As for an installed console script: neither "" nor the project directory is on sys.path.
    clean = [entry for entry in sys.path if entry not in ("", str(project))]
    monkeypatch.setattr(sys, "path", list(clean))
    with pytest.raises(TargetError):
        resolve_target("viz_target_mod")
    (module,) = resolve_targets(["viz_target_mod"])
    assert contents(module)
    assert sys.path == clean


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("missing_file.py", "file not found: missing_file.py"),
        ("missing_file.py:agent", "file not found: missing_file.py"),
        ("no_such_module_anywhere", "cannot import module 'no_such_module_anywhere'"),
        ("no_such_package.sub:agent", "cannot import module 'no_such_package.sub'"),
        ("viz_target_mod.sub", "cannot import module 'viz_target_mod.sub'"),
        ("agents_file.py:nope", "agents_file.py has no attribute 'nope'"),
        ("viz_target_mod:nope", "viz_target_mod has no attribute 'nope'"),
        ("agents_file.py:NOT_A_TARGET", "agents_file.py:NOT_A_TARGET is a dict, not a pydantic_ai.Agent"),
        ("viz_empty_mod", "no agents or graphs found at the top level of viz_empty_mod"),
        ("helper_next_to_the_target.py", "no agents or graphs found at the top level of helper_next_to_the_target.py"),
        ("not a module!", "not a module name or a .py file: 'not a module!'"),
        ("some/dir", "not a module name or a .py file: 'some/dir'"),
        ("", "not a module name or a .py file: ''"),
        (":attr", "not a module name or a .py file: ''"),
    ],
)
def test_targets_that_do_not_exist(project: Path, spec: str, message: str) -> None:
    with pytest.raises(TargetError) as exc_info:
        resolve_target(spec)
    assert message in str(exc_info.value)


def test_an_error_inside_the_target_is_the_users_to_see_not_a_missing_target(project: Path) -> None:
    with pytest.raises(ModuleNotFoundError, match="a_package_that_is_not_installed"):
        resolve_target("viz_broken_mod")
    (project / "raises.py").write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="boom at import"):
        resolve_target("raises.py")


def test_namespace_module_keeps_only_agents_and_graphs() -> None:
    agent: Agent[None, str] = Agent(None)
    module = namespace_module("picked", {"agent": agent, "number": 3, "__name__": "__main__", "Agent": Agent})
    assert module.__name__ == "picked"
    assert contents(module) == {"agent": "Agent"}
    assert module.agent is agent


def test_script_environment_swaps_argv_and_path_and_restores_them_after_an_error(tmp_path: Path) -> None:
    script = tmp_path / "sub" / "tool.py"
    path_before, argv_before = list(sys.path), list(sys.argv)
    inside: list[Any] = []

    def body() -> None:
        with script_environment(script, ["tool.py", "--flag"]):
            inside.extend([sys.path[0], list(sys.argv)])
            raise RuntimeError

    with pytest.raises(RuntimeError):
        body()
    assert inside == [str(tmp_path / "sub"), ["tool.py", "--flag"]]
    assert (sys.path, sys.argv) == (path_before, argv_before)
