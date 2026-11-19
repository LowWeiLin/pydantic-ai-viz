"""Static analysis: blueprints read from source code that is never imported or run.

``analyze`` parses Python files with the standard ``ast`` module and describes the
agents and graphs it finds. The analysed code is data: none of it is executed and
none of what it imports has to be installed.

How the pieces fit:

* ``_locate``: a target string -> the files to read. A GitHub repository is fetched first, by
  ``pydantic_ai_viz._github``, which is kept out of this package: nothing in here starts a process.
* ``_source``: one file -> its syntax tree, scopes and bindings (or a reason it was skipped).
* ``_resolve``: a name -> what it stands for, across the files of a ``Project``.
* ``_values``, ``_schema``, ``_docstring``: literal values, JSON Schema and descriptions from syntax.
* ``_agents``, ``_graphs``: the blueprints.
"""

from __future__ import annotations

import ast
import contextlib
import gc
import os
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

from .._models import AgentBlueprint, GraphBlueprint
from ._agents import AgentExtractor
from ._graphs import GraphExtractor
from ._installed import InstalledPydanticAI
from ._locate import Place, Source, TargetError, is_remote, place, sources_of
from ._resolve import END_OF_MODULE, Project, Resolver
from ._source import Module, SkippedFile, read_module

__all__ = ["Analysis", "Limits", "TargetError", "analyze", "is_remote"]


@dataclass(frozen=True, slots=True)
class Limits:
    """Bounds that keep the analysis of unknown code quick and finite."""

    max_file_bytes: int = 2_000_000
    """A larger file is skipped, with a notice."""
    max_files: int = 2_000
    """No more files than this are read in one analysis."""
    max_seconds: float = 30.0
    """Reading stops, with a notice, once this much time has passed. Fetching is timed separately."""
    max_fetch_seconds: float = 300.0
    """How long fetching one GitHub repository may take before it is given up."""


@dataclass(frozen=True, slots=True)
class Analysis:
    """What `analyze` found.

    Attributes:
        agents: One blueprint per `Agent(...)` construction, with ids `a1`, `a2`, ...
            in the order they appear. `delegates` refer to these ids.
        graphs: One blueprint per `GraphBuilder(...)` or `Graph(nodes=[...])`, with
            ids `g1`, `g2`, ...
        notices: Messages about the analysis as a whole, such as a file that was
            skipped because it is not valid Python, or the commit a GitHub
            repository was read at.
        files: The files that were read, relative to their common directory (for a
            GitHub repository, to the top of the repository): the targets' own
            files first, then the files read because those import them. Targets
            that share no directory, such as a GitHub repository and a local file,
            have their files named under a label each: `owner/repo@abc1234/...`,
            or the name of the target's project directory. A path is never absolute.
        title: A short name for what was analysed: the file, directory or module,
            or `owner/repo@commit`. `None` when there were several targets.
    """

    agents: list[AgentBlueprint] = field(default_factory=list[AgentBlueprint])
    graphs: list[GraphBlueprint] = field(default_factory=list[GraphBlueprint])
    notices: list[str] = field(default_factory=list[str])
    files: list[str] = field(default_factory=list[str])
    title: str | None = None

    @property
    def blueprints(self) -> list[AgentBlueprint | GraphBlueprint]:
        """Every blueprint: the agents, then the graphs."""
        return [*self.agents, *self.graphs]


def analyze(*targets: str | os.PathLike[str], limits: Limits | None = None) -> Analysis:
    """Describe the agents and graphs in Python source code, without importing or running it.

    The files are parsed, not executed: nothing they import has to be installed,
    no model is called and no code of theirs runs. What the source spells out is
    reported as a value; what it computes at run time is reported as the source
    text that computes it, and listed in the blueprint's `warnings`.

    ```python
    import pydantic_ai_viz as pviz

    found = pviz.analyze("src/my_app")
    for agent in found.agents:
        print(agent.label, [tool.name for tool in agent.tools])
    pviz.export_html(found, "blueprint.html")
    ```

    A target is one of:

    - **A file**, `path/to/file.py`. `path/to/file.py:name` selects the agent or
      graph assigned to `name` at the top level of that file.
    - **A directory**. Every `.py` file below it is read. Virtual environments,
      `node_modules`, `site-packages`, `__pycache__`, `build`, `dist` and hidden
      directories are passed over; a `build` or `dist` that holds `.py` files is
      named in a notice.
    - **A module name**, `package.module` or `package.module:name`. The file is
      found on disk, below the working directory, its `src` directory or an entry
      of `sys.path`; nothing is imported to find it. A package is read like a directory.
    - **A GitHub repository**, `https://github.com/owner/repo`, optionally followed
      by `/tree/<ref>/<subdir>` or `/blob/<ref>/<file>`, or in short
      `gh:owner/repo[@ref][/subdir]`. A ref is a branch, a tag or a full commit
      SHA. The commit is fetched with `git` into a cache directory and only read.

    Imports between the project's own files are followed, so an agent, a tool, an
    output model or a delegate defined in another file is described from that
    file. A file that is read only because another one imports it adds no
    blueprints of its own, except for the agents and graphs a target's file
    knows by name and the agents those delegate to. Installed packages are
    never opened.

    Args:
        *targets: Files, directories, module names or GitHub repositories, as above.
        limits: Bounds on file size, file count and time; the defaults suit
            ordinary projects.

    Returns:
        The blueprints found, plus notices about anything that had to be skipped.
        A file that cannot be read or parsed yields a notice, not an exception.

    Raises:
        ValueError: If a target does not exist, cannot be fetched, or selects a
            name that is not an agent or a graph.
    """
    limits = limits or Limits()
    places = [place(target, fetch_seconds=limits.max_fetch_seconds) for target in targets]
    if not places:
        return Analysis()
    with _collector_paused():
        return _read(places, limits)


@contextlib.contextmanager
def _collector_paused() -> Generator[None]:
    """Keep the cyclic garbage collector from running while syntax trees are built.

    The trees of a large project are millions of objects, all of them in use until
    the analysis ends. The collector finds nothing to free among them, and looking
    takes as long as the analysis itself.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _read(places: list[Place], limits: Limits) -> Analysis:
    # Fetching is over; the clock for reading starts here.
    deadline = time.monotonic() + limits.max_seconds
    notices = [notice for found in places for notice in found.notices]
    sources: list[Source] = []
    for found in places:
        listed, said = sources_of(found, max_files=limits.max_files, deadline=deadline)
        sources.extend(listed)
        notices.extend(said)

    names = _Names(places)
    project = _project(places, names.root)
    loaded: dict[Path, Module] = {}
    for source in sources:
        if source.path in loaded:
            continue
        rel = names(source.path)
        if len(project.modules) >= limits.max_files or time.monotonic() >= deadline:
            notices.append(f"stopped reading files at the limit; {rel} and what follows were not analysed")
            break
        result = read_module(source.path, rel, max_bytes=limits.max_file_bytes)
        if isinstance(result, SkippedFile):
            notices.append(f"skipped {result.rel}: {result.reason}")
            continue
        loaded[source.path] = result
        project.add(result)
    _follow_imports(project, names, limits, deadline, notices)

    files = [module.rel for module in project.modules]
    title = places[0].title if len(places) == 1 else None
    if title is None and len(files) == 1:
        title = Path(files[0]).stem  # several targets, of which one file could be read
    resolver = Resolver(project, deadline=deadline)
    agents = AgentExtractor(project, resolver, InstalledPydanticAI(max_bytes=limits.max_file_bytes))
    graphs = GraphExtractor(project, resolver)
    try:
        agents.find()
        graphs.find()
        agent_blueprints = agents.blueprints()
        graph_blueprints = graphs.blueprints()
        notices.extend(agents.notices())
    except RecursionError:
        # The parser accepted the files, yet something in them nests deeper than this analysis can follow.
        notices.append("the analysis was abandoned: the source is nested too deeply")
        return Analysis(notices=notices, files=files, title=title)

    if resolver.timed_out:
        notices.append(
            f"the analysis ran into its time limit of {limits.max_seconds:g} seconds; what is shown is incomplete"
        )
    selected = _selection(sources, loaded, agents, graphs, agent_blueprints, graph_blueprints, notices)
    if selected is not None:
        agent_blueprints, graph_blueprints = selected
    return Analysis(agents=agent_blueprints, graphs=graph_blueprints, notices=notices, files=files, title=title)


def _common_root(roots: list[Path]) -> Path | None:
    """The directory every target lies below; ``None`` when they share none (two drives)."""
    try:
        return Path(os.path.commonpath(roots))
    except ValueError:
        return None


def _plain(path: Path) -> Path:
    """``path`` without the ``\\\\?\\`` prefix that lets Windows open long paths, which is no part of its name."""
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[len("\\\\?\\UNC\\") :])
    if text.startswith("\\\\?\\"):
        return Path(text[len("\\\\?\\") :])
    return path


class _Names:
    """How the files read are named to the user: by relative paths, never by absolute ones.

    The files of one target, or of local targets that share a directory, are named
    relative to that directory. When a fetched repository is among several targets,
    or the targets share no directory (two drives), the files of each are named
    relative to their own target's project, under a short label: the repository
    and its commit, or the name of that directory.
    """

    def __init__(self, places: list[Place]) -> None:
        roots = [_plain(found.root) for found in places]
        separate = len(places) > 1 and any(found.shown_as is not None for found in places)
        self.root = None if separate else _common_root(roots)
        self.anchors: list[tuple[Path, str]] = []
        if self.root is not None:
            return
        labels: set[str] = set()
        for found, root in zip(places, roots, strict=True):
            boundary = None if found.boundary is None else _plain(found.boundary)
            anchor = boundary if boundary is not None and (boundary == root or boundary in root.parents) else root
            label = found.shown_as or anchor.name or found.title
            for number in range(2, len(places) + 2):
                if label not in labels:
                    break
                label = f"{found.shown_as or anchor.name or found.title} ({number})"
            labels.add(label)
            self.anchors.append((anchor, label))
        # A file below two anchors is named from the nearer one.
        self.anchors.sort(key=lambda item: len(item[0].parts), reverse=True)

    def __call__(self, path: Path) -> str:
        path = _plain(path)
        if self.root is not None:
            text, prefix = str(path), os.path.join(str(self.root), "")
            if text.startswith(prefix):
                return text[len(prefix) :].replace(os.sep, "/")
            with contextlib.suppress(ValueError):
                return os.path.relpath(text, str(self.root)).replace(os.sep, "/")  # imported, above the targets
            return path.name
        for anchor, label in self.anchors:
            if anchor in path.parents:
                return f"{label}/{path.relative_to(anchor).as_posix()}"
        return path.name


def _project(places: list[Place], root: Path | None) -> Project:
    boundaries = list(dict.fromkeys(found.boundary for found in places if found.boundary is not None))
    bases = [found.root for found in places]
    for boundary in boundaries:
        bases.append(boundary)
        if (boundary / "src").is_dir():
            bases.append(boundary / "src")
    return Project(root=root or places[0].root, bases=list(dict.fromkeys(bases)), boundaries=boundaries)


def _follow_imports(project: Project, names: _Names, limits: Limits, deadline: float, notices: list[str]) -> None:
    """Read the project files that the files read so far import, and the ones those import, and so on."""
    stopped = False

    def opener(path: Path) -> Module | None:
        nonlocal stopped
        rel = names(path)
        if len(project.modules) >= limits.max_files or time.monotonic() >= deadline:
            if not stopped:
                notices.append(f"stopped following imports at the limit; {rel} and other imported files were not read")
            stopped = True
            return None
        result = read_module(path, rel, max_bytes=limits.max_file_bytes)
        if isinstance(result, SkippedFile):
            notices.append(f"skipped {result.rel}: {result.reason}")
            return None
        return result

    project.opener = opener
    index = 0
    while index < len(project.modules) and not stopped:
        module = project.modules[index]
        index += 1
        for dotted, level in module.imports:
            project.find_module(module, dotted, level)
    # From here on the set of files is fixed: resolving a name never reads another file.
    project.opener = None


def _selection(
    sources: list[Source],
    loaded: dict[Path, Module],
    agents: AgentExtractor,
    graphs: GraphExtractor,
    agent_blueprints: list[AgentBlueprint],
    graph_blueprints: list[GraphBlueprint],
    notices: list[str],
) -> tuple[list[AgentBlueprint], list[GraphBlueprint]] | None:
    """Choose what is shown: ``None`` for everything, else the blueprints to keep, renumbered.

    Everything is shown unless a target selects a name (``file.py:name``) or a file
    was read only because another one imports it. Such a file adds no blueprints
    of its own: of what it defines, only the agents and graphs that a target's
    file knows by name, and the agents those delegate to, are kept.
    """
    selecting = any(source.select is not None for source in sources)
    targets = {module.rel for module in loaded.values()}
    imported_agents = {b.id for b in agent_blueprints if b.location is None or b.location.file not in targets}
    imported_graphs = {b.id for b in graph_blueprints if b.location is None or b.location.file not in targets}
    if not selecting and not imported_agents and not imported_graphs:
        return None

    agents_in: dict[str, list[str]] = {}
    for agent in agent_blueprints:
        if agent.location is not None:
            agents_in.setdefault(agent.location.file, []).append(agent.id)
    graphs_in: dict[str, list[str]] = {}
    for graph in graph_blueprints:
        if graph.location is not None:
            graphs_in.setdefault(graph.location.file, []).append(graph.id)

    by_id = {blueprint.id: blueprint for blueprint in agent_blueprints}
    keep_agents: list[str] = []
    keep_graphs: list[str] = []
    whole: set[Path] = set()
    for source in sources:
        module = loaded.get(source.path)
        if module is None:
            continue  # skipped with a notice
        if source.select is None:
            if source.path in whole:
                continue
            whole.add(source.path)
            keep_agents.extend(agents_in.get(module.rel, ()))
            keep_graphs.extend(graphs_in.get(module.rel, ()))
            # What the file knows by name from elsewhere, as importing the file would bring it along.
            for bound in module.scope.bindings if imported_agents or imported_graphs else ():
                name = ast.Name(id=bound, ctx=ast.Load())
                agent_id = agents.agent_id(name, module.scope, END_OF_MODULE) if imported_agents else None
                if agent_id is not None:
                    if agent_id in imported_agents:
                        keep_agents.append(agent_id)
                    continue
                graph_id = graphs.builder_id(name, module.scope, END_OF_MODULE) if imported_graphs else None
                if graph_id is not None and graph_id in imported_graphs:
                    keep_graphs.append(graph_id)
            continue
        name = ast.Name(id=source.select, ctx=ast.Load())
        agent_id = agents.agent_id(name, module.scope, END_OF_MODULE)
        graph_id = graphs.builder_id(name, module.scope, END_OF_MODULE)
        if agent_id is not None:
            keep_agents.append(agent_id)
        elif graph_id is not None:
            keep_graphs.append(graph_id)
        else:
            raise TargetError(
                f"{source.spec}: no agent or graph is assigned to {source.select!r} at the top level of "
                f"{module.rel}, as far as its source shows"
            )
    # An agent comes with the agents it delegates to, after it, as a live blueprint does.
    order = list(dict.fromkeys(keep_agents))
    index = 0
    while index < len(order):
        for delegate in by_id[order[index]].delegates:
            if delegate.agent_id not in order:
                order.append(delegate.agent_id)
        index += 1
    keep_graphs = list(dict.fromkeys(keep_graphs))
    if not selecting:
        # Nothing was singled out, so the order stays the one the files were read in.
        order.sort(key=lambda agent_id: int(agent_id[1:]))
        keep_graphs.sort(key=lambda graph_id: int(graph_id[1:]))
        left_out = [
            *(b for b in agent_blueprints if b.id in imported_agents and b.id not in order),
            *(b for b in graph_blueprints if b.id in imported_graphs and b.id not in keep_graphs),
        ]
        if left_out:
            where = sorted({b.location.file for b in left_out if b.location is not None})
            shown = ", ".join(where[:5]) + (f" and {len(where) - 5} more" if len(where) > 5 else "")
            count = "1 agent or graph" if len(left_out) == 1 else f"{len(left_out)} agents and graphs"
            notices.append(
                f"{count} defined in imported files ({shown}) are not shown; "
                "name those files or their directory to see them"
            )
    renumbered = {old: f"a{position}" for position, old in enumerate(order, start=1)}
    graphs_by_id = {blueprint.id: blueprint for blueprint in graph_blueprints}
    kept_agents = [
        by_id[old].model_copy(
            update={
                "id": new,
                "label": f"agent-{new[1:]}" if by_id[old].label == f"agent-{old[1:]}" else by_id[old].label,
                "delegates": [
                    delegate.model_copy(update={"agent_id": renumbered[delegate.agent_id]})
                    for delegate in by_id[old].delegates
                ],
            }
        )
        for old, new in renumbered.items()
    ]
    kept_graphs = [
        graphs_by_id[old].model_copy(
            update={
                "id": f"g{position}",
                "label": f"graph-{position}"
                if graphs_by_id[old].label == f"graph-{old[1:]}"
                else graphs_by_id[old].label,
            }
        )
        for position, old in enumerate(keep_graphs, start=1)
    ]
    return kept_agents, kept_graphs
