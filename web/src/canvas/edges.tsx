/**
 * Canvas edges. Line style carries meaning and is repeated in words where it matters:
 * - a moving dash: the target is running now;
 * - dashed delegation: found in code only, or not yet reached in a replay; solid: seen in a
 *   run (the label says which).
 * Loops and delegation cycles are drawn as an arc underneath the nodes, not through them.
 */
import {
  BaseEdge,
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  getBezierPath,
  MarkerType,
} from "@xyflow/react";
import { cx } from "../ui/tone";

export type EdgeState = "idle" | "active" | "done";

export type FlowEdgeData = {
  state: EdgeState;
  /** CSS colour for the active and done states. */
  tone: string;
  back: boolean;
  label: string | null;
  /** Dashed when the edge is a guess from code rather than something a run showed. */
  dashed: boolean;
};

/** How far below the lower endpoint a loop arc dips. */
const ARC_DEPTH = 72;

/** A loop edge: out of the bottom of the source, under everything, into the bottom of the target. */
export function arcPath(
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
): [path: string, labelX: number, labelY: number] {
  const low = Math.max(sourceY, targetY) + ARC_DEPTH;
  const path = `M ${sourceX},${sourceY} C ${sourceX},${low} ${targetX},${low} ${targetX},${targetY}`;
  // The curve's midpoint (t = 0.5) of a cubic with both control points at `low`.
  const labelY = (sourceY + targetY) / 8 + (low * 6) / 8;
  return [path, (sourceX + targetX) / 2, labelY];
}

/** The arrowhead for an edge in a given state. xyflow needs the colour as a string. */
export function edgeMarker(state: EdgeState, tone: string) {
  return {
    type: MarkerType.ArrowClosed,
    width: 14,
    height: 14,
    color: state === "idle" ? "var(--border-strong)" : tone,
  };
}

function EdgeLabel({
  x,
  y,
  state,
  tone,
  children,
}: {
  x: number;
  y: number;
  state: EdgeState;
  tone: string;
  children: string;
}) {
  return (
    <EdgeLabelRenderer>
      <span
        className={cx(
          "pointer-events-none absolute rounded-full border bg-bg px-1.5 py-px font-medium text-meta",
          state === "idle" ? "border-line text-muted" : "border-(--edge-tone) text-fg",
        )}
        style={{
          transform: `translate(-50%, -50%) translate(${x}px, ${y}px)`,
          ["--edge-tone" as string]: tone,
        }}
      >
        {children}
      </span>
    </EdgeLabelRenderer>
  );
}

function PathEdge(props: EdgeProps<Edge<FlowEdgeData>>) {
  const { data } = props;
  const state = data?.state ?? "idle";
  const tone = data?.tone ?? "var(--accent)";
  const [path, labelX, labelY] = data?.back
    ? arcPath(props.sourceX, props.sourceY, props.targetX, props.targetY)
    : getBezierPath({
        sourceX: props.sourceX,
        sourceY: props.sourceY,
        targetX: props.targetX,
        targetY: props.targetY,
        sourcePosition: props.sourcePosition,
        targetPosition: props.targetPosition,
      });
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        markerEnd={props.markerEnd}
        interactionWidth={0}
        className={cx(
          "pviz-edge",
          state === "active" && "pviz-edge-active",
          state === "done" && "pviz-edge-done",
          data?.dashed && state !== "active" && "pviz-edge-dashed",
        )}
        style={{ ["--edge-tone" as string]: tone }}
      />
      {data?.label && (
        <EdgeLabel x={labelX} y={labelY} state={state} tone={tone}>
          {data.label}
        </EdgeLabel>
      )}
    </>
  );
}

/** Agent to tool, agent to output, and graph edges. */
export const FlowEdge = PathEdge;

/** Tool to sub-agent. Same drawing; a separate type so the two can be styled and found apart. */
export function DelegationEdge(props: EdgeProps<Edge<FlowEdgeData>>) {
  return <PathEdge {...props} />;
}

export const edgeTypes = { flow: FlowEdge, delegation: DelegationEdge };
