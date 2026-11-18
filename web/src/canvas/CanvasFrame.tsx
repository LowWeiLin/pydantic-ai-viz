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
  useReactFlow,
  useStore,
} from "@xyflow/react";
import { type ReactNode, useEffect, useRef } from "react";
import "./canvas.css";
import { useShortcuts } from "../shell/shortcuts";
import { IconButton } from "../ui/Button";
import { Tooltip } from "../ui/overlays";
import { edgeTypes } from "./edges";
import { CanvasPick } from "./nodes";
import type { CanvasTarget } from "./targets";

/**
 * Fitting leaves room for what floats over the canvas: the subject switcher and the inspector
 * toggle on top, the zoom buttons on the left, the legend and the attribution underneath.
 */
const FIT = {
  padding: { top: "56px", right: "24px", bottom: "44px", left: "56px" },
  maxZoom: 1,
  minZoom: 0.3,
} as const;

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

/**
 * Fits the view again whenever the layout changes (a new subject, a newly seen tool) or the
 * canvas changes size (the inspector opens, closes or is dragged; the window is resized).
 */
function FitOnLayout({ layoutKey }: { layoutKey: string }) {
  const flow = useReactFlow();
  const size = useStore((state) => `${state.width}x${state.height}`);
  const fitted = useRef<{ layout: string; size: string } | null>(null);
  useEffect(() => {
    const last = fitted.current;
    // Before the canvas has a size there is nothing to fit to.
    if (size === "0x0" || (last?.layout === layoutKey && last.size === size)) return;
    // Only a new layout glides into place; following a resize has to be immediate.
    const glide = last !== null && last.layout !== layoutKey && !reducedMotion();
    // One frame later, so the nodes of the new layout are in place before measuring. The fit
    // is recorded only once it happens: a render in between cancels the frame and asks again.
    const frame = requestAnimationFrame(() => {
      fitted.current = { layout: layoutKey, size };
      void flow.fitView({ ...FIT, duration: glide ? 220 : 0 });
    });
    return () => cancelAnimationFrame(frame);
  }, [layoutKey, size, flow]);
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
