/**
 * Chooses the inspector panel for what is selected: a span of a run, or a part of a blueprint
 * picked on the canvas.
 */
import { memo, useMemo } from "react";
import type { CanvasTarget } from "../canvas/targets";
import { useViewer } from "../data/store";
import type { GraphBlueprint } from "../wire";
import {
  AgentInspector,
  CapabilityInspector,
  GraphInspector,
  GraphNodeInspector,
  SkillInspector,
  ToolInspector,
  ToolsetInspector,
  UnlistedToolInspector,
} from "./BlueprintInspector";
import { Note, type Reveal } from "./parts";
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
  reveal: asked = null,
}: {
  target: CanvasTarget;
  graph?: GraphBlueprint | null;
  /** A section of the agent's panel that the screen asks to show, e.g. its warnings. */
  reveal?: Reveal | null;
}) {
  const agents = useViewer((state) => state.data.agents);
  // The output node and the model chip are parts of the agent's panel: picking one brings its
  // section into view. A new target object asks again, so a second click scrolls back to it.
  const picked = useMemo<Reveal | null>(
    () => (target.kind === "output" || target.kind === "model" ? { section: target.kind } : null),
    [target],
  );
  const reveal = asked ?? picked;
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
    if (agent && target.kind === "toolset") {
      const toolset = agent.toolsets.find((t) => t.id === target.toolsetId);
      if (toolset) return <ToolsetInspector agent={agent} toolset={toolset} />;
    }
    if (agent && target.kind === "capability") {
      const capability = agent.capabilities[target.index];
      if (capability) {
        return <CapabilityInspector agent={agent} index={target.index} capability={capability} />;
      }
    }
    if (agent && target.kind === "skill") {
      const capability = agent.capabilities[target.index];
      const skill = capability?.skills.find((s) => s.name === target.name);
      if (capability && skill) {
        return (
          <SkillInspector
            agent={agent}
            index={target.index}
            capability={capability}
            skill={skill}
          />
        );
      }
    }
    if (agent) return <AgentInspector agent={agent} reveal={reveal} />;
  }
  return (
    <div className="p-3">
      <Note>This part of the blueprint is no longer in the session.</Note>
    </div>
  );
}
