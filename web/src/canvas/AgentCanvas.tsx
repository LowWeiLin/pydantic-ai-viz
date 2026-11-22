/**
 * The agent blueprint canvas: the subject agent, its tools grouped by toolset, its output, its
 * capabilities, and the agents it can reach through delegation. With `overlay` the same picture
 * shows a run: the model chip pulses during a request, tools show their state and call count,
 * edges move while their target is running, and the output lights when the agent finishes.
 */
import type { Edge, Node } from "@xyflow/react";
import { type ReactNode, useMemo } from "react";
import {
  defaultCapability,
  LIBRARY_TOOL_NAMES,
  libraryToolsets,
  shownCapabilities,
} from "../data/model";
import { type Activity, type CanvasAgent, type CanvasModel, fileName } from "../data/selectors";
import { useShowDefaults } from "../shell/defaults";
import { plural } from "../shell/format";
import { CanvasFrame } from "./CanvasFrame";
import { type EdgeState, edgeMarker, type FlowEdgeData } from "./edges";
import { agentLayoutKey, type LayoutNode, layoutAgentCanvas, SEEN_GROUP } from "./layout";
import {
  type AgentNodeData,
  agentNodeTypes,
  type CapabilitiesNodeData,
  type CapabilityNodeData,
  type HookNodeData,
  Legend,
  type OutputNodeData,
  type PlaceholderNodeData,
  type SkillNodeData,
  type ToolNodeData,
  type ToolsetNodeData,
} from "./nodes";
import { type CanvasTarget, targetNodeId } from "./targets";

/** A capability's edge to the toolset it gives: quieter than the edges of a run. */
const GIVES_TONE = "color-mix(in srgb, var(--kind-capability) 70%, transparent)";

function toolEntry(agent: CanvasAgent, name: string) {
  for (const group of agent.toolsets) {
    const hit = group.tools.find((entry) => entry.tool.name === name);
    if (hit) return hit;
  }
  const seen = agent.unlisted.find((entry) => entry.name === name);
  return seen ? { tool: null, activity: seen.activity } : null;
}

const NO_CALLS: Activity = { state: "idle", calls: 0, retries: 0, errors: 0, spanIds: [] };

/**
 * Activities as one: every call counted, and the state of the most urgent (running, then
 * failed, then waiting for a retry).
 */
function mergedActivity(list: readonly Activity[]): Activity {
  const rank = ["idle", "ok", "retry", "error", "running"] as const;
  return list.reduce(
    (sum, a) => ({
      state: rank.indexOf(a.state) > rank.indexOf(sum.state) ? a.state : sum.state,
      calls: sum.calls + a.calls,
      retries: sum.retries + a.retries,
      errors: sum.errors + a.errors,
      spanIds: [...sum.spanIds, ...a.spanIds],
    }),
    NO_CALLS,
  );
}

/**
 * What a capability row says of the parts that have no row of their own: the toolsets it gives
 * that are hidden among the defaults, the members of its stack that are, and in a run the calls
 * that loaded it on demand or went to those hidden tools.
 */
function capabilityData(
  agent: CanvasAgent,
  index: number,
  showDefaults: boolean,
): Pick<CapabilityNodeData, "toolsets" | "hiddenTools" | "inside" | "activity" | "default"> {
  const { blueprint } = agent;
  const capability = blueprint.capabilities[index];
  if (!capability)
    return { toolsets: [], hiddenTools: null, inside: 0, activity: null, default: false };
  const library = showDefaults ? new Set<string>() : libraryToolsets(blueprint);
  // A stack's members follow its entry, which stands for them and the toolsets they give.
  const after = blueprint.capabilities.slice(index + 1);
  const end = after.findIndex((item) => item.stack !== capability.name);
  const members = capability.stack === null ? (end === -1 ? after.length : end) : 0;
  const given = [capability, ...after.slice(0, members)].flatMap((item) => item.toolsets);
  const hidden = agent.toolsets.filter(
    (group) =>
      given.includes(group.toolset.id) &&
      library.has(group.toolset.id) &&
      (group.tools.length > 0 || group.toolset.opaque),
  );
  const tools = hidden.flatMap((group) => group.tools.map((tool) => tool.activity));
  const loaded =
    capability.deferred && capability.capability_id !== null
      ? (agent.loads[capability.capability_id] ?? NO_CALLS)
      : null;
  return {
    toolsets: capability.toolsets
      .filter((id) => !library.has(id))
      .map((id) => blueprint.toolsets.find((toolset) => toolset.id === id)?.label ?? id),
    hiddenTools:
      hidden.length === 0
        ? null
        : tools.length > 0
          ? plural(tools.length, "tool")
          : "tools at run time",
    inside: showDefaults ? 0 : members,
    activity:
      loaded !== null || hidden.length > 0
        ? mergedActivity([...(loaded ? [loaded] : []), ...tools])
        : null,
    default: showDefaults && defaultCapability(capability),
  };
}

function nodeData(
  node: LayoutNode,
  agent: CanvasAgent,
  model: CanvasModel,
  chosen: CanvasTarget | null,
  overlay: boolean,
  showDefaults: boolean,
): Record<string, unknown> | null {
  const isChosen = chosen !== null && targetNodeId(chosen) === node.id;
  switch (node.kind) {
    case "agent":
      return {
        agent,
        subject: agent.blueprint.id === model.subjectId,
        selected: isChosen ? (chosen.kind === "model" ? "model" : "card") : null,
        overlay,
      } satisfies AgentNodeData;
    case "toolset": {
      const group = agent.toolsets.find((g) => g.toolset.id === node.ref);
      const count =
        node.ref === SEEN_GROUP
          ? agent.unlisted.length
          : (group?.tools.length ?? 0) + (group?.toolset.opaque ? agent.unlisted.length : 0);
      return {
        agentId: node.agentId,
        toolset: group?.toolset ?? null,
        count,
        selected: isChosen,
        default: showDefaults && libraryToolsets(agent.blueprint).has(node.ref),
      } satisfies ToolsetNodeData;
    }
    case "tool": {
      const entry = toolEntry(agent, node.ref);
      if (!entry) return null;
      return {
        agentId: node.agentId,
        name: node.ref,
        tool: entry.tool,
        activity: entry.activity,
        selected: isChosen,
        overlay,
        delegates: model.delegations.some(
          (d) => d.fromAgentId === node.agentId && d.toolName === node.ref,
        ),
        default: showDefaults && entry.tool === null && LIBRARY_TOOL_NAMES.has(node.ref),
      } satisfies ToolNodeData;
    }
    case "output":
      return {
        agentId: node.agentId,
        agentLabel: agent.blueprint.label,
        mode: agent.blueprint.output.mode,
        typeName: agent.blueprint.output.type_name,
        ready: overlay && agent.outputReady,
        selected: isChosen,
        overlay,
      } satisfies OutputNodeData;
    case "placeholder": {
      const toolset = agent.toolsets.find((g) => g.toolset.id === node.ref)?.toolset;
      if (!toolset) return null;
      return {
        agentId: node.agentId,
        toolsetId: toolset.id,
        toolsetLabel: toolset.label,
      } satisfies PlaceholderNodeData;
    }
    case "capabilities":
      return {
        count: shownCapabilities(agent.blueprint, showDefaults).length,
      } satisfies CapabilitiesNodeData;
    case "capability": {
      const index = Number(node.ref);
      const capability = agent.blueprint.capabilities[index];
      if (!capability) return null;
      return {
        agentId: node.agentId,
        index,
        capability,
        ...capabilityData(agent, index, showDefaults),
        overlay,
        selected: isChosen,
      } satisfies CapabilityNodeData;
    }
    case "skill": {
      const at = node.ref.indexOf(":");
      const index = Number(node.ref.slice(0, at));
      const owner = agent.blueprint.capabilities[index];
      const skill = owner?.skills.find((s) => s.name === node.ref.slice(at + 1));
      if (!owner || !skill) return null;
      return {
        agentId: node.agentId,
        index,
        owner: owner.name,
        skill,
        activity: agent.loads[skill.name] ?? NO_CALLS,
        overlay,
        selected: isChosen,
      } satisfies SkillNodeData;
    }
    case "hook": {
      const [index = -1, ordinal = -1] = node.ref.split(":").map(Number);
      const owner = agent.blueprint.capabilities[index];
      const hook = owner?.hooks[ordinal];
      if (!owner || !hook) return null;
      return {
        agentId: node.agentId,
        index,
        ordinal,
        owner: owner.name,
        hook,
        selected: isChosen,
      } satisfies HookNodeData;
    }
  }
}

/** Builds what React Flow draws from a layout and the current model. Exported for tests. */
export function buildAgentFlow(
  model: CanvasModel,
  overlay: boolean,
  highlight: readonly CanvasTarget[],
  showDefaults = false,
  layout = layoutAgentCanvas(model, showDefaults),
): { nodes: Node[]; edges: Edge<FlowEdgeData>[] } {
  const agents = new Map(model.agents.map((agent) => [agent.blueprint.id, agent]));
  const ids = new Set(layout.nodes.map((node) => node.id));
  const chosen = highlight.find((target) => ids.has(targetNodeId(target))) ?? null;

  const nodes: Node[] = [];
  for (const node of layout.nodes) {
    const agent = agents.get(node.agentId);
    const data = agent && nodeData(node, agent, model, chosen, overlay, showDefaults);
    if (!data) continue;
    nodes.push({
      id: node.id,
      type: node.kind,
      position: { x: node.x, y: node.y },
      width: node.width,
      height: node.height,
      data,
      ...(node.parentId ? { parentId: node.parentId, extent: "parent" as const } : {}),
      // Toolset boxes sit behind their rows. Only the box's own button takes clicks, so the
      // group of tools seen in a run and the box of capabilities, which have none, let them
      // through to the canvas.
      ...(node.kind === "toolset" || node.kind === "capabilities"
        ? { zIndex: -1, style: { pointerEvents: "none" as const } }
        : {}),
    });
  }

  const edges: Edge<FlowEdgeData>[] = [];
  for (const edge of layout.edges) {
    const agent = agents.get(edge.agentId);
    if (!agent) continue;
    let state: EdgeState = "idle";
    let tone = "var(--kind-tool)";
    let dashed = false;
    let label: string | null = null;
    if (edge.kind === "flow") {
      const activity = toolEntry(agent, edge.ref)?.activity;
      if (overlay && activity?.state === "running") {
        state = "active";
        tone = "var(--accent)";
      } else if (overlay && activity && activity.calls > 0) {
        state = "done";
      }
    } else if (edge.kind === "output") {
      tone = "var(--kind-output)";
      if (overlay && agent.outputReady) state = "done";
    } else if (edge.kind === "gives") {
      // Structure, not activity: always in the capability colour, never moving.
      tone = GIVES_TONE;
      dashed = true;
      state = "done";
    } else {
      const delegation = model.delegations.find(
        (d) =>
          d.fromAgentId === edge.agentId &&
          d.toolName === edge.ref &&
          d.toAgentId === edge.toAgentId,
      );
      tone = "var(--kind-agent)";
      dashed = delegation?.evidence !== "observed";
      label =
        delegation?.evidence === "observed"
          ? "in a run"
          : delegation?.evidence === "pending"
            ? "not yet"
            : "in code";
      // A delegation that crosses files says where it lands.
      const from = agent.blueprint.location?.file;
      const to = agents.get(edge.toAgentId ?? "")?.blueprint.location?.file;
      if (to !== undefined && to !== from) label += ` · ${fileName(to)}`;
      if (overlay && delegation?.active) {
        state = "active";
        tone = "var(--accent)";
      } else if (!dashed) {
        state = "done";
      }
    }
    edges.push({
      id: edge.id,
      type: edge.kind === "delegation" ? "delegation" : "flow",
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.back ? "loop-out" : "out",
      targetHandle: edge.back ? "loop-in" : edge.lane !== null ? "down" : "in",
      ...(edge.kind === "flow" || edge.kind === "gives"
        ? {}
        : { markerEnd: edgeMarker(state, tone) }),
      data: { state, tone, back: edge.back, label, dashed, lane: edge.lane, floor: edge.floor },
    });
  }
  return { nodes, edges };
}

function LegendLine({
  dashed,
  stroke = dashed ? "var(--border-strong)" : "var(--kind-agent)",
  children,
}: {
  dashed: boolean;
  stroke?: string;
  children: string;
}) {
  return (
    <li className="flex items-center gap-1.5">
      <svg aria-hidden="true" width="22" height="6">
        <path
          d="M0 3h22"
          stroke={stroke}
          strokeWidth="1.5"
          strokeDasharray={dashed ? "4 4" : undefined}
        />
      </svg>
      {children}
    </li>
  );
}

export function AgentCanvas({
  model,
  overlay = false,
  highlight = NO_HIGHLIGHT,
  onPick,
  onClear,
  topLeft,
}: {
  model: CanvasModel;
  /** Show run activity (a run view). Without it the canvas is the plain blueprint. */
  overlay?: boolean;
  /** Candidates for the selected node, best first; the first one on the canvas is outlined. */
  highlight?: readonly CanvasTarget[];
  onPick: (target: CanvasTarget) => void;
  onClear?: () => void;
  topLeft?: ReactNode;
}) {
  const showDefaults = useShowDefaults();
  const layoutKey = agentLayoutKey(model, showDefaults);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key changes exactly when the layout would.
  const layout = useMemo(() => layoutAgentCanvas(model, showDefaults), [layoutKey]);
  const { nodes, edges } = useMemo(
    () => buildAgentFlow(model, overlay, highlight, showDefaults, layout),
    [layout, model, overlay, highlight, showDefaults],
  );

  const subject = model.agents.find((agent) => agent.blueprint.id === model.subjectId);
  // A blueprint's key explains only the line styles that are on it. A run can turn a dashed
  // line solid at any moment, so there the key always has both.
  const seen = overlay || model.delegations.some((d) => d.evidence === "observed");
  const written = overlay || model.delegations.some((d) => d.evidence !== "observed");
  const gives = layout.edges.some((edge) => edge.kind === "gives");
  return (
    <CanvasFrame
      label={`${overlay ? "Run on the blueprint" : "Blueprint"} of ${subject?.blueprint.label ?? "agent"}`}
      nodes={nodes}
      edges={edges}
      nodeTypes={agentNodeTypes}
      layoutKey={layoutKey}
      onPick={onPick}
      onClear={onClear}
      topLeft={topLeft}
      bottomRight={
        model.delegations.length > 0 || gives ? (
          <Legend>
            {model.delegations.length > 0 && written && (
              <LegendLine dashed>delegation found in code</LegendLine>
            )}
            {model.delegations.length > 0 && seen && (
              <LegendLine dashed={false}>delegation seen in a run</LegendLine>
            )}
            {gives && (
              <LegendLine dashed stroke={GIVES_TONE}>
                capability gives toolset
              </LegendLine>
            )}
          </Legend>
        ) : undefined
      }
    />
  );
}

const NO_HIGHLIGHT: readonly CanvasTarget[] = [];
