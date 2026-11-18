/**
 * Chooses the inspector panel for what is selected: a span of a run, or a part of a blueprint
 * picked on the canvas.
 */
import { memo } from "react";
import type { CanvasTarget } from "../canvas/targets";
import { useViewer } from "../data/store";
import type { GraphBlueprint } from "../wire";
import {
  AgentInspector,
  GraphInspector,
  GraphNodeInspector,
  ToolInspector,
  UnlistedToolInspector,
} from "./BlueprintInspector";
import { Note } from "./parts";
import { SpanInspector as SpanInspectorImpl } from "./SpanInspector";

/**
 * During playback the run view is rebuilt every frame. A span that has ended is the same
 * object on every frame and nothing about its panel changes, so those frames are skipped.
 */
export const SpanInspector = memo(
  SpanInspectorImpl,
  (before, after) => before.span === after.span && after.span.status !== "running",
);

/** The panel for a blueprint part. `graph` is needed for graph nodes only. */
export function BlueprintInspector({
  target,
  graph,
}: {
  target: CanvasTarget;
  graph?: GraphBlueprint | null;
}) {
  const agents = useViewer((state) => state.data.agents);
  if (target.kind === "graphNode") {
    const node = graph?.nodes.find((n) => n.id === target.nodeId);
    if (graph && node) return <GraphNodeInspector graph={graph} node={node} />;
    if (graph) return <GraphInspector graph={graph} />;
  } else {
    const agent = agents[target.agentId];
    if (agent && target.kind === "tool") {
      const tool = agent.tools.find((t) => t.name === target.name);
      return tool ? (
        <ToolInspector agent={agent} tool={tool} />
      ) : (
        <UnlistedToolInspector agent={agent} name={target.name} />
      );
    }
    if (agent) return <AgentInspector agent={agent} />;
  }
  return (
    <div className="p-3">
      <Note>This part of the blueprint is no longer in the session.</Note>
    </div>
  );
}
