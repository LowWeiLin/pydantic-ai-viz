/**
 * The graph blueprint canvas: start and end as pills, decisions as diamonds, steps as cards,
 * with edge labels. With `overlay` it shows a run: the active node pulses, each node carries
 * its visit count, and the edges taken so far are drawn in the accent colour.
 */
import type { Edge, Node } from "@xyflow/react";
import { type ReactNode, useMemo } from "react";
import type { GraphCanvasModel, GraphCanvasNode } from "../data/selectors";
import { CanvasFrame } from "./CanvasFrame";
import { type EdgeState, edgeMarker, type FlowEdgeData } from "./edges";
import { graphLayoutKey, layoutGraph } from "./layout";
import { type GraphNodeData, graphNodeTypes } from "./nodes";
import { type CanvasTarget, targetNodeId } from "./targets";

const NO_HIGHLIGHT: readonly CanvasTarget[] = [];

const idleActivity = { state: "idle" as const, calls: 0, retries: 0, errors: 0, spanIds: [] };

/** Builds what React Flow draws from a layout and the current model. Exported for tests. */
export function buildGraphFlow(
  model: GraphCanvasModel,
  overlay: boolean,
  highlight: readonly CanvasTarget[],
  layout = layoutGraph(
    model.graph,
    model.unlisted.map((n) => n.nodeId),
  ),
): { nodes: Node[]; edges: Edge<FlowEdgeData>[] } {
  const entries = new Map<string, GraphCanvasNode>(model.nodes.map((n) => [n.node.id, n]));
  for (const extra of model.unlisted) {
    entries.set(extra.nodeId, {
      node: { id: extra.nodeId, kind: "step", label: null, note: null },
      activity: extra.activity,
      passes: 0,
    });
  }
  const ids = new Set(layout.nodes.map((node) => node.id));
  const chosen = highlight.find((target) => ids.has(targetNodeId(target))) ?? null;

  const nodes: Node[] = layout.nodes.map((node) => ({
    id: node.id,
    type:
      node.kind === "start" || node.kind === "end"
        ? "terminal"
        : node.kind === "decision"
          ? "decision"
          : "step",
    position: { x: node.x, y: node.y },
    width: node.width,
    height: node.height,
    data: {
      entry: entries.get(node.nodeId) ?? {
        node: { id: node.nodeId, kind: node.kind, label: null, note: null },
        activity: idleActivity,
        passes: 0,
      },
      selected: chosen !== null && targetNodeId(chosen) === node.id,
      overlay,
    } satisfies GraphNodeData,
  }));

  const active = new Set(model.activeEdges);
  const edges: Edge<FlowEdgeData>[] = layout.edges.map((edge) => {
    const state: EdgeState = !overlay
      ? "idle"
      : active.has(edge.index)
        ? "active"
        : (model.edgeVisits[edge.index] ?? 0) > 0
          ? "done"
          : "idle";
    const tone = state === "active" ? "var(--accent)" : "var(--ok)";
    return {
      id: edge.id,
      type: "flow",
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.back ? "loop-out" : "out",
      targetHandle: edge.back ? "loop-in" : "in",
      markerEnd: edgeMarker(state, tone),
      data: { state, tone, back: edge.back, label: edge.label, dashed: false },
    };
  });
  return { nodes, edges };
}

export function GraphCanvas({
  model,
  overlay = false,
  highlight = NO_HIGHLIGHT,
  onPick,
  onClear,
  topLeft,
}: {
  model: GraphCanvasModel;
  overlay?: boolean;
  highlight?: readonly CanvasTarget[];
  onPick: (target: CanvasTarget) => void;
  onClear?: () => void;
  topLeft?: ReactNode;
}) {
  const extra = model.unlisted.map((n) => n.nodeId);
  const layoutKey = graphLayoutKey(model.graph, extra);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key changes exactly when the layout would.
  const layout = useMemo(() => layoutGraph(model.graph, extra), [layoutKey]);
  const { nodes, edges } = useMemo(
    () => buildGraphFlow(model, overlay, highlight, layout),
    [layout, model, overlay, highlight],
  );
  return (
    <CanvasFrame
      label={`${overlay ? "Run on the graph" : "Graph"} ${model.graph.label}`}
      nodes={nodes}
      edges={edges}
      nodeTypes={graphNodeTypes}
      layoutKey={layoutKey}
      onPick={onPick}
      onClear={onClear}
      topLeft={topLeft}
    />
  );
}
