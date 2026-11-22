/**
 * The event-log reducer. The whole viewer is a pure function of the envelopes fed through here:
 * live mode, recordings and embedded exports all use the same path.
 *
 * `reduce` never mutates its input. `reduceAll` applies a batch with one copy of the containers
 * instead of one per event, which keeps loading a 50,000-event recording linear.
 */
import type {
  AgentBlueprint,
  Capability,
  Envelope,
  GraphBlueprint,
  SessionStarted,
  Span,
  StreamDelta,
} from "../wire";

/**
 * Recordings made before capabilities were grouped (pydantic-ai-viz 0.1.0) give only a name and
 * the builtin flag; those made before skills and harness stacks were read lack the last three, and those made
 * before hooks were read lack `hooks`.
 */
const CAPABILITY_DEFAULTS = {
  category: "custom",
  package: null,
  deferred: false,
  details: {},
  expressions: {},
  toolsets: [],
  native_tools: [],
  capability_id: null,
  stack: null,
  skills: [],
  hooks: [],
} satisfies Omit<Capability, "name" | "builtin">;

const withCapabilityDefaults = (list: Capability[] | undefined): Capability[] =>
  (list ?? []).map((c) => ({ ...CAPABILITY_DEFAULTS, ...c }));

/** Recordings made before default settings were told apart name none. */
const withSettingsDefaults = (settings: AgentBlueprint["settings"]): AgentBlueprint["settings"] =>
  isObject(settings) ? { ...settings, defaults: settings.defaults ?? [] } : settings;

/** One stream.delta, kept with its envelope time so time travel can filter it. */
export type DeltaRecord = {
  seq: number;
  ts: number;
  part_index: number;
  part_kind: StreamDelta["part_kind"];
  tool_name: string | null;
  delta: string;
};

export type SessionInfo = Omit<SessionStarted, "type"> & { started_ts: number };

export type DataState = {
  session: SessionInfo | null;
  /** Agent blueprints by id. */
  agents: Readonly<Record<string, AgentBlueprint>>;
  /** Agent blueprint ids in first-seen order. */
  agentOrder: readonly string[];
  graphs: Readonly<Record<string, GraphBlueprint>>;
  graphOrder: readonly string[];
  /** Latest version of every span, by span_id. */
  spans: Readonly<Record<string, Span>>;
  /** span_ids per trace, in first-seen order. */
  traces: Readonly<Record<string, readonly string[]>>;
  /** trace_ids in first-seen order. */
  traceOrder: readonly string[];
  /** The seq of the first span event of each trace: where it entered the log. */
  traceSeq: Readonly<Record<string, number>>;
  /** Stream deltas per model_request span_id, in arrival order. */
  deltas: Readonly<Record<string, readonly DeltaRecord[]>>;
  /** Highest seq applied, or -1. Doubles as the SSE resume cursor. */
  lastSeq: number;
  /** Highest envelope ts applied, or 0. This is "now" for a live session. */
  lastTs: number;
  /** Number of envelopes applied (ignored duplicates not counted). */
  eventCount: number;
};

export const initialData: DataState = {
  session: null,
  agents: {},
  agentOrder: [],
  graphs: {},
  graphOrder: [],
  spans: {},
  traces: {},
  traceOrder: [],
  traceSeq: {},
  deltas: {},
  lastSeq: -1,
  lastTs: 0,
  eventCount: 0,
};

type Draft = {
  -readonly [K in keyof DataState]: DataState[K] extends Readonly<Record<string, infer V>>
    ? Record<string, V>
    : DataState[K] extends readonly (infer V)[]
      ? V[]
      : DataState[K];
};

/** Tracks which containers of the draft are already private copies. */
type Owned = { top: Set<keyof DataState>; traces: Set<string>; deltas: Set<string> };

function draftOf(state: DataState): Draft {
  // Shallow: containers are copied lazily by `own`, so an ignored batch allocates nothing else.
  return { ...state } as Draft;
}

function own<K extends keyof DataState>(draft: Draft, owned: Owned, key: K): Draft[K] {
  if (!owned.top.has(key)) {
    const value = draft[key];
    draft[key] = (Array.isArray(value) ? [...value] : { ...(value as object) }) as Draft[K];
    owned.top.add(key);
  }
  return draft[key];
}

function freshDraft(): Draft {
  return {
    session: null,
    agents: {},
    agentOrder: [],
    graphs: {},
    graphOrder: [],
    spans: {},
    traces: {},
    traceOrder: [],
    traceSeq: {},
    deltas: {},
    lastSeq: -1,
    lastTs: 0,
    eventCount: 0,
  };
}

function allOwned(): Owned {
  return {
    top: new Set<keyof DataState>([
      "agents",
      "agentOrder",
      "graphs",
      "graphOrder",
      "spans",
      "traces",
      "traceOrder",
      "traceSeq",
      "deltas",
    ]),
    traces: new Set(),
    deltas: new Set(),
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Recordings made before deferred tool calls were read lack `deferred` and `deferral`. */
function withSpanDefaults(span: Span): Span {
  if (span.kind === "agent_run" && isObject(span.data) && !Array.isArray(span.data.deferred))
    return { ...span, data: { ...span.data, deferred: [] } };
  if (span.kind === "tool_call" && isObject(span.data) && span.data.deferral === undefined)
    return { ...span, data: { ...span.data, deferral: null } };
  return span;
}

function upsertSpan(draft: Draft, owned: Owned, raw: Span, seq: number): void {
  const span = withSpanDefaults(raw);
  const known = span.span_id in draft.spans;
  own(draft, owned, "spans")[span.span_id] = span;
  if (known) return;

  const traces = own(draft, owned, "traces");
  const existing = traces[span.trace_id];
  if (!existing) {
    traces[span.trace_id] = [span.span_id];
    owned.traces.add(span.trace_id);
    own(draft, owned, "traceOrder").push(span.trace_id);
    own(draft, owned, "traceSeq")[span.trace_id] = seq;
    return;
  }
  if (!owned.traces.has(span.trace_id)) {
    traces[span.trace_id] = [...existing, span.span_id];
    owned.traces.add(span.trace_id);
  } else {
    (existing as string[]).push(span.span_id);
  }
}

function appendDelta(draft: Draft, owned: Owned, env: Envelope & StreamDelta): void {
  const record: DeltaRecord = {
    seq: env.seq,
    ts: env.ts,
    part_index: env.part_index,
    part_kind: env.part_kind,
    tool_name: env.tool_name,
    delta: env.delta,
  };
  const deltas = own(draft, owned, "deltas");
  const existing = deltas[env.span_id];
  if (!existing) {
    deltas[env.span_id] = [record];
    owned.deltas.add(env.span_id);
  } else if (!owned.deltas.has(env.span_id)) {
    deltas[env.span_id] = [...existing, record];
    owned.deltas.add(env.span_id);
  } else {
    (existing as DeltaRecord[]).push(record);
  }
}

/** Applies one envelope to the draft. Returns a replacement draft when the session changes. */
function apply(draft: Draft, owned: Owned, env: Envelope): { draft: Draft; owned: Owned } {
  // `type` is an open set on the wire, so it is read as a plain string here.
  const type: string = env.type;

  if (env.type === "session.started") {
    const sameSession = draft.session?.session_id === env.session_id;
    if (sameSession) {
      // A reconnect that replays from seq 0. Everything we hold is still valid.
      return { draft, owned };
    }
    const next = freshDraft();
    const { type: _type, seq: _seq, ts, ...info } = env;
    next.session = { ...info, started_ts: ts };
    next.lastSeq = env.seq;
    next.lastTs = ts;
    next.eventCount = 1;
    return { draft: next, owned: allOwned() };
  }

  // Replayed events (SSE reconnect without Last-Event-ID) are dropped; seq only ever grows.
  if (env.seq <= draft.lastSeq) return { draft, owned };

  draft.lastSeq = env.seq;
  if (env.ts > draft.lastTs) draft.lastTs = env.ts;
  draft.eventCount += 1;

  switch (env.type) {
    case "blueprint.agent": {
      if (!isObject(env.agent) || typeof env.agent.id !== "string") break;
      if (!(env.agent.id in draft.agents)) own(draft, owned, "agentOrder").push(env.agent.id);
      own(draft, owned, "agents")[env.agent.id] = {
        ...env.agent,
        capabilities: withCapabilityDefaults(env.agent.capabilities),
        settings: withSettingsDefaults(env.agent.settings),
      };
      break;
    }
    case "blueprint.graph": {
      if (!isObject(env.graph) || typeof env.graph.id !== "string") break;
      if (!(env.graph.id in draft.graphs)) own(draft, owned, "graphOrder").push(env.graph.id);
      own(draft, owned, "graphs")[env.graph.id] = env.graph;
      break;
    }
    case "span.started": {
      if (!isObject(env.span) || typeof env.span.span_id !== "string") break;
      // A late or replayed start must not regress a span that has already ended.
      const current = draft.spans[env.span.span_id];
      if (current && current.end_ms !== null) break;
      upsertSpan(draft, owned, env.span, env.seq);
      break;
    }
    case "span.ended": {
      if (!isObject(env.span) || typeof env.span.span_id !== "string") break;
      upsertSpan(draft, owned, env.span, env.seq);
      break;
    }
    case "stream.delta": {
      if (typeof env.span_id !== "string" || typeof env.delta !== "string") break;
      appendDelta(draft, owned, env);
      break;
    }
    default:
      // Forward compatibility: event types this viewer does not know are skipped.
      void type;
  }
  return { draft, owned };
}

function isEnvelope(value: unknown): value is Envelope {
  return (
    isObject(value) &&
    typeof value.seq === "number" &&
    typeof value.ts === "number" &&
    typeof value.type === "string"
  );
}

/** Applies a batch of envelopes in order. Malformed entries are skipped. */
export function reduceAll(state: DataState, envelopes: readonly Envelope[]): DataState {
  if (envelopes.length === 0) return state;
  let draft = draftOf(state);
  let owned: Owned = { top: new Set(), traces: new Set(), deltas: new Set() };
  const before = draft.eventCount;
  const beforeSession = draft.session;
  for (const env of envelopes) {
    if (!isEnvelope(env)) continue;
    ({ draft, owned } = apply(draft, owned, env));
  }
  if (draft.eventCount === before && draft.session === beforeSession) return state;
  return draft;
}

/** Applies one envelope and returns the next state. Pure. */
export function reduce(state: DataState, envelope: Envelope): DataState {
  return reduceAll(state, [envelope]);
}
