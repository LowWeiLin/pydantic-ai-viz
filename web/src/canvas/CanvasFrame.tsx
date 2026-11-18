/**
 * The shared canvas shell: a read-only React Flow with the viewer's controls. The agent and
 * graph canvases build nodes and edges and hand them here.
 *
 * Nothing on the canvas can be dragged, connected or deleted; it is a picture of the code.
 * React Flow's own keyboard handling is off because the arrow keys belong to playback.
 */
import {
  Background,
  BackgroundVariant,
  type Edge,
  type Node,
  type NodeTypes,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
} from "@xyflow/react";
import { type ReactNode, useEffect, useRef } from "react";
import "./canvas.css";
import { useShortcuts } from "../shell/shortcuts";
import { IconButton } from "../ui/Button";
import { Tooltip } from "../ui/overlays";
import { edgeTypes } from "./edges";
import { CanvasPick } from "./nodes";
import type { CanvasTarget } from "./targets";

const FIT = { padding: 0.08, maxZoom: 1, minZoom: 0.3 } as const;

const reducedMotion = (): boolean =>
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Zoom and fit buttons, and the `f` shortcut. */
export function CanvasControls() {
  const flow = useReactFlow();
  const duration = (): number => (reducedMotion() ? 0 : 220);
  const fit = (): void => void flow.fitView({ ...FIT, duration: duration() });
  useShortcuts({ fitCanvas: fit });
  return (
    <div className="flex flex-col overflow-hidden rounded-control border border-line bg-surface">
      <Tooltip content="Zoom in" side="right">
        <IconButton
          icon="plus"
          label="Zoom in"
          className="rounded-none"
          onClick={() => void flow.zoomIn({ duration: duration() })}
        />
      </Tooltip>
      <Tooltip content="Zoom out" side="right">
        <IconButton
          icon="minus"
          label="Zoom out"
          className="rounded-none border-t-line"
          onClick={() => void flow.zoomOut({ duration: duration() })}
        />
      </Tooltip>
      <Tooltip content="Fit the canvas (f)" side="right">
        <IconButton
          icon="fit"
          label="Fit the canvas"
          className="rounded-none border-t-line"
          onClick={fit}
        />
      </Tooltip>
    </div>
  );
}

/** Fits the view again whenever the layout changes (a new subject, a newly seen tool). */
function FitOnLayout({ layoutKey }: { layoutKey: string }) {
  const flow = useReactFlow();
  const ready = useNodesInitialized();
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    if (!ready || fitted.current === layoutKey) return;
    const first = fitted.current === null;
    fitted.current = layoutKey;
    // One frame later, so the nodes of the new layout are in place before measuring.
    const frame = requestAnimationFrame(() => {
      void flow.fitView({ ...FIT, duration: first || reducedMotion() ? 0 : 220 });
    });
    return () => cancelAnimationFrame(frame);
  }, [ready, layoutKey, flow]);
  return null;
}

export function CanvasFrame({
  label,
  nodes,
  edges,
  nodeTypes,
  layoutKey,
  onPick,
  onClear,
  topLeft,
  bottomRight,
}: {
  /** Names the canvas for assistive tech: "Blueprint of triage". */
  label: string;
  nodes: Node[];
  edges: Edge[];
  nodeTypes: NodeTypes;
  layoutKey: string;
  onPick: (target: CanvasTarget) => void;
  /** A click on empty canvas. */
  onClear?: () => void;
  topLeft?: ReactNode;
  bottomRight?: ReactNode;
}) {
  return (
    <CanvasPick.Provider value={onPick}>
      <ReactFlowProvider>
        <ReactFlow
          aria-label={label}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={FIT}
          minZoom={0.2}
          maxZoom={2}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          disableKeyboardA11y
          deleteKeyCode={null}
          selectionKeyCode={null}
          multiSelectionKeyCode={null}
          zoomActivationKeyCode={null}
          panActivationKeyCode={null}
          onPaneClick={onClear}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1.5}
            color="var(--grid-dot)"
          />
          <FitOnLayout layoutKey={layoutKey} />
          {topLeft && <Panel position="top-left">{topLeft}</Panel>}
          <Panel position="bottom-left">
            <CanvasControls />
          </Panel>
          {bottomRight && (
            <Panel position="bottom-right" className="mb-6!">
              {bottomRight}
            </Panel>
          )}
        </ReactFlow>
      </ReactFlowProvider>
    </CanvasPick.Provider>
  );
}
