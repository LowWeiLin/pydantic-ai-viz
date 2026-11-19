"""Blueprints learned from runs (spec section 3.5).

A static blueprint says what an agent is built from; spans say what it actually
did. The functions here fold the second into the first: an agent nobody registered
still gets a blueprint, tools that only exist at run time show up, and a delegation
edge that was a guess from source code becomes a fact once it is seen happening.

Everything is a pure function of blueprints and already-normalised span data. Each
returns its input object unchanged when there is nothing to add, so the caller can
tell with ``is`` whether a blueprint needs to be sent again. The session owns the
state: ids, the name index and when each rule fires.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from . import _compat
from ._introspect import AGENT_TOOLSET_ID, ToolKind
from ._models import AgentBlueprint, AgentSettings, Delegate, Instruction, ModelInfo, OutputInfo, Tool, Toolset
from ._normalize import clip

_TOOL_KINDS: frozenset[str] = frozenset({"function", "output", "external", "unapproved"})
_UNKNOWN_TOOLSET_LABEL = "Observed tools"


def observed_blueprint(id: str, name: str, *, description: str | None, model_name: str | None) -> AgentBlueprint:
    """The blueprint of an agent known only from its ``invoke_agent`` span."""
    return AgentBlueprint(
        id=id,
        name=name,
        label=name or id,
        description=description,
        source="observed",
        location=None,
        model=None
        if model_name is None
        else ModelInfo(
            id=None, name=model_name, provider=None, offline=False, expression=None, model_class=None, env=None
        ),
        deps_type=None,
        instructions=[],
        toolsets=[],
        tools=[],
        native_tools=[],
        output=OutputInfo(
            mode="unknown", allows_text=None, type_name=None, json_schema=None, tool_names=[], location=None
        ),
        capabilities=[],
        settings=AgentSettings(
            model_settings=None,
            end_strategy=None,
            tool_retries=None,
            output_retries=None,
            tool_timeout=None,
            expressions={},
        ),
        delegates=[],
        warnings=[],
    )


def _toolset_for(toolsets: list[Toolset], toolset_id: str | None) -> Toolset:
    """The toolset an observed tool belongs to, appended to ``toolsets`` if it is new."""
    for toolset in toolsets:
        if toolset.toolset_id == toolset_id and (toolset_id is not None or toolset.label == _UNKNOWN_TOOLSET_LABEL):
            return toolset
    if toolset_id == AGENT_TOOLSET_ID:
        label = "Agent tools"
    elif toolset_id is None:
        label = _UNKNOWN_TOOLSET_LABEL
    else:
        label = f"Toolset {toolset_id!r}"
    toolset = Toolset(
        id=f"ts{len(toolsets)}",
        toolset_id=toolset_id,
        label=label,
        kind="agent" if toolset_id == AGENT_TOOLSET_ID else "other",
        wrappers=[],
        opaque=False,
        expression=None,
        location=None,
    )
    toolsets.append(toolset)
    return toolset


def _observed_tool(raw: Mapping[str, Any], name: str, toolset: str) -> Tool:
    description = raw.get("description")
    kind = raw.get("kind")
    return Tool(
        name=name,
        toolset=toolset,
        description=description if isinstance(description, str) else None,
        parameters_schema=clip(raw.get("parameters_json_schema")),
        return_schema=clip(raw.get("return_schema")),
        kind=cast("ToolKind", kind) if kind in _TOOL_KINDS else "function",
        # Nothing about the Python function behind a tool is visible in a span.
        takes_ctx=None,
        is_async=None,
        max_retries=None,
        function=None,
        origin="observed",
        location=None,
        library_version=None,
    )


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast("dict[str, Any]", item) for item in cast("list[Any]", value) if isinstance(item, dict)]


def merge_request(
    blueprint: AgentBlueprint,
    parameters: Mapping[str, Any],
    *,
    instructions: str | None,
    provider: str | None,
) -> AgentBlueprint:
    """Fold one model request's ``model_request_parameters`` into ``blueprint`` (rule 2).

    Adds tools the blueprint does not list, fills in output facts that are still
    unknown, and gives an observed blueprint the resolved instructions and the
    model provider. What static introspection already established is never changed.
    """
    update: dict[str, Any] = {}

    toolsets = list(blueprint.toolsets)
    tools = list(blueprint.tools)
    known = {tool.name for tool in tools}
    for raw in _dicts(parameters.get("function_tools")):
        name = raw.get("name")
        if isinstance(name, str) and name not in known:
            toolset_id = raw.get("toolset_id")
            toolset = _toolset_for(toolsets, toolset_id if isinstance(toolset_id, str) else None)
            tools.append(_observed_tool(raw, name, toolset.id))
            known.add(name)
    if len(tools) != len(blueprint.tools):
        update["tools"] = tools
        update["toolsets"] = toolsets

    output = blueprint.output
    output_update: dict[str, Any] = {}
    names = [raw["name"] for raw in _dicts(parameters.get("output_tools")) if isinstance(raw.get("name"), str)]
    mode = parameters.get("output_mode")
    allows_text = parameters.get("allow_text_output")
    if not output.tool_names and names:
        output_update["tool_names"] = names
    if output.mode == "unknown" and isinstance(mode, str):
        output_update["mode"] = mode
    if output.allows_text is None and isinstance(allows_text, bool):
        output_update["allows_text"] = allows_text
    if output_update:
        update["output"] = output.model_copy(update=output_update)

    if blueprint.source == "observed":
        if not blueprint.instructions and instructions:
            update["instructions"] = [
                Instruction(
                    kind="instructions", dynamic=False, text=instructions, function=None, expression=None, location=None
                )
            ]
        model = blueprint.model
        if model is not None and model.provider is None and provider is not None:
            offline = provider in _compat.OFFLINE_PROVIDERS
            update["model"] = model.model_copy(update={"provider": provider, "offline": offline})

    return blueprint.model_copy(update=update) if update else blueprint


def add_delegate(blueprint: AgentBlueprint, tool_name: str, agent_id: str) -> AgentBlueprint:
    """Record that ``tool_name`` was seen running the agent ``agent_id`` (rule 3)."""
    delegates = list(blueprint.delegates)
    for index, delegate in enumerate(delegates):
        if delegate.tool_name == tool_name and delegate.agent_id == agent_id:
            if delegate.evidence == "observed":
                return blueprint
            delegates[index] = delegate.model_copy(update={"evidence": "observed"})
            break
    else:
        delegates.append(Delegate(tool_name=tool_name, agent_id=agent_id, evidence="observed"))
    return blueprint.model_copy(update={"delegates": delegates})


def carry_observed(previous: AgentBlueprint, fresh: AgentBlueprint) -> AgentBlueprint:
    """Keep what runs have shown when ``fresh`` (static) replaces ``previous`` under the same id (rule 4)."""
    result = fresh
    for delegate in previous.delegates:
        if delegate.evidence == "observed":
            result = add_delegate(result, delegate.tool_name, delegate.agent_id)

    toolsets = list(result.toolsets)
    tools = list(result.tools)
    known = {tool.name for tool in tools}
    previous_toolsets = {toolset.id: toolset for toolset in previous.toolsets}
    for tool in previous.tools:
        if tool.origin == "observed" and tool.name not in known:
            origin = previous_toolsets.get(tool.toolset)
            toolset = _toolset_for(toolsets, None if origin is None else origin.toolset_id)
            tools.append(tool.model_copy(update={"toolset": toolset.id}))
    update: dict[str, Any] = {}
    if len(tools) != len(result.tools):
        update = {"tools": tools, "toolsets": toolsets}

    output_update: dict[str, Any] = {}
    if result.output.mode == "unknown" and previous.output.mode != "unknown":
        output_update["mode"] = previous.output.mode
    if result.output.allows_text is None and previous.output.allows_text is not None:
        output_update["allows_text"] = previous.output.allows_text
    if not result.output.tool_names and previous.output.tool_names:
        output_update["tool_names"] = previous.output.tool_names
    if output_update:
        update["output"] = result.output.model_copy(update=output_update)
    return result.model_copy(update=update) if update else result
