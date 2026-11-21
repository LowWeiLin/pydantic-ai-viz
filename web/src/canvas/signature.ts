/**
 * During playback a canvas model is rebuilt on every frame, but what it shows changes only
 * when a span starts or ends. These signatures name what is on screen, so the canvases can
 * skip the frames in between (see `useSettled`).
 */
import { useMemo } from "react";
import type { Activity, CanvasModel, GraphCanvasModel } from "../data/selectors";

const act = (a: Activity): string =>
  `${a.state}:${a.calls}:${a.retries}:${a.errors}:${a.spanIds.at(-1) ?? ""}`;

export function agentModelSignature(model: CanvasModel): string {
  return JSON.stringify([
    model.subjectId,
    model.agents.map((agent) => [
      agent.blueprint.id,
      act(agent.run),
      act(agent.model),
      agent.outputReady,
      agent.toolsets.map((group) => group.tools.map((tool) => act(tool.activity))),
      agent.unlisted.map((tool) => [tool.name, act(tool.activity)]),
      Object.entries(agent.loads).map(([id, activity]) => [id, act(activity)]),
    ]),
    model.delegations.map((d) => [d.evidence, d.active]),
  ]);
}

export function graphModelSignature(model: GraphCanvasModel): string {
  return JSON.stringify([
    model.graph.id,
    act(model.run),
    model.nodes.map((entry) => [act(entry.activity), entry.passes]),
    model.edgeVisits,
    model.activeEdges,
    model.unlisted.map((entry) => [entry.nodeId, act(entry.activity)]),
  ]);
}

/**
 * Returns the same `value` object until `signature` or `source` changes. `source` is the data
 * the value was derived from (blueprints can be replaced without any activity changing).
 */
export function useSettled<T>(value: T, signature: string, source: unknown): T {
  // biome-ignore lint/correctness/useExhaustiveDependencies: the signature stands in for the value.
  return useMemo(() => value, [signature, source]);
}
