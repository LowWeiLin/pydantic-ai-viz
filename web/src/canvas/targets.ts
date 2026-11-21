/**
 * What a click on the canvas means. A target names a blueprint part; the screens turn it into
 * a selection (a blueprint part on blueprint screens, the latest matching span on a run).
 */
import type { DataState } from "../data/reduce";
import {
  type Activity,
  blueprintForAgentName,
  type CanvasModel,
  type GraphCanvasModel,
  type RunView,
} from "../data/selectors";
import type { AgentBlueprint } from "../wire";

export type CanvasTarget =
  | { kind: "agent"; agentId: string }
  /** The model chip on an agent card. */
  | { kind: "model"; agentId: string }
  | { kind: "tool"; agentId: string; name: string }
  /** The box that groups a toolset's tools; its placeholder row picks it too. */
  | { kind: "toolset"; agentId: string; toolsetId: string }
  | { kind: "output"; agentId: string }
  /** A capability the canvas lists under its agent; `index` is into `blueprint.capabilities`. */
  | { kind: "capability"; agentId: string; index: number }
  | { kind: "graphNode"; nodeId: string };

export const agentNodeId = (agentId: string): string => `agent:${agentId}`;
export const toolNodeId = (agentId: string, name: string): string => `tool:${agentId}:${name}`;
export const toolsetNodeId = (agentId: string, toolsetId: string): string =>
  `toolset:${agentId}:${toolsetId}`;
export const outputNodeId = (agentId: string): string => `output:${agentId}`;
export const capabilityNodeId = (agentId: string, index: number): string =>
  `capability:${agentId}:${index}`;
export const graphNodeId = (nodeId: string): string => `node:${nodeId}`;

/** The canvas node a target lives on. The model chip is part of its agent's card. */
export function targetNodeId(target: CanvasTarget): string {
  switch (target.kind) {
    case "agent":
    case "model":
      return agentNodeId(target.agentId);
    case "tool":
      return toolNodeId(target.agentId, target.name);
    case "toolset":
      return toolsetNodeId(target.agentId, target.toolsetId);
    case "output":
      return outputNodeId(target.agentId);
    case "capability":
      return capabilityNodeId(target.agentId, target.index);
    case "graphNode":
      return graphNodeId(target.nodeId);
  }
}

export const sameTarget = (a: CanvasTarget | null, b: CanvasTarget | null): boolean =>
  a !== null && b !== null && a.kind === b.kind && targetNodeId(a) === targetNodeId(b);

/**
 * A part of an agent as the `?part=` of the agent's link: "tool:<name>", "toolset:<id>",
 * "capability:<index>", "model" or "output". The agent itself, and a graph node, have none.
 */
export function partOf(target: CanvasTarget): string | undefined {
  switch (target.kind) {
    case "tool":
      return `tool:${target.name}`;
    case "toolset":
      return `toolset:${target.toolsetId}`;
    case "capability":
      return `capability:${target.index}`;
    case "model":
    case "output":
      return target.kind;
    case "agent":
    case "graphNode":
      return undefined;
  }
}

/** The part of agent `agentId` that a `?part=` names, as `partOf` writes it; null for none. */
export function parsePart(agentId: string, part: string | undefined): CanvasTarget | null {
  if (part === "model" || part === "output") return { kind: part, agentId };
  const at = part?.indexOf(":") ?? -1;
  if (part === undefined || at === -1) return null;
  const kind = part.slice(0, at);
  const name = part.slice(at + 1);
  if (name === "") return null;
  if (kind === "tool") return { kind: "tool", agentId, name };
  if (kind === "toolset") return { kind: "toolset", agentId, toolsetId: name };
  if (kind === "capability" && /^\d+$/.test(name)) {
    const index = Number(name);
    return Number.isSafeInteger(index) ? { kind: "capability", agentId, index } : null;
  }
  return null;
}

/**
 * Whether the agent has the tool, toolset or capability a target names. A link from another
 * session, or one edited by hand, may name one that is not there; the agent itself is shown
 * instead.
 */
export function agentHas(agent: AgentBlueprint, target: CanvasTarget): boolean {
  switch (target.kind) {
    case "tool":
      return agent.tools.some((tool) => tool.name === target.name);
    case "toolset":
      return agent.toolsets.some((toolset) => toolset.id === target.toolsetId);
    case "capability":
      return target.agentId === agent.id && target.index < agent.capabilities.length;
    case "graphNode":
      return false;
    default:
      return target.agentId === agent.id;
  }
}

/**
 * Where a span shows on a canvas: its own node first, then the nodes of its ancestors. A canvas
 * highlights the first target it actually draws, so a span of a sub-agent that is not on the
 * canvas still lights the tool or graph node it ran under.
 */
export function spanTargets(data: DataState, view: RunView, spanId: string): CanvasTarget[] {
  const targets: CanvasTarget[] = [];
  const seen = new Set<string>();
  let span = view.spans[spanId];
  while (span && !seen.has(span.span_id)) {
    seen.add(span.span_id);
    if (span.kind === "graph_node") {
      targets.push({ kind: "graphNode", nodeId: span.data.node_id });
    } else {
      const agent = blueprintForAgentName(data, span.agent_name);
      if (agent) {
        if (span.kind === "agent_run") targets.push({ kind: "agent", agentId: agent.id });
        else if (span.kind === "model_request") targets.push({ kind: "model", agentId: agent.id });
        else if (span.kind === "tool_call") {
          targets.push({ kind: "tool", agentId: agent.id, name: span.data.tool_name });
        }
      }
    }
    span = span.parent_id === null ? undefined : view.spans[span.parent_id];
  }
  return targets;
}

function agentActivity(model: CanvasModel, target: CanvasTarget): Activity | null {
  if (target.kind === "graphNode") return null;
  const agent = model.agents.find((a) => a.blueprint.id === target.agentId);
  if (!agent) return null;
  switch (target.kind) {
    // A toolset or a capability is never a span of its own: picking one shows its blueprint,
    // also in a run.
    case "toolset":
    case "capability":
      return null;
    case "agent":
    case "output":
      return agent.run;
    case "model":
      return agent.model;
    case "tool": {
      for (const group of agent.toolsets) {
        const hit = group.tools.find((t) => t.tool.name === target.name);
        if (hit) return hit.activity;
      }
      return agent.unlisted.find((t) => t.name === target.name)?.activity ?? null;
    }
  }
}

/** The span a click on this target selects during a run: the latest one that touched it. */
export function latestSpanFor(
  model: CanvasModel | GraphCanvasModel,
  target: CanvasTarget,
): string | null {
  if ("graph" in model) {
    if (target.kind !== "graphNode") return null;
    const activity =
      model.nodes.find((n) => n.node.id === target.nodeId)?.activity ??
      model.unlisted.find((n) => n.nodeId === target.nodeId)?.activity;
    return activity?.spanIds.at(-1) ?? null;
  }
  return agentActivity(model, target)?.spanIds.at(-1) ?? null;
}
