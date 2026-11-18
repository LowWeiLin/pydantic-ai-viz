/**
 * The agent blueprint canvas: the subject agent, its tools grouped by toolset, its output, and
 * the agents it can reach through delegation. With `overlay` the same picture shows a run: the
 * model chip pulses during a request, tools show their state and call count, edges move while
 * their target is running, and the output lights when the agent finishes.
 */
import type { Edge, Node } from "@xyflow/react";
import { type ReactNode, useMemo } from "react";
import type { CanvasAgent, CanvasModel } from "../data/selectors";
import { CanvasFrame } from "./CanvasFrame";
import { type EdgeState, edgeMarker, type FlowEdgeData } from "./edges";
import { agentLayoutKey, type LayoutNode, layoutAgentCanvas, SEEN_GROUP } from "./layout";
import {
  type AgentNodeData,
  agentNodeTypes,
  Legend,
  type OutputNodeData,
  type ToolNodeData,
  type ToolsetNodeData,
} from "./nodes";
import { type CanvasTarget, targetNodeId } from "./targets";

function toolEntry(agent: CanvasAgent, name: string) {
  for (const group of agent.toolsets) {
    const hit = group.tools.find((entry) => entry.tool.name === name);
    if (hit) return hit;
  }
  const seen = agent.unlisted.find((entry) => entry.name === name);
  return seen ? { tool: null, activity: seen.activity } : null;
}

function nodeData(
  node: LayoutNode,
  agent: CanvasAgent,
  model: CanvasModel,
  chosen: CanvasTarget | null,
  overlay: boolean,
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
      return { toolset: group?.toolset ?? null, count } satisfies ToolsetNodeData;
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
    case "placeholder":
      return {};
  }
}

/** Builds what React Flow draws from a layout and the current model. Exported for tests. */
export function buildAgentFlow(
  model: CanvasModel,
  overlay: boolean,
  highlight: readonly CanvasTarget[],
  layout = layoutAgentCanvas(model),
): { nodes: Node[]; edges: Edge<FlowEdgeData>[] } {
  const agents = new Map(model.agents.map((agent) => [agent.blueprint.id, agent]));
  const ids = new Set(layout.nodes.map((node) => node.id));
  const chosen = highlight.find((target) => ids.has(targetNodeId(target))) ?? null;

  const nodes: Node[] = [];
  for (const node of layout.nodes) {
    const agent = agents.get(node.agentId);
    const data = agent && nodeData(node, agent, model, chosen, overlay);
    if (!data) continue;
    nodes.push({
      id: node.id,
      type: node.kind,
      position: { x: node.x, y: node.y },
      width: node.width,
      height: node.height,
      data,
      ...(node.parentId ? { parentId: node.parentId, extent: "parent" as const } : {}),
      // Toolset boxes sit behind their rows and take no clicks.
      ...(node.kind === "toolset" ? { zIndex: -1, style: { pointerEvents: "none" as const } } : {}),
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
      targetHandle: edge.back ? "loop-in" : "in",
      ...(edge.kind === "flow" ? {} : { markerEnd: edgeMarker(state, tone) }),
      data: { state, tone, back: edge.back, label, dashed },
    });
  }
  return { nodes, edges };
}

function LegendLine({ dashed, children }: { dashed: boolean; children: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <svg aria-hidden="true" width="22" height="6">
        <path
          d="M0 3h22"
          stroke={dashed ? "var(--border-strong)" : "var(--kind-agent)"}
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
  const layoutKey = agentLayoutKey(model);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key changes exactly when the layout would.
  const layout = useMemo(() => layoutAgentCanvas(model), [layoutKey]);
  const { nodes, edges } = useMemo(
    () => buildAgentFlow(model, overlay, highlight, layout),
    [layout, model, overlay, highlight],
  );

  const subject = model.agents.find((agent) => agent.blueprint.id === model.subjectId);
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
        model.delegations.length > 0 ? (
          <Legend>
            <LegendLine dashed>delegation found in code</LegendLine>
            <LegendLine dashed={false}>delegation seen in a run</LegendLine>
          </Legend>
        ) : undefined
      }
    />
  );
}

const NO_HIGHLIGHT: readonly CanvasTarget[] = [];
