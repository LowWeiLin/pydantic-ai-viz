"""Static analysis: blueprints read from source code that is never imported or run.

``analyze`` parses Python files with the standard ``ast`` module and describes the
agents and graphs it finds. The analysed code is data: none of it is executed and
none of what it imports has to be installed.

How the pieces fit:

* ``_locate``: a target string -> the files to read.
* ``_source``: one file -> its syntax tree, scopes and bindings (or a reason it was skipped).
* ``_resolve``: a name -> what it stands for, across the files of a ``Project``.
* ``_values``, ``_schema``, ``_docstring``: literal values, JSON Schema and descriptions from syntax.
* ``_agents``, ``_graphs``: the blueprints.
"""

from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .._models import AgentBlueprint, GraphBlueprint
from ._agents import AgentExtractor
from ._graphs import GraphExtractor
from ._locate import Source, TargetError, locate
from ._resolve import END_OF_MODULE, Project, Resolver
from ._source import Module, SkippedFile, read_module

__all__ = ["Analysis", "Limits", "TargetError", "analyze"]


@dataclass(frozen=True, slots=True)
class Limits:
    """Bounds that keep the analysis of unknown code quick and finite."""

    max_file_bytes: int = 2_000_000
    """A larger file is skipped, with a notice."""
    max_files: int = 2_000
    """No more files than this are read in one analysis."""
    max_seconds: float = 30.0
    """Reading stops, with a notice, once this much time has passed."""


@dataclass(frozen=True, slots=True)
class Analysis:
    """What `analyze` found.

    Attributes:
        agents: One blueprint per `Agent(...)` construction, with ids `a1`, `a2`, ...
            in the order they appear. `delegates` refer to these ids.
        graphs: One blueprint per `GraphBuilder(...)` or `Graph(nodes=[...])`, with
            ids `g1`, `g2`, ...
        notices: Messages about the analysis as a whole, such as a file that was
            skipped because it is not valid Python.
        files: The files that were read, relative to their common directory.
    """

    agents: list[AgentBlueprint] = field(default_factory=list[AgentBlueprint])
    graphs: list[GraphBlueprint] = field(default_factory=list[GraphBlueprint])
    notices: list[str] = field(default_factory=list[str])
    files: list[str] = field(default_factory=list[str])

    @property
    def blueprints(self) -> list[AgentBlueprint | GraphBlueprint]:
        """Every blueprint: the agents, then the graphs."""
        return [*self.agents, *self.graphs]


def analyze(*targets: str | os.PathLike[str], limits: Limits | None = None) -> Analysis:
    """Describe the agents and graphs in Python source files, without importing or running them.

    The files are parsed, not executed: nothing they import has to be installed,
    no model is called and no code of theirs runs. What the source spells out is
    reported as a value; what it computes at run time is reported as the source
    text that computes it, and listed in the blueprint's `warnings`.

    ```python
    import pydantic_ai_viz as pviz

    found = pviz.analyze("agents.py")
    for agent in found.agents:
        print(agent.label, [tool.name for tool in agent.tools])
    pviz.export_html(found, "blueprint.html")
    ```

    Args:
        *targets: Paths of `.py` files. `path/to/file.py:name` selects the agent or
            graph assigned to `name` at the top level of that file, together with
            the agents it delegates to.
        limits: Bounds on file size, file count and time; the defaults suit
            ordinary projects.

    Returns:
        The blueprints found, plus notices about anything that had to be skipped.
        A file that cannot be read or parsed yields a notice, not an exception.

    Raises:
        ValueError: If a target is not an existing `.py` file, or selects a name
            that is not an agent or a graph.
    """
    limits = limits or Limits()
    sources = [locate(target) for target in targets]
    if not sources:
        return Analysis()
    deadline = time.monotonic() + limits.max_seconds
    root = Path(os.path.commonpath([source.path.parent for source in sources]))
    project = Project(root=root)
    notices: list[str] = []
    loaded: dict[Path, Module] = {}
    for source in sources:
        if source.path in loaded:
            continue
        if len(loaded) >= limits.max_files or time.monotonic() >= deadline:
            notices.append(f"stopped reading files at the limit; {source.spec} and what follows were not analysed")
            break
        rel = source.path.relative_to(root).as_posix()
        result = read_module(source.path, rel, max_bytes=limits.max_file_bytes)
        if isinstance(result, SkippedFile):
            notices.append(f"skipped {result.rel}: {result.reason}")
            continue
        loaded[source.path] = result
        project.add(result)

    resolver = Resolver(project, deadline=deadline)
    agents = AgentExtractor(project, resolver)
    graphs = GraphExtractor(project, resolver)
    try:
        agents.find()
        graphs.find()
        agent_blueprints = agents.blueprints()
        graph_blueprints = graphs.blueprints()
    except RecursionError:
        # The parser accepted the files, yet something in them nests deeper than this analysis can follow.
        notices.append("the analysis was abandoned: the source is nested too deeply")
        return Analysis(notices=notices, files=[module.rel for module in project.modules])

    if resolver.timed_out:
        notices.append(
            f"the analysis ran into its time limit of {limits.max_seconds:g} seconds; what is shown is incomplete"
        )
    selected = _selection(sources, loaded, agents, graphs, agent_blueprints, graph_blueprints)
    if selected is not None:
        agent_blueprints, graph_blueprints = selected
    return Analysis(
        agents=agent_blueprints,
        graphs=graph_blueprints,
        notices=notices,
        files=[module.rel for module in project.modules],
    )


def _selection(
    sources: list[Source],
    loaded: dict[Path, Module],
    agents: AgentExtractor,
    graphs: GraphExtractor,
    agent_blueprints: list[AgentBlueprint],
    graph_blueprints: list[GraphBlueprint],
) -> tuple[list[AgentBlueprint], list[GraphBlueprint]] | None:
    """Apply ``file.py:name`` selectors. ``None`` when no target has one, which keeps everything."""
    if all(source.select is None for source in sources):
        return None
    by_id = {blueprint.id: blueprint for blueprint in agent_blueprints}
    keep_agents: list[str] = []
    keep_graphs: list[str] = []
    for source in sources:
        module = loaded.get(source.path)
        if module is None:
            continue  # skipped with a notice
        if source.select is None:
            keep_agents.extend(b.id for b in agent_blueprints if b.location and b.location.file == module.rel)
            keep_graphs.extend(b.id for b in graph_blueprints if b.location and b.location.file == module.rel)
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
    renumbered = {old: f"a{position}" for position, old in enumerate(order, start=1)}
    keep_graphs = list(dict.fromkeys(keep_graphs))
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
