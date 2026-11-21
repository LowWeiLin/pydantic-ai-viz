/**
 * Canvas nodes. Each is a fixed-size box (see `layout.ts`) with one real `<button>` covering
 * it, so every node is reachable by Tab and has a spoken name that includes its run state.
 * State is never colour alone: a status icon and the label carry it too.
 */
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { createContext, type ReactNode, useContext } from "react";
import { categoryNames, environmentWords, modelLabel } from "../data/model";
import type { Activity, CanvasAgent, CanvasTool, GraphCanvasNode } from "../data/selectors";
import { plural } from "../shell/format";
import { Badge, Chip, StatusDot } from "../ui/Badge";
import { Icon } from "../ui/Icon";
import { MiddleTruncate } from "../ui/MiddleTruncate";
import { cx, statusMeta, toneClass } from "../ui/tone";
import type { Capability, ModelInfo, Skill, Toolset } from "../wire";
import { DELEGATION_INLET, graphNodeLabel, MAX_SKILL_ROWS, nodeKind, SIZE } from "./layout";
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
function Cover({ label, target, title }: { label: string; target: CanvasTarget; title?: string }) {
  const pick = useContext(CanvasPick);
  return (
    <button
      type="button"
      aria-label={label}
      title={title}
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
  parts.push(stateWords(activity.state));
  return `, ${parts.join(", ")}`;
}

const stateWords = (state: Activity["state"]): string =>
  state === "running"
    ? "running"
    : state === "error"
      ? "failed"
      : state === "retry"
        ? "waiting for a retry"
        : "OK";

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
  const warnings = bp.warnings.length;
  const computed = bp.model !== null && modelIsComputed(bp.model);
  const fromEnvironment = bp.model ? environmentWords(bp.model) : null;
  const modelChip = bp.model ? (
    // An expression is not a model name: it is set as code, with a dashed edge. A name from the
    // environment is the variable's name, never the default the source gives for it.
    <span title={fromEnvironment ?? undefined} className="inline-flex min-w-0 max-w-full">
      <Chip
        tone="model"
        icon={computed ? "code" : "model"}
        mono
        pulse={requesting}
        // min-w-0: a long name gives way to what follows it in the row instead of pushing it
        // out of the card.
        className={cx("min-w-0", computed && "border-dashed")}
      >
        {modelLabel(bp.model)}
      </Chip>
    </span>
  ) : (
    <Chip icon="model">No model set</Chip>
  );
  const modelWords = bp.model
    ? `Model ${modelLabel(bp.model)}${computed ? ", decided at run time," : ""} of ${bp.label}`
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
        label={`Agent ${bp.label}${warnings > 0 ? `, ${plural(warnings, "warning")}` : ""}${activityPhrase(agent.run, overlay)}`}
        target={{ kind: "agent", agentId: bp.id }}
      />
      <div className="pointer-events-none relative flex h-full flex-col gap-2 p-3">
        <div className="flex items-center gap-2">
          <span className="tone-soft flex size-6 shrink-0 items-center justify-center rounded-control border">
            <Icon name="agent" size={14} />
          </span>
          <span className="min-w-0 flex-1 truncate font-semibold text-em text-fg">{bp.label}</span>
          {warnings > 0 && (
            // What could not be read is listed in the inspector; the card says only that there is some.
            <span
              title={plural(warnings, "warning")}
              className="flex shrink-0 items-center gap-0.5 text-faint text-meta tabular-nums"
            >
              <Icon name="warning" size={12} />
              {warnings}
            </span>
          )}
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

/** Said of a part Pydantic AI added by default, where defaults are shown. */
const DEFAULT_WORDS = ", a Pydantic AI default";

/** The faint word that marks a part Pydantic AI added by default; the border is dashed too. */
function DefaultTag() {
  return <span className="shrink-0 text-faint text-meta">default</span>;
}

export type ToolsetNodeData = {
  agentId: string;
  /** Null for the group of tools a run used that the blueprint does not list. */
  toolset: Toolset | null;
  count: number;
  selected: boolean;
  /** Given by Pydantic AI on its own; only drawn when defaults are shown. */
  default: boolean;
};

/** Null for a toolset whose tools are not known: its placeholder row below says why. */
const toolsetCount = (toolset: Toolset | null, count: number): string | null =>
  toolset?.opaque && count === 0 ? null : plural(count, "tool");

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
        data.default && "border-dashed",
        data.selected && "pviz-selected",
      )}
    >
      {/* Where the edge from a capability that gives this toolset comes in: at the header. */}
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        isConnectable={false}
        style={{ top: SIZE.groupHeader / 2 }}
      />
      {toolset && (
        <Cover
          label={`Toolset ${toolset.label}${count === null ? "" : `, ${count}`}${data.default ? DEFAULT_WORDS : ""}`}
          target={{ kind: "toolset", agentId: data.agentId, toolsetId: toolset.id }}
        />
      )}
      <div className="pointer-events-none relative flex h-[30px] items-center gap-1.5 px-2.5 text-meta">
        <Icon name="toolset" size={12} className="text-faint" />
        <span className="min-w-0 flex-1 truncate font-medium text-muted">
          {toolset ? toolset.label : "Seen in this run"}
        </span>
        {data.default && <DefaultTag />}
        {count !== null && <span className="shrink-0 text-faint">{count}</span>}
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
  /**
   * A tool Pydantic AI adds that a run used outside any toolset drawn as a default
   * (`load_capability`); only drawn when defaults are shown.
   */
  default: boolean;
};

export function ToolNode({ data }: NodeProps<Node<ToolNodeData, "tool">>) {
  const { activity, overlay } = data;
  return (
    <div
      className={cx(
        "tone-tool relative size-full rounded-control border bg-surface",
        overlay ? stateFrame(activity.state) : "border-line-strong",
        overlay && activity.state === "error" && "bg-error/8",
        data.default && "border-dashed",
        data.selected && "pviz-selected",
      )}
    >
      <Ports loop />
      <Cover
        label={`Tool ${data.name}${activityPhrase(activity, overlay)}${data.default ? DEFAULT_WORDS : ""}`}
        target={{ kind: "tool", agentId: data.agentId, name: data.name }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2 px-2.5">
        <Icon name={data.delegates ? "agent" : "tool"} size={14} className="tone-text" />
        <MiddleTruncate text={data.name} className="min-w-0 flex-1 font-mono text-data text-fg" />
        {data.default && <DefaultTag />}
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
        label={`Toolset ${data.toolsetLabel}: its tools are decided at run time`}
        target={{ kind: "toolset", agentId: data.agentId, toolsetId: data.toolsetId }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2 px-2.5 text-data text-muted">
        <Icon name="idle" size={14} className="text-faint" />
        decided at run time
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

export type CapabilitiesNodeData = { count: number };

/**
 * The box under an agent's card that lists its capabilities. Its rows are picked on their own.
 * A delegation arc to or from the agent comes in under the box, so it does not cross the rows.
 */
export function CapabilitiesBox({ data }: NodeProps<Node<CapabilitiesNodeData, "capabilities">>) {
  return (
    <div className="relative size-full rounded-card border border-line bg-surface-2/70">
      <Ports loop />
      <div className="pointer-events-none relative flex h-[30px] items-center gap-1.5 px-2.5 text-meta">
        <Icon name="capability" size={12} className="text-faint" />
        <span className="min-w-0 flex-1 truncate font-medium text-muted">Capabilities</span>
        <span className="shrink-0 text-faint">{data.count}</span>
      </div>
    </div>
  );
}

export type CapabilityNodeData = {
  agentId: string;
  /** Index into `blueprint.capabilities`. */
  index: number;
  capability: Capability;
  /** Labels of the toolsets it gives the agent that are drawn. */
  toolsets: string[];
  /**
   * What it gives that is hidden among the defaults: "4 tools", or "tools at run time" for a
   * toolset that lists them only then. Null when nothing is.
   */
  hiddenTools: string | null;
  /** How many capabilities of its harness stack are hidden among the defaults. */
  inside: number;
  /**
   * In a run: the calls that loaded it on demand and those to the tools it gives that have no
   * row of their own. Null when it has neither.
   */
  activity: Activity | null;
  overlay: boolean;
  /** Added by Pydantic AI or a harness stack; only drawn when defaults are shown. */
  default: boolean;
  selected: boolean;
};

const listed = (one: string, many: string, items: readonly string[]): string =>
  `${items.length === 1 ? one : many} ${items.join(", ")}`;

/** What a capability row has in it that is drawn elsewhere or hidden: "2 skills · 4 tools". */
export function capabilityRollup(
  capability: Capability,
  hiddenTools: string | null,
  inside: number,
): string | null {
  const count = capability.skills.length;
  const parts = [
    count > MAX_SKILL_ROWS
      ? `${plural(count, "skill")} · ${MAX_SKILL_ROWS} shown`
      : count > 0 && plural(count, "skill"),
    inside > 0 && `${inside} inside`,
    hiddenTools,
  ].filter((part): part is string => typeof part === "string");
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * "Capability WebSearch, Web & research, gives toolset ..., provider-side tool web_search". A
 * member of a harness stack says which one put it there instead of its group.
 */
export function capabilityName(
  capability: Capability,
  toolsets: readonly string[],
  hidden: { hiddenTools?: string | null; inside?: number; default?: boolean } = {},
): string {
  const parts = [
    `Capability ${capability.name}`,
    capability.stack ? `via ${capability.stack}` : categoryNames(capability).label,
  ];
  if (toolsets.length > 0) parts.push(listed("gives toolset", "gives toolsets", toolsets));
  if (hidden.hiddenTools) parts.push(`gives ${hidden.hiddenTools} from Pydantic AI, hidden`);
  if (capability.native_tools.length > 0) {
    parts.push(listed("provider-side tool", "provider-side tools", capability.native_tools));
  }
  if (capability.skills.length > 0) parts.push(plural(capability.skills.length, "skill"));
  if (hidden.inside) parts.push(`${plural(hidden.inside, "capability", "capabilities")} inside`);
  if (capability.deferred) parts.push("on demand");
  return `${parts.join(", ")}${hidden.default ? DEFAULT_WORDS : ""}`;
}

/** A call count and a state icon, for a row that stands for spans of its own in a run. */
function RowActivity({ activity }: { activity: Activity }) {
  return (
    <>
      {activity.calls > 1 && <Badge className="shrink-0">{activity.calls}x</Badge>}
      <StateIcon state={activity.state} />
    </>
  );
}

/**
 * A capability of the agent: its name, and on the right its group in the Pydantic AI docs. A
 * sparkle in the model's colour marks one that gives the model a provider-side tool. What it
 * holds that is not drawn (its skills past the first few, tools hidden among the defaults) is
 * counted in faint text.
 */
export function CapabilityNode({ data }: NodeProps<Node<CapabilityNodeData, "capability">>) {
  const { capability, activity, overlay } = data;
  const name = `${capabilityName(capability, data.toolsets, data)}${
    activity ? activityPhrase(activity, overlay) : ""
  }`;
  const rollup = capabilityRollup(capability, data.hiddenTools, data.inside);
  return (
    <div
      className={cx(
        "tone-capability relative size-full rounded-control border border-line-strong bg-surface",
        data.default && "border-dashed",
        data.selected && "pviz-selected",
      )}
    >
      <Ports />
      {/* The row may be too narrow for all of it: hovering it says the whole of it. */}
      <Cover
        label={name}
        title={name}
        target={{ kind: "capability", agentId: data.agentId, index: data.index }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-2 px-2.5">
        <Icon name="capability" size={14} className="shrink-0 tone-text" />
        {/* The name keeps its width the longest: the group gives way first, four times as fast. */}
        <span className="min-w-0 flex-auto truncate font-mono text-data text-fg">
          {capability.name}
        </span>
        {capability.native_tools.length > 0 && (
          <Icon name="model" size={12} className="shrink-0 tone-model tone-text" />
        )}
        {capability.deferred && (
          <span className="shrink-0 font-medium text-accent text-meta">on demand</span>
        )}
        {rollup && <span className="shrink-0 text-faint text-meta">{rollup}</span>}
        {data.default && !capability.stack && <DefaultTag />}
        <span className="min-w-0 shrink-[4] truncate text-faint text-meta">
          {capability.stack ? `via ${capability.stack}` : categoryNames(capability).short}
        </span>
        {overlay && activity && <RowActivity activity={activity} />}
      </div>
    </div>
  );
}

export type SkillNodeData = {
  agentId: string;
  /** Index into `blueprint.capabilities` of the capability that offers it. */
  index: number;
  /** That capability's name. */
  owner: string;
  skill: Skill;
  /** Its `load_capability` calls in a run. */
  activity: Activity;
  overlay: boolean;
  selected: boolean;
};

/** "Skill house-style of Skills, loaded on demand, hidden from the model, loaded once, OK". */
export function skillName(
  skill: Skill,
  owner: string,
  activity: Activity,
  overlay: boolean,
): string {
  const parts = [`Skill ${skill.name} of ${owner}`, "loaded on demand"];
  if (!skill.model_invocable) parts.push("hidden from the model");
  if (overlay) {
    parts.push(
      activity.calls === 0
        ? "not loaded yet"
        : `loaded ${times(activity.calls)}, ${stateWords(activity.state)}`,
    );
  }
  return parts.join(", ");
}

/**
 * A skill a capability offers, in a row under the capability's. In a run it shows whether the
 * model loaded it. One the model is not told about says so.
 */
export function SkillNode({ data }: NodeProps<Node<SkillNodeData, "skill">>) {
  const { skill, activity, overlay } = data;
  const name = skillName(skill, data.owner, activity, overlay);
  return (
    <div
      className={cx(
        "tone-capability relative size-full rounded-control border border-line bg-surface",
        data.selected && "pviz-selected",
      )}
    >
      <Cover
        label={name}
        title={skill.description}
        target={{ kind: "skill", agentId: data.agentId, index: data.index, name: skill.name }}
      />
      <div className="pointer-events-none relative flex h-full items-center gap-1.5 px-2">
        <Icon name="instructions" size={12} className="shrink-0 tone-text" />
        <MiddleTruncate text={skill.name} className="min-w-0 flex-1 font-mono text-data text-fg" />
        {!skill.model_invocable && <span className="shrink-0 text-faint text-meta">hidden</span>}
        {overlay && <RowActivity activity={activity} />}
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
  capabilities: CapabilitiesBox,
  capability: CapabilityNode,
  skill: SkillNode,
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
