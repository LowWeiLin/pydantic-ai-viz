/**
 * Canvas nodes. Each is a fixed-size box (see `layout.ts`) with one real `<button>` covering
 * it, so every node is reachable by Tab and has a spoken name that includes its run state.
 * State is never colour alone: a status icon and the label carry it too.
 */
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { createContext, type ReactNode, useContext } from "react";
import type { Activity, CanvasAgent, CanvasTool, GraphCanvasNode } from "../data/selectors";
import { plural } from "../shell/format";
import { Badge, Chip, StatusDot } from "../ui/Badge";
import { Icon } from "../ui/Icon";
import { MiddleTruncate } from "../ui/MiddleTruncate";
import { cx, statusMeta, toneClass } from "../ui/tone";
import type { ModelInfo, Toolset } from "../wire";
import { DELEGATION_INLET, graphNodeLabel, nodeKind } from "./layout";
import type { CanvasTarget } from "./targets";

/** How nodes report clicks. Provided by the canvas frame. */
export const CanvasPick = createContext<(target: CanvasTarget) => void>(() => {});

// ---------- shared pieces ----------

/** Invisible connection points: edges need them, people do not. */
function Ports({ loop = false }: { loop?: boolean }) {
  return (
    <>
      <Handle type="target" position={Position.Left} id="in" isConnectable={false} />
      <Handle type="source" position={Position.Right} id="out" isConnectable={false} />
      {loop && (
        <>
          {/* Apart, so an arc that leaves a node and one that arrives do not share a point. */}
          <Handle
            type="target"
            position={Position.Bottom}
            id="loop-in"
            isConnectable={false}
            style={{ left: "62%" }}
          />
          <Handle
            type="source"
            position={Position.Bottom}
            id="loop-out"
            isConnectable={false}
            style={{ left: "38%" }}
          />
        </>
      )}
    </>
  );
}

const CLICKABLE = { pointerEvents: "auto" } as const;

/** The button that makes a node clickable and focusable. It sits under the node's content. */
function Cover({ label, target }: { label: string; target: CanvasTarget }) {
  const pick = useContext(CanvasPick);
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() => pick(target)}
      // React Flow turns pointer events off on nodes that cannot be dragged or selected.
      style={CLICKABLE}
      className="absolute inset-0 cursor-pointer rounded-[inherit] border-0 bg-transparent p-0"
    />
  );
}

const times = (n: number): string => (n === 1 ? "once" : n === 2 ? "twice" : `${n} times`);

/** Run state in words, for accessible names: "retried once, 2 calls, OK". */
export function activityPhrase(activity: Activity, overlay: boolean): string {
  if (!overlay) return "";
  if (activity.calls === 0) return ", not used yet";
  const parts: string[] = [];
  if (activity.retries > 0) parts.push(`retried ${times(activity.retries)}`);
  parts.push(plural(activity.calls, "call"));
  parts.push(
    activity.state === "running"
      ? "running"
      : activity.state === "error"
        ? "failed"
        : activity.state === "retry"
          ? "waiting for a retry"
          : "OK",
  );
  return `, ${parts.join(", ")}`;
}

/** Border and ring for a node in a given run state. */
function stateFrame(state: Activity["state"]): string {
  switch (state) {
    case "running":
      return "tone-accent pulse-ring border-accent";
    case "ok":
      return "border-ok/55";
    case "retry":
      return "border-retry/70";
    case "error":
      return "border-error/80";
    default:
      return "border-line-strong";
  }
}

function StateIcon({ state }: { state: Activity["state"] }) {
  if (state === "idle") return null;
  const meta = statusMeta[state];
  return (
    <span className={cx(toneClass[meta.tone], "tone-text inline-flex")}>
      <Icon name={meta.icon} size={14} className={state === "running" ? "animate-spin" : ""} />
    </span>
  );
}

// ---------- agent canvas ----------

export type AgentNodeData = {
  agent: CanvasAgent;
  /** The agent this canvas is about (the others are reached through delegation). */
  subject: boolean;
  selected: "card" | "model" | null;
  /** True on a run view: activity is shown and the model chip is its own target. */
  overlay: boolean;
};

/** True when the model is known only as the source text that produces it at run time. */
export const modelIsComputed = (model: ModelInfo): boolean =>
  model.expression !== null && model.name === model.expression;

export function AgentNode({ data }: NodeProps<Node<AgentNodeData, "agent">>) {
  const pick = useContext(CanvasPick);
  const { agent, overlay } = data;
  const bp = agent.blueprint;
  const requesting = agent.model.state === "running";
  const toolCount = bp.tools.length;
  const computed = bp.model !== null && modelIsComputed(bp.model);
  const modelChip = bp.model ? (
    // An expression is not a model name: it is set as code, with a dashed edge.
    <Chip
      tone="model"
      icon={computed ? "code" : "model"}
      mono
      pulse={requesting}
      // min-w-0: a long name gives way to what follows it in the row instead of pushing it
      // out of the card.
      className={cx("min-w-0", computed && "border-dashed")}
    >
      {bp.model.name}
    </Chip>
  ) : (
    <Chip icon="model">No model set</Chip>
  );
  const modelWords = bp.model
    ? `Model ${bp.model.name}${computed ? ", decided at run time," : ""} of ${bp.label}`
    : "";
  return (
    <div
      className={cx(
        "tone-agent relative size-full rounded-card border bg-surface",
        overlay ? stateFrame(agent.run.state) : "border-line-strong",
        data.subject && "pviz-subject",
        data.selected === "card" && "pviz-selected",
      )}
    >
      <Ports loop />
      {/* Where a delegation from the row above comes in. */}
      <Handle
        type="target"
        position={Position.Top}
        id="down"
        isConnectable={false}
        style={{ left: DELEGATION_INLET }}
      />
      <Cover
        label={`Agent ${bp.label}${activityPhrase(agent.run, overlay)}`}
        target={{ kind: "agent", agentId: bp.id }}
      />
      <div className="pointer-events-none relative flex h-full flex-col gap-2 p-3">
        <div className="flex items-center gap-2">
          <span className="tone-soft flex size-6 shrink-0 items-center justify-center rounded-control border">
            <Icon name="agent" size={14} />
          </span>
          <span className="min-w-0 flex-1 truncate font-semibold text-em text-fg">{bp.label}</span>
          {overlay && agent.run.state !== "idle" && (
            <StatusDot status={agent.run.state} showLabel className="shrink-0" />
          )}
        </div>
        {bp.description && (
          <p className="m-0 line-clamp-2 h-[35px] text-data text-muted">{bp.description}</p>
        )}
        <div className="flex min-w-0 items-center gap-1.5">
          {overlay && bp.model ? (
            <button
              type="button"
              aria-label={`${modelWords}${activityPhrase(agent.model, true)}`}
              onClick={() => pick({ kind: "model", agentId: bp.id })}
              style={CLICKABLE}
              className={cx(
                "inline-flex min-w-0 cursor-pointer rounded-control border-0 bg-transparent p-0",
                data.selected === "model" && "pviz-selected",
              )}
            >
              {modelChip}
            </button>
          ) : (
            modelChip
          )}
          {computed && <span className="shrink-0 text-faint text-meta">at run time</span>}
          {bp.model?.offline && <Chip className="shrink-0">offline</Chip>}
          {bp.source === "observed" && (
            <Chip tone="accent" className="shrink-0">
              observed
            </Chip>
          )}
        </div>
        <p className="m-0 mt-auto flex items-center gap-1.5 truncate text-faint text-meta">
          <Icon name="instructions" size={12} />
          <span>
            {plural(bp.instructions.length, "instruction")} · {plural(toolCount, "tool")}
            {overlay && agent.model.calls > 0 && ` · ${plural(agent.model.calls, "request")}`}
          </span>
        </p>
      </div>
    </div>
  );
}

export type ToolsetNodeData = {
  agentId: string;
  /** Null for the group of tools a run used that the blueprint does not list. */
  toolset: Toolset | null;
  count: number;
  selected: boolean;
};

const toolsetCount = (toolset: Toolset | null, count: number): string =>
  toolset?.opaque && count === 0 ? "dynamic" : plural(count, "tool");

/**
 * The box around a toolset's tools, with the toolset label as its header. The box picks the
 * toolset: its tools sit on top of it and are picked on their own. The group of tools seen in a
 * run is not a toolset and takes no clicks.
 */
export function ToolsetHeader({ data }: NodeProps<Node<ToolsetNodeData, "toolset">>) {
  const { toolset } = data;
  const count = toolsetCount(toolset, data.count);
  return (
    <div
      className={cx(
        "relative size-full rounded-card border border-line bg-surface-2/70",
        data.selected && "pviz-selected",
      )}
    >
      {toolset && (
        <Cover
          label={`Toolset ${toolset.label}, ${count}`}
          target={{ kind: "toolset", agentId: data.agentId, toolsetId: toolset.id }}
        />
      )}
      <div className="pointer-events-none relative flex h-[30px] items-center gap-1.5 px-2.5 text-meta">
        <Icon name="toolset" size={12} className="text-faint" />
        <span className="min-w-0 flex-1 truncate font-medium text-muted">
          {toolset ? toolset.label : "Seen in this run"}
        </span>
        <span className="shrink-0 text-faint">{count}</span>
      </div>
    </div>
  );
}

export type ToolNodeData = {
  agentId: string;
  name: string;
  /** Null for a tool seen in a run that the blueprint does not list. */
  tool: CanvasTool["tool"] | null;
  activity: Activity;
  selected: boolean;
  overlay: boolean;
  /** This tool hands over to another agent. */
  delegates: boolean;
};

export function ToolNode({ data }: NodeProps<Node<ToolNodeData, "tool">>) {
  const { activity, overlay } = data;
  return (
    <div
      className={cx(
        "tone-tool relative size-full rounded-control border bg-surface",
        overlay ? stateFrame(activity.state) : "border-line-strong",
        overlay && activity.state === "error" && "bg-error/8",
        data.selected && "pviz-selected",
      )}
    >
      <Ports loop />
      <Cover
        label={`Tool ${data.name}${activityPhrase(activity, overlay)}`}
        target={{ kind: "tool", agentId: data.agentId, name: data.name }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2 px-2.5">
        <Icon name={data.delegates ? "agent" : "tool"} size={14} className="tone-text" />
        <MiddleTruncate text={data.name} className="min-w-0 flex-1 font-mono text-data text-fg" />
        {overlay && activity.retries > 0 && (
          <span className="tone-retry tone-text inline-flex shrink-0 items-center gap-0.5 font-medium text-meta">
            <Icon name="retry" size={12} />
            {activity.retries}
          </span>
        )}
        {overlay && activity.calls > 1 && <Badge className="shrink-0">{activity.calls}x</Badge>}
        {overlay && <StateIcon state={activity.state} />}
      </div>
    </div>
  );
}

export type PlaceholderNodeData = { agentId: string; toolsetId: string; toolsetLabel: string };

/** Stands in for the tools of a toolset that can only list them during a run. Picks the toolset. */
export function PlaceholderNode({ data }: NodeProps<Node<PlaceholderNodeData, "placeholder">>) {
  return (
    <div className="relative size-full rounded-control border border-line-strong border-dashed">
      <Ports />
      <Cover
        label={`Toolset ${data.toolsetLabel}: its tools are resolved at run time`}
        target={{ kind: "toolset", agentId: data.agentId, toolsetId: data.toolsetId }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2 px-2.5 text-data text-muted">
        <Icon name="idle" size={14} className="text-faint" />
        resolved at run time
      </div>
    </div>
  );
}

export type OutputNodeData = {
  agentId: string;
  agentLabel: string;
  mode: string;
  typeName: string | null;
  ready: boolean;
  selected: boolean;
  overlay: boolean;
};

export function OutputNode({ data }: NodeProps<Node<OutputNodeData, "output">>) {
  const type = data.typeName ?? (data.mode === "text" ? "str" : "unknown type");
  return (
    <div
      className={cx(
        "tone-output relative size-full rounded-card border bg-surface",
        data.ready ? "border-output bg-output/8" : "border-line-strong",
        data.selected && "pviz-selected",
      )}
    >
      <Ports />
      <Cover
        label={`Output of ${data.agentLabel}: ${type}, ${data.mode} mode${
          data.overlay ? (data.ready ? ", ready" : ", not ready yet") : ""
        }`}
        target={{ kind: "output", agentId: data.agentId }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2.5 px-3">
        <span className="tone-soft flex size-6 shrink-0 items-center justify-center rounded-control border">
          <Icon name="output" size={14} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="font-semibold text-[10px] text-faint uppercase leading-tight tracking-[0.08em]">
            Output · {data.mode}
          </span>
          <span className="truncate font-mono text-data text-fg">{type}</span>
        </span>
        {data.ready && <Icon name="check" size={14} className="tone-text" />}
      </div>
    </div>
  );
}

// ---------- graph canvas ----------

export type GraphNodeData = {
  entry: GraphCanvasNode;
  selected: boolean;
  overlay: boolean;
};

/** A graph node's state: its own spans decide; a waypoint that was passed counts as done. */
export function graphNodeState(entry: GraphCanvasNode): Activity["state"] {
  if (entry.activity.state !== "idle") return entry.activity.state;
  return entry.passes > 0 ? "ok" : "idle";
}

const visitsOf = (entry: GraphCanvasNode): number => entry.activity.calls + entry.passes;

function graphNodeName(data: GraphNodeData): string {
  const { entry, overlay } = data;
  const kind = nodeKind(entry.node.kind);
  const base = `${kind === "step" ? "Step" : kind[0]?.toUpperCase() + kind.slice(1)} ${graphNodeLabel(entry.node)}`;
  if (!overlay) return base;
  const visits = visitsOf(entry);
  if (visits === 0) return `${base}, not visited yet`;
  const state = graphNodeState(entry);
  const word = state === "running" ? "running" : state === "error" ? "failed" : "done";
  return `${base}, visited ${times(visits)}, ${word}`;
}

function Visits({ data, className }: { data: GraphNodeData; className?: string }) {
  const visits = visitsOf(data.entry);
  if (!data.overlay || visits === 0) return null;
  const state = graphNodeState(data.entry);
  return (
    <span
      className={cx(
        toneClass[statusMeta[state].tone],
        "tone-soft pviz-badge pointer-events-none absolute inline-flex h-[18px] items-center gap-0.5 rounded-full border px-1.5 font-semibold text-meta leading-none",
        className,
      )}
    >
      <Icon
        name={statusMeta[state].icon}
        size={11}
        className={state === "running" ? "animate-spin" : ""}
      />
      {visits}x
    </span>
  );
}

function graphTarget(data: GraphNodeData): CanvasTarget {
  return { kind: "graphNode", nodeId: data.entry.node.id };
}

export function GraphStepNode({ data }: NodeProps<Node<GraphNodeData, "step">>) {
  const { node } = data.entry;
  const kind = nodeKind(node.kind);
  const label = graphNodeLabel(node);
  const state = graphNodeState(data.entry);
  return (
    <div
      className={cx(
        "tone-graph relative size-full rounded-card border bg-surface",
        data.overlay ? stateFrame(state) : "border-line-strong",
        data.overlay && state === "running" && "bg-accent/8",
        data.selected && "pviz-selected",
      )}
    >
      <Ports loop />
      <Cover label={graphNodeName(data)} target={graphTarget(data)} />
      <div className="pointer-events-none relative flex h-full flex-col justify-center px-3">
        <span className="truncate font-medium text-fg text-ui">{label}</span>
        <span className="truncate font-mono text-faint text-meta">
          {kind !== "step" ? kind : label === node.id ? "step" : node.id}
        </span>
      </div>
      <Visits data={data} className="-top-2.5 -right-2" />
    </div>
  );
}

export function TerminalNode({ data }: NodeProps<Node<GraphNodeData, "terminal">>) {
  const { node } = data.entry;
  const state = graphNodeState(data.entry);
  return (
    <div
      className={cx(
        "tone-graph relative size-full rounded-full border bg-surface-2",
        data.overlay ? stateFrame(state) : "border-line-strong",
        data.selected && "pviz-selected",
      )}
    >
      <Ports loop />
      <Cover label={graphNodeName(data)} target={graphTarget(data)} />
      <div className="pointer-events-none relative flex h-full items-center justify-center gap-1.5 px-3">
        <span
          aria-hidden="true"
          className={cx(
            "size-2 rounded-full border-(--tone) border-[1.5px]",
            node.kind === "end" && "bg-(--tone)",
          )}
        />
        <span className="truncate font-medium text-muted text-ui">{graphNodeLabel(node)}</span>
      </div>
      <Visits data={data} className="-top-2.5 -right-2" />
    </div>
  );
}

export function DecisionNode({ data }: NodeProps<Node<GraphNodeData, "decision">>) {
  const { node } = data.entry;
  const state = graphNodeState(data.entry);
  const stroke =
    !data.overlay || state === "idle"
      ? "var(--border-strong)"
      : state === "running"
        ? "var(--accent)"
        : state === "error"
          ? "var(--error)"
          : "color-mix(in srgb, var(--ok) 55%, transparent)";
  return (
    <div className={cx("tone-graph relative size-full", data.selected && "pviz-selected")}>
      <Ports loop />
      <svg
        aria-hidden="true"
        className="absolute inset-0 size-full overflow-visible"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
      >
        <polygon
          points="50,1 99,50 50,99 1,50"
          fill="var(--surface)"
          stroke={stroke}
          strokeWidth="1.25"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
      </svg>
      <Cover label={graphNodeName(data)} target={graphTarget(data)} />
      <div className="pointer-events-none relative flex h-full flex-col items-center justify-center px-8 text-center">
        <span className="max-w-full truncate font-medium text-fg text-ui">
          {node.note ?? node.label ?? node.id}
        </span>
        <span className="font-mono text-faint text-meta">decision</span>
      </div>
      <Visits data={data} className="top-0 right-3" />
    </div>
  );
}

/** For legends and tests: the nodes as a map xyflow understands. */
export const agentNodeTypes = {
  agent: AgentNode,
  toolset: ToolsetHeader,
  tool: ToolNode,
  placeholder: PlaceholderNode,
  output: OutputNode,
};

export const graphNodeTypes = {
  step: GraphStepNode,
  terminal: TerminalNode,
  decision: DecisionNode,
};

/** A small key to the canvas: what the line styles mean. */
export function Legend({ children }: { children: ReactNode }) {
  return (
    <ul className="m-0 flex list-none flex-wrap items-center gap-x-3 gap-y-1 p-0 text-faint text-meta">
      {children}
    </ul>
  );
}
