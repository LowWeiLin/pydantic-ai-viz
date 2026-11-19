"""Run the static analyser over the real-world set and write a table of what it found.

    uv run python scripts/realworld_report.py --out realworld-results.md

The set is ``tests/realworld/manifest.toml``: public projects that use Pydantic AI,
each pinned to a commit. Every project is fetched from GitHub with git, once, into
the cache directory (``PYDANTIC_AI_VIZ_CACHE_DIR``, or the user's cache), and then
only read: none of its code is imported or run. Nothing of the projects is kept in
this repository.

The table has one row per project: files read, agents, tools, graphs, delegation
edges, warnings, and the time the analysis took once the files were on disk. The
exit status is 1 when a project could not be fetched or analysed, or when it no
longer meets what the manifest expects of it.

``tests/realworld`` runs the same checks under pytest (``uv run pytest -m network tests/realworld``).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pydantic_ai_viz as pviz
from pydantic_ai_viz import _github

MANIFEST = Path(__file__).resolve().parent.parent / "tests" / "realworld" / "manifest.toml"

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*/[A-Za-z0-9_][A-Za-z0-9._-]*")
_PROJECT_KEYS = {"id", "repo", "sha", "subdir", "licence", "exercises", "expect"}
_COUNTS = ("files", "agents", "tools", "graphs", "delegations")
_NAMES = ("agents", "tools", "graphs", "delegations")
_EXPECT_KEYS = {*(f"min_{count}" for count in _COUNTS), *_NAMES}


class ManifestError(ValueError):
    """A manifest that cannot be used as written."""


@dataclass(frozen=True)
class Expectation:
    """What the analyser must find in a project, at the least."""

    min_files: int = 1
    min_agents: int = 0
    min_tools: int = 0
    min_graphs: int = 0
    min_delegations: int = 0
    agents: tuple[str, ...] = ()
    """Labels of agents that must be found."""
    tools: tuple[str, ...] = ()
    """Names of tools that must be found on some agent."""
    graphs: tuple[str, ...] = ()
    """Labels of graphs that must be found."""
    delegations: tuple[str, ...] = ()
    """Delegation edges that must be found, each as ``"from label -> to label"``."""


@dataclass(frozen=True)
class Project:
    """One entry of the manifest."""

    id: str
    repo: str
    sha: str
    subdir: str
    licence: str
    exercises: str
    expect: Expectation

    @property
    def target(self) -> str:
        """The project as a target of ``pydantic-ai-viz view`` and ``analyze``."""
        return f"gh:{self.repo}@{self.sha}" + (f"/{self.subdir}" if self.subdir else "")

    @property
    def url(self) -> str:
        """Where the pinned files can be browsed."""
        return f"https://github.com/{self.repo}/tree/{self.sha}" + (f"/{self.subdir}" if self.subdir else "")


@dataclass
class Result:
    """What the analyser found in one project."""

    project: Project
    files: int = 0
    agents: int = 0
    tools: int = 0
    graphs: int = 0
    delegations: int = 0
    warnings: int = 0
    seconds: float = 0.0
    problems: list[str] = field(default_factory=list[str])
    """Why the project counts as failed: an error, or an expectation of the manifest that was not met."""


def load_manifest(path: Path = MANIFEST) -> list[Project]:
    """Read and check the manifest.

    Raises:
        ManifestError: If an entry lacks a field, is not pinned to a full commit SHA, or repeats an id.
    """
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"{path}: {exc}") from None
    entries = document.get("project")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{path}: no [[project]] entries")
    projects: list[Project] = []
    seen: set[str] = set()
    for index, raw in enumerate(cast("list[Any]", entries), start=1):
        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: entry {index} is not a table")
        project = _project(cast("dict[str, Any]", raw), f"{path}: entry {index}")
        if project.id in seen:
            raise ManifestError(f"{path}: the id {project.id!r} is used twice")
        seen.add(project.id)
        projects.append(project)
    return projects


def _project(raw: dict[str, Any], where: str) -> Project:
    unknown = sorted(set(raw) - _PROJECT_KEYS)
    if unknown:
        raise ManifestError(f"{where}: unknown keys {unknown}")
    text: dict[str, str] = {}
    for key in ("id", "repo", "sha", "licence", "exercises"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(f"{where}: {key!r} is missing or empty")
        text[key] = value.strip()
    where = f"{where} ({text['id']})"
    if not _REPO.fullmatch(text["repo"]):
        raise ManifestError(f"{where}: repo {text['repo']!r} is not owner/name")
    if not _SHA.fullmatch(text["sha"]):
        raise ManifestError(f"{where}: sha must be a full 40-character lower-case commit SHA, not a branch or a tag")
    subdir = raw.get("subdir", "")
    if not isinstance(subdir, str) or subdir.startswith("/") or ".." in subdir.split("/"):
        raise ManifestError(f"{where}: subdir {subdir!r} is not a path inside the repository")
    expect = raw.get("expect", {})
    if not isinstance(expect, dict):
        raise ManifestError(f"{where}: expect is not a table")
    return Project(
        id=text["id"],
        repo=text["repo"],
        sha=text["sha"],
        subdir=subdir.strip("/"),
        licence=text["licence"],
        exercises=text["exercises"],
        expect=_expectation(cast("dict[str, Any]", expect), where),
    )


def _expectation(raw: dict[str, Any], where: str) -> Expectation:
    unknown = sorted(set(raw) - _EXPECT_KEYS)
    if unknown:
        raise ManifestError(f"{where}: unknown keys in expect: {unknown}")
    counts: dict[str, int] = {}
    for count in _COUNTS:
        value = raw.get(f"min_{count}", 1 if count == "files" else 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ManifestError(f"{where}: min_{count} must be a whole number")
        counts[f"min_{count}"] = value
    names: dict[str, tuple[str, ...]] = {}
    for key in _NAMES:
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in cast("list[Any]", value)):
            raise ManifestError(f"{where}: {key} must be a list of names")
        names[key] = tuple(cast("list[str]", value))
    for edge in names["delegations"]:
        if len(edge.split("->")) != 2:
            raise ManifestError(f"{where}: the delegation {edge!r} must read 'from -> to'")
    return Expectation(
        min_files=counts["min_files"],
        min_agents=counts["min_agents"],
        min_tools=counts["min_tools"],
        min_graphs=counts["min_graphs"],
        min_delegations=counts["min_delegations"],
        agents=names["agents"],
        tools=names["tools"],
        graphs=names["graphs"],
        delegations=names["delegations"],
    )


def measure(project: Project, limits: pviz.Limits | None = None) -> tuple[Result, pviz.Analysis | None]:
    """Fetch a project, analyse it and check it against what the manifest expects."""
    result = Result(project)
    reference = _github.parse(project.target)
    assert reference is not None
    try:
        # Fetched first and on its own, so that the time reported is the time spent reading.
        _github.fetch(reference, timeout=(limits or pviz.Limits()).max_fetch_seconds)
        started = time.perf_counter()
        found = pviz.analyze(project.target, limits=limits)
        result.seconds = time.perf_counter() - started
    except (_github.FetchError, ValueError) as exc:
        result.problems.append(f"could not be read: {exc}")
        return result, None
    result.files = len(found.files)
    result.agents = len(found.agents)
    result.tools = sum(len(agent.tools) for agent in found.agents)
    result.graphs = len(found.graphs)
    result.delegations = sum(len(agent.delegates) for agent in found.agents)
    result.warnings = sum(len(blueprint.warnings) for blueprint in found.blueprints)
    result.problems.extend(check(found, project.expect))
    return result, found


def check(found: pviz.Analysis, expect: Expectation) -> list[str]:
    """Every way in which an analysis falls short of an expectation; empty when it meets it."""
    problems: list[str] = []
    for notice in found.notices:
        # A file of a real project that could not be parsed is the project's business. These are ours.
        if any(sign in notice for sign in ("time limit", "at the limit", "abandoned", "holds more than")):
            problems.append(f"the analysis was cut short: {notice}")
    labels = {agent.id: agent.label for agent in found.agents}
    actual = {
        "files": len(found.files),
        "agents": len(found.agents),
        "tools": sum(len(agent.tools) for agent in found.agents),
        "graphs": len(found.graphs),
        "delegations": sum(len(agent.delegates) for agent in found.agents),
    }
    for count, value in actual.items():
        least: int = getattr(expect, f"min_{count}")
        if value < least:
            problems.append(f"{value} {count} found, at least {least} expected")
    names = {
        "agents": set(labels.values()),
        "tools": {tool.name for agent in found.agents for tool in agent.tools},
        "graphs": {graph.label for graph in found.graphs},
        "delegations": {
            f"{agent.label} -> {labels[delegate.agent_id]}" for agent in found.agents for delegate in agent.delegates
        },
    }
    for key, known in names.items():
        expected: tuple[str, ...] = getattr(expect, key)
        if key == "delegations":
            expected = tuple(" -> ".join(part.strip() for part in edge.split("->")) for edge in expected)
        missing = [name for name in expected if name not in known]
        if missing:
            problems.append(f"{key} not found: {', '.join(missing)}")
    return problems


def render(results: list[Result]) -> str:
    """The results as a Markdown table, with a line of totals."""
    lines = [
        "| Project | Commit | Files | Agents | Tools | Graphs | Delegation edges | Warnings | Time |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        project = result.project
        name = project.repo + (f" `{project.subdir}`" if project.subdir else "")
        commit = f"[`{project.sha[:7]}`]({project.url})"
        if result.problems and not result.files:
            lines.append(f"| {name} | {commit} | failed: {_cell(result.problems[0])} | | | | | | |")
            continue
        lines.append(
            f"| {name} | {commit} | {result.files:,} | {result.agents:,} | {result.tools:,} | {result.graphs:,} "
            f"| {result.delegations:,} | {result.warnings:,} | {result.seconds:.1f} s |"
        )
    read = [result for result in results if result.files]
    total = {key: sum(getattr(result, key) for result in read) for key in (*_COUNTS, "warnings")}
    lines.append(
        f"| **{len(read)} projects** | | **{total['files']:,}** | **{total['agents']:,}** | **{total['tools']:,}** "
        f"| **{total['graphs']:,}** | **{total['delegations']:,}** | **{total['warnings']:,}** "
        f"| **{sum(result.seconds for result in read):.1f} s** |"
    )
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """Text that is safe inside one cell of a Markdown table."""
    return " ".join(text.replace("|", "\\|").split())[:200]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse the real-world set and write a Markdown table of results.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help="the manifest to read (default: %(default)s)")
    parser.add_argument("--out", type=Path, help="write the table to this file instead of standard output")
    parser.add_argument("--only", action="append", metavar="ID", help="run only this project; may be repeated")
    args = parser.parse_args(argv)

    try:
        projects = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"realworld_report: {exc}", file=sys.stderr)
        return 2
    if args.only:
        unknown = sorted(set(args.only) - {project.id for project in projects})
        if unknown:
            print(f"realworld_report: no project with id {', '.join(unknown)}", file=sys.stderr)
            return 2
        projects = [project for project in projects if project.id in args.only]

    results: list[Result] = []
    for project in projects:
        result, _ = measure(project)
        results.append(result)
        status = "ok" if not result.problems else "FAILED"
        print(f"{project.id}: {status} ({result.agents} agents, {result.seconds:.1f} s)", file=sys.stderr)
        for problem in result.problems:
            print(f"  {problem}", file=sys.stderr)

    table = render(results)
    if args.out is None:
        sys.stdout.write(table)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(table)
    return 1 if any(result.problems for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
