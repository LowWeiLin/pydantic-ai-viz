/** Small builders for wire values, so tests state only what they are about. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { parseJsonl } from "../data/fileSource";
import { type DataState, initialData, reduceAll } from "../data/reduce";
import type {
  AgentBlueprint,
  AgentRunSpan,
  Capability,
  Deferral,
  DeferredCall,
  Envelope,
  Event,
  GraphBlueprint,
  Hook,
  Json,
  Message,
  ModelRequestSpan,
  RunEvent,
  SessionStarted,
  Skill,
  Span,
  StreamDelta,
  ToolCallSpan,
  Usage,
} from "../wire";

export const TRACE = "0af7651916cd43dd8448eb211c80319c";
export const SESSION = "5f0c2a9e6f3b4d0c9a7e1b2c3d4e5f60";

/** Span ids are written short in tests ("root", "t1") and padded here. */
export const sid = (name: string): string => name.padEnd(16, "0").slice(0, 16);

type Common = {
  id: string;
  parent?: string | null;
  trace?: string;
  start: number;
  end?: number | null;
  status?: Span["status"];
  agent?: string | null;
  error?: Span["error"];
  events?: RunEvent[];
};

function base(c: Common, name: string) {
  const end = c.end ?? null;
  return {
    trace_id: c.trace ?? TRACE,
    span_id: sid(c.id),
    parent_id: c.parent ? sid(c.parent) : null,
    name,
    start_ms: c.start,
    end_ms: end,
    status: c.status ?? (end === null ? ("running" as const) : ("ok" as const)),
    agent_name: c.agent === undefined ? "triage" : c.agent,
    run_id: null,
    error: c.error ?? null,
    attributes: (end === null ? {} : { "gen_ai.operation.name": "x" }) as Record<string, Json>,
    events: end === null ? [] : (c.events ?? []),
  };
}

export function agentRun(
  c: Common & {
    usage?: Usage | null;
    output?: string;
    conversation?: string;
    deferred?: DeferredCall[];
  },
): AgentRunSpan {
  const b = base(c, c.agent ?? "triage");
  const ended = b.end_ms !== null;
  return {
    ...b,
    kind: "agent_run",
    data: {
      model_name: "m",
      conversation_id: c.conversation ?? "c",
      instructions: ended ? "be brief" : null,
      output: ended ? (c.output ?? "done") : null,
      usage: ended ? (c.usage ?? null) : null,
      messages: ended ? [] : null,
      deferred: ended ? (c.deferred ?? []) : [],
    },
  };
}

export function modelRequest(
  c: Common & { usage?: Usage | null; text?: string; input?: Message[] | null },
): ModelRequestSpan {
  const b = base(c, "m");
  const ended = b.end_ms !== null;
  return {
    ...b,
    kind: "model_request",
    data: {
      model_name: "m",
      provider: ended ? "function" : null,
      response_model: ended ? "m" : null,
      settings: ended ? { temperature: 0 } : {},
      instructions: ended ? "be brief" : null,
      input_messages: ended ? (c.input === undefined ? [] : c.input) : null,
      output_messages: ended
        ? [{ role: "assistant", parts: [{ type: "text", content: c.text ?? "hello" }] }]
        : null,
      usage: ended ? (c.usage ?? null) : null,
    },
  };
}

export function toolCall(
  c: Common & {
    tool: string;
    retry?: boolean;
    result?: string;
    callId?: string;
    deferral?: Deferral;
  },
): ToolCallSpan {
  const b = base(c, c.tool);
  const ended = b.end_ms !== null;
  return {
    ...b,
    kind: "tool_call",
    data: {
      tool_name: c.tool,
      call_id: c.callId ?? `call_${sid(c.id)}`,
      arguments: { q: 1 },
      result: ended ? (c.result ?? "ok") : null,
      retry: ended && c.retry ? { cause: "model_retry", attempt: 1, max_retries: 2 } : null,
      deferral: ended ? (c.deferral ?? null) : null,
    },
  };
}

/** The same span before it ended, as `span.started` carries it. */
export function asStarted<S extends Span>(span: S): S {
  switch (span.kind) {
    case "agent_run":
      return agentRun({ ...common(span), end: null }) as S;
    case "model_request":
      return modelRequest({ ...common(span), end: null }) as S;
    case "tool_call":
      return toolCall({ ...common(span), end: null, tool: span.data.tool_name }) as S;
    default:
      return { ...span, end_ms: null, status: "running", error: null, attributes: {} };
  }
}

function common(span: Span): Common {
  return {
    id: span.span_id,
    parent: span.parent_id,
    trace: span.trace_id,
    start: span.start_ms,
    agent: span.agent_name,
  };
}

export const sessionStarted = (session_id: string = SESSION): SessionStarted => ({
  type: "session.started",
  pviz: 1,
  session_id,
  title: "Test session",
  mode: "capture",
  include_content: true,
  stream: true,
  notices: [],
  versions: { pydantic_ai_viz: "0.1.0", pydantic_ai: "2.70.0", python: "3.12.9" },
});

export const delta = (
  span: string,
  part_index: number,
  text: string,
  part_kind: StreamDelta["part_kind"] = "text",
  tool_name: string | null = null,
): StreamDelta => ({
  type: "stream.delta",
  trace_id: TRACE,
  span_id: sid(span),
  part_index,
  part_kind,
  tool_name,
  delta: text,
});

export const capability = (fields: Partial<Capability> & { name: string }): Capability => ({
  builtin: false,
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
  ...fields,
});

export const hook = (fields: Partial<Hook> & { point: string }): Hook => ({
  function: `app.${fields.point}_hook`,
  tools: null,
  timeout: null,
  location: null,
  ...fields,
});

export const skill = (fields: Partial<Skill> & { name: string }): Skill => ({
  description: `What ${fields.name} is for.`,
  when_to_use: null,
  model_invocable: true,
  path: `skills/${fields.name}/SKILL.md`,
  body: `# ${fields.name}\n\nDo it this way.`,
  ...fields,
});

export function agentBlueprint(fields: Partial<AgentBlueprint> & { id: string }): AgentBlueprint {
  const name = fields.name === undefined ? fields.id : fields.name;
  return {
    name,
    label: name ?? fields.id,
    description: null,
    source: "object",
    location: null,
    model: {
      id: "function:m",
      name: "m",
      provider: "function",
      offline: true,
      expression: null,
      model_class: "FunctionModel",
      env: null,
    },
    deps_type: null,
    instructions: [],
    toolsets: [],
    tools: [],
    native_tools: [],
    output: {
      mode: "text",
      allows_text: true,
      type_name: "str",
      json_schema: null,
      tool_names: [],
      validators: [],
      location: null,
    },
    capabilities: [],
    settings: {
      model_settings: null,
      end_strategy: null,
      tool_retries: null,
      output_retries: null,
      tool_timeout: null,
      expressions: {},
      defaults: [],
    },
    delegates: [],
    warnings: [],
    ...fields,
  };
}

export const graphBlueprint = (id: string, nodeIds: string[]): GraphBlueprint => ({
  id,
  name: id,
  label: id,
  source: "object",
  location: null,
  state_type: null,
  input_type: null,
  output_type: null,
  nodes: nodeIds.map((n) => ({ id: n, kind: "step", label: null, note: null, location: null })),
  edges: nodeIds.slice(1).map((n, i) => ({ source: nodeIds[i] ?? n, target: n, label: null })),
  mermaid: null,
  warnings: [],
});

/**
 * Numbers a list of events into envelopes. An event is `[ts, event]`; `seq` is its position.
 * A `session.started` is prepended unless the list already starts with one.
 */
export function log(...events: [number, Event][]): Envelope[] {
  const withSession: [number, Event][] =
    events[0]?.[1].type === "session.started" ? events : [[0, sessionStarted()], ...events];
  return withSession.map(([ts, event], seq) => ({ seq, ts, ...event }));
}

export const started = (span: Span): [number, Event] => [
  span.start_ms,
  { type: "span.started", span: asStarted(span) },
];

export const ended = (span: Span): [number, Event] => [
  span.end_ms ?? span.start_ms,
  { type: "span.ended", span },
];

/** Start and end events for finished spans (start only for running ones), ordered by time. */
export function lifecycle(...spans: Span[]): [number, Event][] {
  const events = spans.flatMap((span) =>
    span.end_ms === null ? [started(span)] : [started(span), ended(span)],
  );
  return events
    .map((e, i) => ({ e, i }))
    .sort((a, b) => a.e[0] - b.e[0] || a.i - b.i)
    .map((x) => x.e);
}

export const stateOf = (...events: [number, Event][]): DataState =>
  reduceAll(initialData, log(...events));

const fixturesDir = path.resolve(import.meta.dirname, "..", "..", "fixtures");

export function fixtureText(name: string): string {
  return readFileSync(path.join(fixturesDir, name), "utf8");
}

export function fixture(name: string): Envelope[] {
  return parseJsonl(fixtureText(name), name);
}

export const fixtureState = (name: string): DataState => reduceAll(initialData, fixture(name));
