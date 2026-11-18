/**
 * Pure read models over the reduced event log. Nothing here mutates or caches; callers memoise
 * on `(data, t)`.
 *
 * Time travel is a projection, not a re-reduction. At time `t`:
 * - a span is visible if `start_ms <= t`;
 * - it is shown as running, with its end-only fields hidden, if `end_ms` is null or `end_ms > t`;
 * - a stream delta is visible if its envelope `ts <= t`.
 * Live views pass `t = Infinity`.
 */
import type {
  AgentBlueprint,
  GraphBlueprint,
  GraphEdge,
  GraphNode,
  Span,
  SpanKind,
  SpanStatus,
  StreamDelta,
  Tool,
  Toolset,
  Usage,
} from "../wire";
import type { DataState, DeltaRecord } from "./reduce";

// ---------- status ----------

/** What the UI shows. A failed tool call that the model may retry is amber, not red. */
export type DisplayStatus = "running" | "ok" | "retry" | "error";

export function displayStatus(span: Span): DisplayStatus {
  if (span.status === "running") return "running";
  if (span.status === "ok") return "ok";
  return span.kind === "tool_call" && span.data.retry !== null ? "retry" : "error";
}

// ---------- projection ----------

/** The span as it looked at time `t`, or null if it had not started yet. */
export function projectSpan(span: Span, t: number): Span | null {
  if (span.start_ms > t) return null;
  if (span.end_ms !== null && span.end_ms <= t) return span;
  const running = { end_ms: null, status: "running" as const, error: null, attributes: {} };
  switch (span.kind) {
    case "agent_run":
      return {
        ...span,
        ...running,
        data: { ...span.data, instructions: null, output: null, usage: null, messages: null },
      };
    case "model_request":
      return {
        ...span,
        ...running,
        data: {
          model_name: span.data.model_name,
          provider: null,
          response_model: null,
          settings: {},
          instructions: null,
          input_messages: null,
          output_messages: null,
          usage: null,
        },
      };
    case "tool_call":
      return { ...span, ...running, data: { ...span.data, result: null, retry: null } };
    case "graph_run":
      return { ...span, ...running, data: { ...span.data, output: null } };
    default:
      return { ...span, ...running };
  }
}

/** One response part being streamed, assembled from deltas. */
export type StreamPart = {
  index: number;
  kind: StreamDelta["part_kind"];
  tool_name: string | null;
  /** Concatenated text, or the raw (possibly incomplete) arguments JSON for tool calls. */
  text: string;
};

/** Assembles the deltas visible at `t` into parts ordered by `part_index`. */
export function assembleStream(deltas: readonly DeltaRecord[], t: number): StreamPart[] {
  const parts = new Map<number, StreamPart>();
  for (const d of deltas) {
    if (d.ts > t) continue;
    const part = parts.get(d.part_index);
    if (part) {
      part.text += d.delta;
      if (part.tool_name === null) part.tool_name = d.tool_name;
    } else {
      parts.set(d.part_index, {
        index: d.part_index,
        kind: d.part_kind,
        tool_name: d.tool_name,
        text: d.delta,
      });
    }
  }
  return [...parts.values()].sort((a, b) => a.index - b.index);
}

// ---------- runs ----------

function traceSpans(data: DataState, traceId: string): Span[] {
  const ids = data.traces[traceId] ?? [];
  const spans: Span[] = [];
  for (const id of ids) {
    const span = data.spans[id];
    if (span) spans.push(span);
  }
  return spans;
}

/**
 * The run's root: the span with no parent. If that span was evicted or never seen, the earliest
 * span whose parent is unknown stands in for it.
 */
function findRoot(spans: readonly Span[]): Span | null {
  const ids = new Set(spans.map((s) => s.span_id));
  let best: Span | null = null;
  for (const span of spans) {
    if (span.parent_id !== null && ids.has(span.parent_id)) continue;
    if (
      best === null ||
      (span.parent_id === null && best.parent_id !== null) ||
      ((span.parent_id === null) === (best.parent_id === null) && span.start_ms < best.start_ms)
    ) {
      best = span;
    }
  }
  return best;
}

const ZERO_USAGE: Usage = { input_tokens: 0, output_tokens: 0 };

export type RunSummary = {
  traceId: string;
  rootSpanId: string;
  /** Root span name: the agent or graph that was run. */
  name: string;
  kind: SpanKind;
  status: SpanStatus;
  startMs: number;
  /** Null while the run is still going (or the recording stopped before it ended). */
  endMs: number | null;
  /** Wall time; measured up to the latest event for a run that has not ended. */
  durationMs: number;
  spanCount: number;
  usage: Usage;
};

/** Every run (trace) in the log, newest first. */
export function runs(data: DataState): RunSummary[] {
  const out: RunSummary[] = [];
  for (const traceId of data.traceOrder) {
    const spans = traceSpans(data, traceId);
    const root = findRoot(spans);
    if (!root) continue;
    let startMs = root.start_ms;
    const usage = { ...ZERO_USAGE };
    for (const span of spans) {
      if (span.start_ms < startMs) startMs = span.start_ms;
      if (span.kind === "model_request" && span.data.usage) {
        usage.input_tokens += span.data.usage.input_tokens;
        usage.output_tokens += span.data.usage.output_tokens;
      }
    }
    out.push({
      traceId,
      rootSpanId: root.span_id,
      name: root.name,
      kind: root.kind,
      status: root.status,
      startMs,
      endMs: root.end_ms,
      durationMs: Math.max(0, (root.end_ms ?? Math.max(data.lastTs, startMs)) - startMs),
      spanCount: spans.length,
      usage,
    });
  }
  // Stable sort: equal start times keep arrival order, then the whole list is newest first.
  return out.sort((a, b) => b.startMs - a.startMs);
}

/** `[start, end]` of a run in epoch ms; `end` falls back to the latest event while running. */
export function runBounds(data: DataState, traceId: string): { start: number; end: number } | null {
  const spans = traceSpans(data, traceId);
  const root = findRoot(spans);
  if (!root) return null;
  let start = root.start_ms;
  let lastSeen = root.start_ms;
  for (const span of spans) {
    if (span.start_ms < start) start = span.start_ms;
    lastSeen = Math.max(lastSeen, span.end_ms ?? span.start_ms);
  }
  for (const id of data.traces[traceId] ?? []) {
    const last = data.deltas[id]?.at(-1);
    if (last && last.ts > lastSeen) lastSeen = last.ts;
  }
  return { start, end: root.end_ms ?? Math.max(lastSeen, start) };
}

/** Sorted, de-duplicated span start and end times of a run: the stops for "step" in playback. */
export function runBoundaries(data: DataState, traceId: string): number[] {
  const times = new Set<number>();
  for (const span of traceSpans(data, traceId)) {
    times.add(span.start_ms);
    if (span.end_ms !== null) times.add(span.end_ms);
  }
  return [...times].sort((a, b) => a - b);
}

export type RunView = {
  traceId: string;
  /** The projection time this view was built for. */
  t: number;
  /** Root of the visible tree, or null before the run starts. */
  rootId: string | null;
  status: SpanStatus;
  startMs: number;
  /** Null while the run is running at `t`. */
  endMs: number | null;
  /** Visible spans, projected to `t`, by span_id. */
  spans: Readonly<Record<string, Span>>;
  /** Visible span ids, depth first, siblings by start time: the timeline row order. */
  order: readonly string[];
  children: Readonly<Record<string, readonly string[]>>;
  depth: Readonly<Record<string, number>>;
  /** Streamed parts for model requests that are still running at `t`. */
  streams: Readonly<Record<string, readonly StreamPart[]>>;
};

/** The run as it looked at time `t`. Null when the trace is unknown. */
export function runView(data: DataState, traceId: string, t: number): RunView | null {
  const all = traceSpans(data, traceId);
  const fullRoot = findRoot(all);
  if (!fullRoot) return null;

  const spans: Record<string, Span> = {};
  const visible: Span[] = [];
  for (const span of all) {
    const projected = projectSpan(span, t);
    if (!projected) continue;
    spans[projected.span_id] = projected;
    visible.push(projected);
  }

  const children: Record<string, string[]> = {};
  const tops: Span[] = [];
  // Siblings by start time; arrival order breaks ties (parallel tool calls keep their call order).
  const byStart = visible
    .map((span, index) => ({ span, index }))
    .sort((a, b) => a.span.start_ms - b.span.start_ms || a.index - b.index);
  for (const { span } of byStart) {
    if (span.parent_id !== null && spans[span.parent_id]) {
      const list = children[span.parent_id];
      if (list) list.push(span.span_id);
      else children[span.parent_id] = [span.span_id];
    } else {
      tops.push(span);
    }
  }

  const order: string[] = [];
  const depth: Record<string, number> = {};
  const walk = (id: string, level: number): void => {
    if (id in depth) return; // Guards against a malformed parent cycle.
    depth[id] = level;
    order.push(id);
    for (const child of children[id] ?? []) walk(child, level + 1);
  };
  for (const top of tops) walk(top.span_id, 0);

  const streams: Record<string, StreamPart[]> = {};
  for (const span of visible) {
    if (span.kind !== "model_request" || span.status !== "running") continue;
    const deltas = data.deltas[span.span_id];
    if (!deltas) continue;
    const parts = assembleStream(deltas, t);
    if (parts.length > 0) streams[span.span_id] = parts;
  }

  const root = spans[fullRoot.span_id] ?? null;
  return {
    traceId,
    t,
    rootId: root ? root.span_id : (tops[0]?.span_id ?? null),
    status: root ? root.status : "running",
    startMs: fullRoot.start_ms,
    endMs: root ? root.end_ms : null,
    spans,
    order,
    children,
    depth,
    streams,
  };
}

export type RunTotals = {
  /** Summed over model requests only: agent-run usages overlap when a sub-agent shares usage. */
  usage: Usage;
  requests: number;
  toolCalls: number;
  retries: number;
  errors: number;
  /** Elapsed at `t`, capped at the run's end. */
  durationMs: number;
};

/** Totals for a run at time `t`. */
export function runTotals(data: DataState, traceId: string, t: number): RunTotals {
  const totals: RunTotals = {
    usage: { ...ZERO_USAGE },
    requests: 0,
    toolCalls: 0,
    retries: 0,
    errors: 0,
    durationMs: 0,
  };
  const bounds = runBounds(data, traceId);
  if (!bounds) return totals;
  totals.durationMs = Math.max(0, Math.min(t, bounds.end) - bounds.start);
  for (const raw of traceSpans(data, traceId)) {
    const span = projectSpan(raw, t);
    if (!span) continue;
    if (span.kind === "model_request") {
      totals.requests += 1;
      if (span.data.usage) {
        totals.usage.input_tokens += span.data.usage.input_tokens;
        totals.usage.output_tokens += span.data.usage.output_tokens;
      }
    } else if (span.kind === "tool_call") {
      totals.toolCalls += 1;
    }
    const status = displayStatus(span);
    if (status === "retry") totals.retries += 1;
    // A failed parent repeats its child's exception; count the innermost failures only.
    else if (status === "error" && span.kind !== "agent_run" && span.kind !== "graph_run") {
      totals.errors += 1;
    }
  }
  return totals;
}

// ---------- blueprints ----------

/** The first blueprint with this agent name (names are unique per session by contract). */
export function blueprintForAgentName(data: DataState, name: string | null): AgentBlueprint | null {
  if (name === null) return null;
  for (const id of data.agentOrder) {
    const agent = data.agents[id];
    if (agent && agent.name === name) return agent;
  }
  return null;
}

export type RunSubject = { kind: "agent" | "graph"; id: string; label: string };

/** Blueprints that take part in a run, root first: the options of the canvas subject switcher. */
export function runSubjects(data: DataState, view: RunView): RunSubject[] {
  const subjects: RunSubject[] = [];
  const seen = new Set<string>();
  for (const id of view.order) {
    const span = view.spans[id];
    if (!span) continue;
    if (span.kind === "graph_run") {
      const graph = data.graphs[span.data.graph_id];
      if (graph && !seen.has(`g:${graph.id}`)) {
        seen.add(`g:${graph.id}`);
        subjects.push({ kind: "graph", id: graph.id, label: graph.label });
      }
    } else if (span.kind === "agent_run") {
      const agent = blueprintForAgentName(data, span.agent_name);
      if (agent && !seen.has(`a:${agent.id}`)) {
        seen.add(`a:${agent.id}`);
        subjects.push({ kind: "agent", id: agent.id, label: agent.label });
      }
    }
  }
  return subjects;
}

// ---------- canvas models ----------

/** Run activity on one canvas node at the view's time. */
export type Activity = {
  /** `idle` when the node has not been touched; otherwise the state of its latest span. */
  state: "idle" | DisplayStatus;
  /** Spans that touched this node so far. */
  calls: number;
  retries: number;
  errors: number;
  /** Those spans, oldest first; the last one is what a click on the node selects. */
  spanIds: string[];
};

const idle = (): Activity => ({ state: "idle", calls: 0, retries: 0, errors: 0, spanIds: [] });

function touch(activity: Activity, span: Span): void {
  const status = displayStatus(span);
  activity.calls += 1;
  activity.spanIds.push(span.span_id);
  if (status === "retry") activity.retries += 1;
  if (status === "error") activity.errors += 1;
  // Anything still running wins; otherwise the most recent span decides.
  if (activity.state !== "running" || status === "running") activity.state = status;
}

export type CanvasTool = { tool: Tool; activity: Activity };
export type CanvasToolset = { toolset: Toolset; tools: CanvasTool[] };

export type CanvasAgent = {
  blueprint: AgentBlueprint;
  /** Delegation distance from the subject (0 = the subject itself). */
  depth: number;
  run: Activity;
  /** Model requests: `running` drives the pulsing model chip. */
  model: Activity;
  toolsets: CanvasToolset[];
  /** Calls to tools the blueprint does not list (tools of opaque toolsets, mostly). */
  unlisted: { name: string; activity: Activity }[];
  /** Lit once a run of this agent has ended ok. */
  outputReady: boolean;
};

export type CanvasDelegation = {
  fromAgentId: string;
  toolName: string;
  toAgentId: string;
  evidence: "static" | "observed";
  /** True while the delegated run is in flight. */
  active: boolean;
};

export type CanvasModel = {
  subjectId: string;
  agents: CanvasAgent[];
  delegations: CanvasDelegation[];
};

/** How far the canvas follows delegation edges from its subject. */
export const MAX_DELEGATION_DEPTH = 3;

/**
 * Everything the agent canvas draws: the subject blueprint, the agents it can reach through
 * delegation (cycle-safe, at most `MAX_DELEGATION_DEPTH` hops), and the run overlay when a
 * view is given.
 */
export function canvasModel(
  data: DataState,
  blueprintId: string,
  view: RunView | null,
): CanvasModel | null {
  const subject = data.agents[blueprintId];
  if (!subject) return null;

  const reached = new Map<string, number>([[subject.id, 0]]);
  const queue: AgentBlueprint[] = [subject];
  for (const agent of queue) {
    const level = reached.get(agent.id) ?? 0;
    if (level >= MAX_DELEGATION_DEPTH) continue;
    for (const delegate of agent.delegates) {
      const target = data.agents[delegate.agent_id];
      if (!target || reached.has(target.id)) continue;
      reached.set(target.id, level + 1);
      queue.push(target);
    }
  }

  const viewSpans: Span[] = view ? view.order.flatMap((id) => view.spans[id] ?? []) : [];

  const agents = queue.map((blueprint): CanvasAgent => {
    const run = idle();
    const model = idle();
    const byTool = new Map<string, Activity>();
    let outputReady = false;
    if (blueprint.name !== null) {
      for (const span of viewSpans) {
        if (span.agent_name !== blueprint.name) continue;
        if (span.kind === "agent_run") {
          touch(run, span);
          if (span.status === "ok") outputReady = true;
        } else if (span.kind === "model_request") {
          touch(model, span);
        } else if (span.kind === "tool_call") {
          let activity = byTool.get(span.data.tool_name);
          if (!activity) {
            activity = idle();
            byTool.set(span.data.tool_name, activity);
          }
          touch(activity, span);
        }
      }
    }

    const groups = new Map<string, CanvasToolset>(
      blueprint.toolsets.map((toolset) => [toolset.id, { toolset, tools: [] }]),
    );
    const listed = new Set<string>();
    for (const tool of blueprint.tools) {
      listed.add(tool.name);
      let group = groups.get(tool.toolset);
      if (!group) {
        // A tool naming a toolset the blueprint does not declare still needs a home.
        const toolset: Toolset = {
          id: tool.toolset,
          toolset_id: null,
          label: "Tools",
          kind: "other",
          wrappers: [],
          opaque: false,
        };
        group = { toolset, tools: [] };
        groups.set(tool.toolset, group);
      }
      group.tools.push({ tool, activity: byTool.get(tool.name) ?? idle() });
    }
    const unlisted = [...byTool]
      .filter(([name]) => !listed.has(name))
      .map(([name, activity]) => ({ name, activity }));

    return {
      blueprint,
      depth: reached.get(blueprint.id) ?? 0,
      run,
      model,
      toolsets: [...groups.values()],
      unlisted,
      outputReady,
    };
  });

  const delegations: CanvasDelegation[] = [];
  for (const { blueprint } of agents) {
    for (const delegate of blueprint.delegates) {
      const target = data.agents[delegate.agent_id];
      if (!target || !reached.has(target.id)) continue;
      const active = viewSpans.some((span) => {
        if (span.kind !== "agent_run" || span.status !== "running") return false;
        if (span.agent_name !== target.name || span.parent_id === null) return false;
        const parent = view?.spans[span.parent_id];
        return (
          parent?.kind === "tool_call" &&
          parent.data.tool_name === delegate.tool_name &&
          parent.agent_name === blueprint.name
        );
      });
      delegations.push({
        fromAgentId: blueprint.id,
        toolName: delegate.tool_name,
        toAgentId: target.id,
        evidence: delegate.evidence,
        active,
      });
    }
  }

  return { subjectId: subject.id, agents, delegations };
}

export type GraphCanvasNode = {
  node: GraphNode;
  /** `calls` is the visit count; `state: "running"` marks the active node. */
  activity: Activity;
};

export type GraphCanvasModel = {
  graph: GraphBlueprint;
  run: Activity;
  nodes: GraphCanvasNode[];
  edges: GraphEdge[];
  /** Node spans whose `node_id` the blueprint does not list. */
  unlisted: { nodeId: string; activity: Activity }[];
};

/** Everything the graph canvas draws, with the run overlay when a view is given. */
export function graphCanvasModel(
  data: DataState,
  graphId: string,
  view: RunView | null,
): GraphCanvasModel | null {
  const graph = data.graphs[graphId];
  if (!graph) return null;
  const run = idle();
  const byNode = new Map<string, Activity>();
  if (view) {
    for (const id of view.order) {
      const span = view.spans[id];
      if (!span) continue;
      if (span.kind === "graph_run" && span.data.graph_id === graphId) {
        touch(run, span);
      } else if (span.kind === "graph_node" && span.data.graph_id === graphId) {
        let activity = byNode.get(span.data.node_id);
        if (!activity) {
          activity = idle();
          byNode.set(span.data.node_id, activity);
        }
        touch(activity, span);
      }
    }
  }
  const listed = new Set(graph.nodes.map((node) => node.id));
  return {
    graph,
    run,
    nodes: graph.nodes.map((node) => ({ node, activity: byNode.get(node.id) ?? idle() })),
    edges: graph.edges,
    unlisted: [...byNode]
      .filter(([nodeId]) => !listed.has(nodeId))
      .map(([nodeId, activity]) => ({ nodeId, activity })),
  };
}
