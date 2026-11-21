// Writes the hand-authored recordings in web/fixtures. Run with `pnpm fixtures`.
//
// The scenarios below are written by hand against the pviz wire contract (web/src/wire.ts);
// this script only does the bookkeeping a human gets wrong: ids, seq numbers, repeating the
// message history on every model request, and spreading stream deltas over time. Output is
// deterministic, so re-running it leaves the committed files unchanged.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fixturesDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
const VERSIONS = { pydantic_ai_viz: "0.1.0", pydantic_ai: "2.70.0", python: "3.12.9" };

// ---------- bookkeeping ----------

const round = (ms) => Math.round(ms * 1000) / 1000;

/** json.dumps-style text (", " and ": " separators), which is what streamed tool arguments look like. */
function pyJson(value) {
  if (Array.isArray(value)) return `[${value.map(pyJson).join(", ")}]`;
  if (value && typeof value === "object") {
    const body = Object.entries(value).map(([k, v]) => `${JSON.stringify(k)}: ${pyJson(v)}`);
    return `{${body.join(", ")}}`;
  }
  return JSON.stringify(value);
}

function chunkText(text) {
  return text.match(/\S+\s*/g) ?? [];
}

function chunkArgs(json) {
  const size = Math.ceil(json.length / 3);
  return [json.slice(0, size), json.slice(size, size * 2), json.slice(size * 2)].filter(Boolean);
}

class Recording {
  constructor({ title, seed, t0, content = true, stream = true }) {
    this.t0 = t0;
    this.content = content;
    this.stream = stream;
    this.state = seed >>> 0;
    this.pending = [];
    this.header = {
      type: "session.started",
      pviz: 1,
      session_id: this.hex(32),
      title,
      mode: "capture",
      include_content: content,
      stream,
      notices: [],
      versions: VERSIONS,
    };
  }

  hex(length) {
    let out = "";
    while (out.length < length) {
      this.state = (Math.imul(this.state, 1664525) + 1013904223) >>> 0;
      out += this.state.toString(16).padStart(8, "0");
    }
    return out.slice(0, length);
  }

  uuid() {
    const h = this.hex(32);
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-7${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20)}`;
  }

  /** Content-bearing values become null when content capture is off. */
  c(value) {
    return this.content ? value : null;
  }

  /** Queues an event at `ms` after session start. Events at the same time keep call order. */
  emit(ms, event) {
    this.pending.push({ ts: round(this.t0 + ms), order: this.pending.length, event });
  }

  jsonl() {
    const sorted = [...this.pending].sort((a, b) => a.ts - b.ts || a.order - b.order);
    const envelopes = [
      { seq: 0, ts: this.t0, ...this.header },
      ...sorted.map(({ ts, event }, i) => ({ seq: i + 1, ts, ...event })),
    ];
    return `${envelopes.map((e) => JSON.stringify(e)).join("\n")}\n`;
  }

  /** Emits span.started now and returns a function that emits span.ended. */
  span(base, startData) {
    const head = {
      trace_id: base.trace,
      span_id: base.id ?? this.hex(16),
      parent_id: base.parent ?? null,
      kind: base.kind,
      name: base.name,
      start_ms: round(this.t0 + base.start),
    };
    const owner = { agent_name: base.agent ?? null, run_id: base.runId ?? null };
    this.emit(base.start, {
      type: "span.started",
      span: {
        ...head,
        end_ms: null,
        status: "running",
        ...owner,
        error: null,
        attributes: {},
        data: startData,
      },
    });
    const end = (ms, { data = {}, status = "ok", error = null, attributes = {} } = {}) => {
      this.emit(ms, {
        type: "span.ended",
        span: {
          ...head,
          end_ms: round(this.t0 + ms),
          status,
          ...owner,
          error,
          attributes,
          data: startData === null ? null : { ...startData, ...data },
        },
      });
    };
    return { id: head.span_id, end, head, owner };
  }
}

/** One agent run: keeps the conversation so each request repeats the history, as the real spans do. */
class AgentRun {
  constructor(rec, o) {
    this.rec = rec;
    this.o = o;
    this.runId = rec.uuid();
    this.conversationId = rec.uuid();
    this.history = [];
    if (o.system)
      this.history.push({ role: "system", parts: [{ type: "text", content: rec.c(o.system) }] });
    this.history.push({ role: "user", parts: [{ type: "text", content: rec.c(o.prompt) }] });
    this.results = [];
    this.usage = { input_tokens: 0, output_tokens: 0 };
    this.root = rec.span(
      {
        trace: o.trace,
        parent: o.parent,
        kind: "agent_run",
        name: o.agent,
        start: o.start,
        agent: o.agent,
        runId: this.runId,
      },
      {
        model_name: o.model,
        conversation_id: this.conversationId,
        instructions: null,
        output: null,
        usage: null,
        messages: null,
      },
    );
    this.id = this.root.id;
  }

  child(kind, name, start) {
    const { trace, agent } = this.o;
    return { trace, parent: this.id, kind, name, start, agent, runId: this.runId };
  }

  flushResults() {
    if (this.results.length === 0) return;
    this.history.push({ role: "tool", parts: this.results });
    this.results = [];
  }

  /**
   * A model request from `start` to `end`. With `end: null` the request is left open (the
   * recording stops mid-stream) and deltas run until `until`.
   */
  request(start, end, { thinking, text, calls = [], usage, until, error } = {}) {
    const { rec, o } = this;
    this.flushResults();
    const input = structuredClone(this.history);
    const span = rec.span(this.child("model_request", o.model, start), {
      model_name: o.model,
      provider: null,
      response_model: null,
      settings: {},
      instructions: null,
      input_messages: null,
      output_messages: null,
      usage: null,
    });

    const parts = [];
    const streams = [];
    if (thinking) {
      parts.push({ type: "thinking", content: rec.c(thinking) });
      streams.push({ kind: "thinking", tool: null, chunks: chunkText(thinking) });
    }
    if (text) {
      parts.push({ type: "text", content: rec.c(text) });
      streams.push({ kind: "text", tool: null, chunks: chunkText(text) });
    }
    for (const call of calls) {
      parts.push({ type: "tool_call", id: call.id, name: call.name, arguments: rec.c(call.args) });
      streams.push({ kind: "tool_call", tool: call.name, chunks: chunkArgs(pyJson(call.args)) });
    }

    if (rec.stream && rec.content) {
      // The model "thinks" for the first third, then parts stream one after another.
      const stop = end ?? until;
      const from = start + (stop - start) * 0.34;
      const total = streams.reduce((n, s) => n + s.chunks.length, 0);
      const gap = (stop - 4 - from) / Math.max(1, total - 1);
      let n = 0;
      streams.forEach((stream, partIndex) => {
        for (const delta of stream.chunks) {
          rec.emit(from + gap * n++, {
            type: "stream.delta",
            trace_id: o.trace,
            span_id: span.id,
            part_index: partIndex,
            part_kind: stream.kind,
            tool_name: stream.tool,
            delta,
          });
        }
      });
    }
    if (end === null) return span;

    const attributes = {
      "gen_ai.operation.name": "chat",
      "gen_ai.conversation.id": this.conversationId,
    };
    if (error) {
      span.end(end, { status: "error", error, attributes });
      return span;
    }
    span.end(end, {
      attributes,
      data: {
        provider: o.provider ?? "function",
        response_model: o.model,
        settings: o.settings ?? {},
        instructions: rec.c(o.instructions),
        input_messages: input,
        output_messages: [{ role: "assistant", parts }],
        usage,
      },
    });
    this.history.push({ role: "assistant", parts });
    this.usage.input_tokens += usage.input_tokens;
    this.usage.output_tokens += usage.output_tokens;
    return span;
  }

  /** Starts a tool call; call `.done(ms, {...})` on the result to end it. */
  tool(start, call) {
    const { rec } = this;
    const span = rec.span(this.child("tool_call", call.name, start), {
      tool_name: call.name,
      call_id: call.id,
      arguments: rec.c(call.args),
      result: null,
      retry: null,
    });
    const done = (end, { result, retry = null, error = null } = {}) => {
      span.end(end, {
        status: error ? "error" : "ok",
        error,
        attributes: {
          "gen_ai.operation.name": "execute_tool",
          "logfire.msg": `running tool: ${call.name}`,
        },
        data: { result: rec.c(result ?? null), retry },
      });
      // An unhandled tool exception ends the run; nothing is sent back to the model.
      if (!error || retry) {
        this.results.push({
          type: "tool_result",
          id: call.id,
          name: call.name,
          result: rec.c(result ?? null),
        });
      }
    };
    return { id: span.id, done };
  }

  finish(end, { output = null, error = null, usage = this.usage, finalResultId } = {}) {
    const { rec, o } = this;
    if (finalResultId) {
      this.results.push({
        type: "tool_result",
        id: finalResultId,
        name: "final_result",
        result: rec.c("Final result processed."),
      });
    }
    this.flushResults();
    this.root.end(end, {
      status: error ? "error" : "ok",
      error,
      attributes: { "gen_ai.operation.name": "invoke_agent", "logfire.msg": `${o.agent} run` },
      data: {
        instructions: rec.c(o.instructions),
        output: rec.c(output),
        usage,
        messages: structuredClone(this.history),
      },
    });
  }
}

// ---------- blueprint shorthand ----------

const objectSchema = (properties, required = Object.keys(properties)) => ({
  type: "object",
  properties,
  required,
  additionalProperties: false,
});

const tool = (name, toolset, description, parameters, extra = {}) => ({
  name,
  toolset,
  description,
  parameters_schema: parameters,
  return_schema: null,
  kind: "function",
  takes_ctx: false,
  is_async: true,
  max_retries: null,
  function: null,
  origin: "object",
  location: null,
  library_version: null,
  ...extra,
});

const agentTools = {
  id: "ts0",
  toolset_id: "<agent>",
  label: "Agent tools",
  kind: "agent",
  wrappers: [],
  opaque: false,
};

const BUILTIN_CAPABILITIES = [
  {
    name: "ToolSearch",
    builtin: true,
    category: "context",
    package: "pydantic_ai",
    deferred: false,
    details: {},
    expressions: {},
    toolsets: [],
    native_tools: ["tool_search"],
  },
  {
    name: "PendingMessageDrainCapability",
    builtin: true,
    category: "runtime",
    package: "pydantic_ai",
    deferred: false,
    details: {},
    expressions: {},
    toolsets: [],
    native_tools: [],
  },
];

/** Adds the fields that only static analysis fills in, so the blueprints below list what matters. */
const unlocated = (item) => ({ ...item, expression: null, location: null });

/** A model with every field, keys in the order the backend writes them. */
const fullModel = (model) => {
  const full = { expression: null, ...model };
  return { ...full, model_class: full.model_class ?? null, env: full.env ?? null };
};

const complete = (blueprint) => ({
  ...blueprint,
  model: blueprint.model && {
    id: blueprint.model.id,
    name: blueprint.model.name,
    provider: blueprint.model.provider,
    offline: blueprint.model.offline,
    expression: null,
    model_class: blueprint.model.model_class ?? null,
    env: blueprint.model.env ?? null,
  },
  instructions: blueprint.instructions.map(unlocated),
  toolsets: blueprint.toolsets.map(unlocated),
  output: { ...blueprint.output, location: null },
  settings: { ...blueprint.settings, expressions: {} },
});

const agent = (fields) =>
  complete({
    description: null,
    source: "object",
    location: null,
    deps_type: null,
    instructions: [],
    toolsets: [],
    tools: [],
    native_tools: [],
    output: {
      mode: "text",
      allows_text: true,
      type_name: "str",
      json_schema: { type: "string" },
      tool_names: [],
    },
    capabilities: BUILTIN_CAPABILITIES,
    settings: {
      model_settings: null,
      end_strategy: "early",
      tool_retries: 1,
      output_retries: 1,
      tool_timeout: null,
    },
    delegates: [],
    warnings: [],
    ...fields,
  });

/** An agent read from source: what `agent` blanks, and only what static analysis can say. */
const codeAgent = (fields) => {
  const built = agent({ capabilities: [], ...fields });
  return {
    ...built,
    source: "code",
    // `agent` blanks what only static analysis fills in; here that is the point.
    model: fullModel(fields.model),
    instructions: fields.instructions ?? [],
    toolsets: (fields.toolsets ?? []).map((toolset) => ({
      expression: null,
      location: null,
      ...toolset,
    })),
    output: fields.output ?? built.output,
    // Static analysis reads what the code sets and never fills in a default.
    settings: {
      model_settings: null,
      end_strategy: null,
      tool_retries: null,
      output_retries: null,
      tool_timeout: null,
      expressions: {},
      ...fields.settings,
    },
  };
};

const offlineModel = (name) => ({
  id: `function:${name}`,
  name,
  provider: "function",
  offline: true,
  model_class: "FunctionModel",
  env: null,
});

const retryError = (message) => ({
  type: "pydantic_ai.exceptions.ToolRetryError",
  message,
  stacktrace:
    "Traceback (most recent call last):\n" +
    '  File "pydantic_ai/_tool_manager.py", line 312, in _call_tool\n' +
    "    return await toolset.call_tool(name, args_dict, ctx, tool)\n" +
    `pydantic_ai.exceptions.ModelRetry: ${message}\n`,
});

// ---------- sample.pviz.jsonl ----------

function sample() {
  const rec = new Recording({ title: "Sample session", seed: 0x5eed0001, t0: 1795046400000 });
  const mod = "pydantic_ai_viz.demos.support";

  const refunds = agent({
    id: "a2",
    name: "refunds",
    label: "refunds",
    description: "Checks eligibility and issues refunds",
    model: offlineModel("scripted-support"),
    deps_type: `${mod}.SupportDeps`,
    instructions: [
      {
        kind: "instructions",
        dynamic: false,
        text: "You issue refunds. Check eligibility first, then issue the refund and confirm the reference.",
        function: null,
      },
    ],
    toolsets: [agentTools],
    tools: [
      tool(
        "issue_refund",
        "ts0",
        "Issue a refund to the original payment method.",
        objectSchema({
          order_id: { type: "string", description: "Order id, for example A-1042." },
          amount_eur: {
            type: "number",
            description: "Amount to refund, in euros.",
            exclusiveMinimum: 0,
          },
        }),
        {
          return_schema: objectSchema({
            reference: { type: "string" },
            amount_eur: { type: "number" },
            method: { type: "string" },
          }),
          takes_ctx: true,
          function: `${mod}.issue_refund`,
        },
      ),
    ],
  });

  const triage = (evidence) =>
    agent({
      id: "a1",
      name: "triage",
      label: "triage",
      description: "Resolves customer support tickets",
      model: offlineModel("scripted-support"),
      deps_type: `${mod}.SupportDeps`,
      instructions: [
        {
          kind: "instructions",
          dynamic: false,
          text: "You are a support triage agent. Resolve the ticket or hand it to the right specialist.",
          function: null,
        },
        { kind: "instructions", dynamic: true, text: null, function: `${mod}.customer_context` },
        { kind: "system_prompt", dynamic: false, text: "Be concise.", function: null },
      ],
      toolsets: [
        agentTools,
        {
          id: "ts1",
          toolset_id: "kb",
          label: "FunctionToolset 'kb'",
          kind: "function",
          wrappers: [],
          opaque: false,
        },
      ],
      tools: [
        tool(
          "get_order",
          "ts0",
          "Fetch an order by id.",
          objectSchema({
            order_id: { type: "string", description: "Order id, for example A-1042." },
          }),
          {
            return_schema: objectSchema({
              order_id: { type: "string" },
              item: { type: "string" },
              total_eur: { type: "number" },
              delivered: { type: "string", format: "date" },
              status: { type: "string", enum: ["processing", "shipped", "delivered", "returned"] },
            }),
            takes_ctx: true,
            max_retries: 2,
            function: `${mod}.get_order`,
          },
        ),
        tool(
          "get_customer",
          "ts0",
          "Look up the customer who opened the ticket.",
          objectSchema(
            {
              include_history: {
                type: "boolean",
                default: false,
                description: "Include past tickets.",
              },
            },
            [],
          ),
          { takes_ctx: true, function: `${mod}.get_customer` },
        ),
        tool(
          "escalate_refund",
          "ts0",
          "Hand the refund to the refunds agent.",
          objectSchema({
            order_id: { type: "string" },
            reason: { type: "string", description: "Why the customer is owed a refund." },
          }),
          { takes_ctx: true, function: `${mod}.escalate_refund` },
        ),
        tool(
          "search_kb",
          "ts1",
          "Search the knowledge base.",
          objectSchema(
            {
              query: { type: "string" },
              limit: { type: "integer", default: 3, minimum: 1, maximum: 10 },
            },
            ["query"],
          ),
          { is_async: false, function: `${mod}.search_kb` },
        ),
      ],
      native_tools: ["tool_search"],
      output: {
        mode: "tool",
        allows_text: false,
        type_name: "Resolution",
        json_schema: {
          type: "object",
          title: "Resolution",
          properties: {
            status: { type: "string", enum: ["resolved", "refunded", "escalated"] },
            refund_eur: { type: "number", description: "Amount refunded, 0 if none." },
            reply: { type: "string", description: "Message sent to the customer." },
          },
          required: ["status", "refund_eur", "reply"],
        },
        tool_names: ["final_result"],
      },
      settings: {
        model_settings: { temperature: 0.2 },
        end_strategy: "graceful",
        tool_retries: 3,
        output_retries: 3,
        tool_timeout: null,
      },
      delegates: [{ tool_name: "escalate_refund", agent_id: "a2", evidence }],
    });

  const pipelineMod = "pydantic_ai_viz.demos.pipeline";
  const writer = agent({
    id: "a3",
    name: "writer",
    label: "writer",
    description: "Drafts short release notes",
    model: offlineModel("scripted-pipeline"),
    instructions: [
      {
        kind: "instructions",
        dynamic: false,
        text: "Write two plain sentences. No marketing words.",
        function: null,
      },
    ],
  });
  const critic = agent({
    id: "a4",
    name: "critic",
    label: "critic",
    description: "Approves a draft or asks for changes",
    model: offlineModel("scripted-pipeline"),
    instructions: [
      {
        kind: "instructions",
        dynamic: false,
        text: "Approve the draft only if it says what the release does and what it needs to run.",
        function: null,
      },
    ],
    output: {
      mode: "tool",
      allows_text: false,
      type_name: "Review",
      json_schema: {
        type: "object",
        title: "Review",
        properties: { approved: { type: "boolean" }, notes: { type: "string" } },
        required: ["approved", "notes"],
      },
      tool_names: ["final_result"],
    },
  });
  const pipeline = {
    id: "g1",
    name: "pipeline",
    label: "pipeline",
    source: "object",
    location: null,
    state_type: `${pipelineMod}.Draft`,
    input_type: "str",
    output_type: "str",
    nodes: [
      { id: "__start__", kind: "start", label: null, note: null, location: null },
      { id: "write", kind: "step", label: "Write draft", note: null, location: null },
      { id: "review", kind: "step", label: "Review", note: null, location: null },
      { id: "decision", kind: "decision", label: null, note: "approved?", location: null },
      { id: "publish", kind: "step", label: "Publish", note: null, location: null },
      { id: "__end__", kind: "end", label: null, note: null, location: null },
    ],
    edges: [
      { source: "__start__", target: "write", label: null },
      { source: "write", target: "review", label: null },
      { source: "review", target: "decision", label: null },
      { source: "decision", target: "publish", label: "approved" },
      { source: "decision", target: "write", label: "needs work" },
      { source: "publish", target: "__end__", label: null },
    ],
    mermaid:
      "stateDiagram-v2\n  direction LR\n  [*] --> write\n  write --> review\n  state decision <<choice>>\n" +
      "  review --> decision\n  decision --> publish: approved\n  decision --> write: needs work\n  publish --> [*]",
    warnings: [],
  };

  rec.emit(1.2, { type: "blueprint.agent", agent: triage("object") });
  rec.emit(1.6, { type: "blueprint.agent", agent: refunds });
  rec.emit(2.1, { type: "blueprint.graph", graph: pipeline });
  rec.emit(2.5, { type: "blueprint.agent", agent: writer });
  rec.emit(2.8, { type: "blueprint.agent", agent: critic });

  // --- run 1: triage, with three parallel tools, one retry and a delegated refund ---
  const trace1 = rec.hex(32);
  const run = new AgentRun(rec, {
    trace: trace1,
    agent: "triage",
    model: "scripted-support",
    settings: { temperature: 0.2 },
    start: 1000,
    instructions:
      "You are a support triage agent. Resolve the ticket or hand it to the right specialist.\n\n" +
      "Customer: Ann Lee (gold tier, customer since 2021).",
    system: "Be concise.",
    prompt: "My order 1042 arrived with a cracked screen. I'd like a refund.",
  });

  const order1 = { id: "call_ord1", name: "get_order", args: { order_id: "1042" } };
  const customer = { id: "call_cus1", name: "get_customer", args: { include_history: true } };
  const kb = {
    id: "call_kb1",
    name: "search_kb",
    args: { query: "damaged on arrival refund policy" },
  };
  run.request(1002.4, 2180, {
    text: "I'll pull up the order, your account and our policy on damaged items.",
    calls: [order1, customer, kb],
    usage: { input_tokens: 412, output_tokens: 58 },
  });
  const hint = "Order ids look like 'A-1042'. Fix the id and try again.";
  const t1 = run.tool(2186.1, order1);
  const t2 = run.tool(2186.9, customer);
  const t3 = run.tool(2187.6, kb);
  t1.done(2410.3, {
    result: hint,
    retry: { cause: "model_retry", attempt: 1, max_retries: 2 },
    error: retryError(hint),
  });
  t2.done(2655.8, {
    result: {
      customer_id: "c_2187",
      name: "Ann Lee",
      tier: "gold",
      since: "2021-03-14",
      open_tickets: 1,
    },
  });
  t3.done(2890.2, {
    result: [
      {
        id: "kb-112",
        title: "Damaged on arrival",
        snippet:
          "Items damaged in transit are refunded in full within 30 days of delivery. No return is needed below EUR 50.",
      },
      {
        id: "kb-087",
        title: "Refund timelines",
        snippet: "Refunds reach the original payment method in 3 to 5 working days.",
      },
    ],
  });

  const order2 = { id: "call_ord2", name: "get_order", args: { order_id: "A-1042" } };
  run.request(2898, 3540, { calls: [order2], usage: { input_tokens: 655, output_tokens: 21 } });
  run.tool(3546.2, order2).done(3900.7, {
    result: {
      order_id: "A-1042",
      item: "Lumen 11 e-reader",
      total_eur: 42.0,
      delivered: "2026-11-12",
      status: "delivered",
    },
  });

  const escalate = {
    id: "call_esc1",
    name: "escalate_refund",
    args: { order_id: "A-1042", reason: "Screen cracked on arrival" },
  };
  run.request(3908, 4620, {
    text: "The order is inside the 30-day window and under the no-return limit, so it qualifies. Handing it to refunds.",
    calls: [escalate],
    usage: { input_tokens: 742, output_tokens: 49 },
  });
  const delegation = run.tool(4626.4, escalate);

  const sub = new AgentRun(rec, {
    trace: trace1,
    parent: delegation.id,
    agent: "refunds",
    model: "scripted-support",
    start: 4631,
    instructions:
      "You issue refunds. Check eligibility first, then issue the refund and confirm the reference.",
    prompt: "Refund order A-1042. Reason: Screen cracked on arrival",
  });
  // The session sees the delegated run start under the tool call and upgrades the edge.
  rec.emit(4631.4, { type: "blueprint.agent", agent: triage("observed") });
  const issue = {
    id: "call_ref1",
    name: "issue_refund",
    args: { order_id: "A-1042", amount_eur: 42.0 },
  };
  sub.request(4634.2, 5180, {
    thinking:
      "Order A-1042 is EUR 42.00 and was delivered six days ago. It is eligible, so issue the full amount.",
    calls: [issue],
    usage: { input_tokens: 188, output_tokens: 37 },
  });
  sub.tool(5186.3, issue).done(5610.9, {
    result: { reference: "RF-88213", amount_eur: 42.0, method: "original payment method" },
  });
  const confirmation = "Refund RF-88213 of EUR 42.00 issued to the original payment method.";
  sub.request(5618, 6228, { text: confirmation, usage: { input_tokens: 251, output_tokens: 24 } });
  sub.finish(6234.5, { output: confirmation });
  delegation.done(6240.2, { result: confirmation });

  const resolution = {
    status: "refunded",
    refund_eur: 42.0,
    reply:
      "I'm sorry about the cracked screen. I've refunded EUR 42.00 to your original payment method " +
      "(reference RF-88213). You don't need to send the e-reader back.",
  };
  run.request(6248, 7310, {
    calls: [{ id: "call_fin1", name: "final_result", args: resolution }],
    usage: { input_tokens: 861, output_tokens: 64 },
  });
  // The sub-agent ran with usage=ctx.usage, so the parent's aggregate already includes it.
  run.finish(7318.6, {
    output: resolution,
    finalResultId: "call_fin1",
    usage: {
      input_tokens: run.usage.input_tokens + sub.usage.input_tokens,
      output_tokens: run.usage.output_tokens + sub.usage.output_tokens,
    },
  });

  // --- run 2: the pipeline graph, with a "needs work" loop and agents inside steps ---
  const trace2 = rec.hex(32);
  const graphBase = { trace: trace2, agent: null, runId: null };
  const topic = "Release notes for 0.1";
  const graphRun = rec.span(
    { ...graphBase, kind: "graph_run", name: "pipeline", start: 10000 },
    { graph_id: "g1", graph_name: "pipeline", inputs: topic, output: null },
  );
  const node = (nodeId, start, inputs) =>
    rec.span(
      { ...graphBase, parent: graphRun.id, kind: "graph_node", name: nodeId, start },
      { graph_id: "g1", node_id: nodeId, inputs },
    );
  const nodeAttrs = { attributes: { "pydantic_ai_viz.graph.name": "pipeline" } };

  const draft1 =
    "pydantic-ai-viz 0.1 is out. It draws your agents as blueprints and replays their runs.";
  const draft2 =
    "pydantic-ai-viz 0.1 is out. It draws your agents as blueprints and replays their runs, " +
    "and it needs no API key or account.";
  const notes = "Say what it needs to run: no API key, no account.";

  const write = (start, end, prompt, draft, usage) => {
    const step = node("write", start, prompt);
    const w = new AgentRun(rec, {
      trace: trace2,
      parent: step.id,
      agent: "writer",
      model: "scripted-pipeline",
      start: start + 3,
      instructions: "Write two plain sentences. No marketing words.",
      prompt,
    });
    w.request(start + 5.5, end - 14, { text: draft, usage });
    w.finish(end - 8, { output: draft });
    step.end(end, nodeAttrs);
  };
  const review = (start, end, draft, verdict, usage) => {
    const step = node("review", start, draft);
    const r = new AgentRun(rec, {
      trace: trace2,
      parent: step.id,
      agent: "critic",
      model: "scripted-pipeline",
      start: start + 3,
      instructions:
        "Approve the draft only if it says what the release does and what it needs to run.",
      prompt: draft,
    });
    const id = `call_rev${verdict.approved ? 2 : 1}`;
    r.request(start + 5.5, end - 14, {
      calls: [{ id, name: "final_result", args: verdict }],
      usage,
    });
    r.finish(end - 8, { output: verdict, finalResultId: id });
    step.end(end, nodeAttrs);
  };

  write(10001.2, 11250, topic, draft1, { input_tokens: 96, output_tokens: 31 });
  review(
    11252.4,
    12020,
    draft1,
    { approved: false, notes },
    { input_tokens: 143, output_tokens: 22 },
  );
  write(12024.1, 13130, `${topic}\n\nReviewer notes: ${notes}`, draft2, {
    input_tokens: 131,
    output_tokens: 38,
  });
  review(
    13133.7,
    13790,
    draft2,
    { approved: true, notes: "Clear and complete." },
    { input_tokens: 178, output_tokens: 15 },
  );
  node("publish", 13793.3, draft2).end(13840.6, nodeAttrs);
  node("__end__", 13841.2, `Published: ${draft2}`).end(13842, nodeAttrs);
  graphRun.end(13845.5, {
    attributes: { "pydantic_ai_viz.graph.name": "pipeline" },
    data: { output: `Published: ${draft2}` },
  });

  return rec;
}

// ---------- failed.pviz.jsonl ----------

function failed() {
  const rec = new Recording({ title: "Nightly digest", seed: 0x5eed0002, t0: 1795132800000 });
  const instructions = "Fetch every feed, then write a five-line digest of what changed.";
  const feedSchema = objectSchema({ url: { type: "string", format: "uri" } });

  // Nobody registered this agent: the session builds its blueprint from what the spans show.
  const observed = (extra = {}) =>
    agent({
      id: "a1",
      name: "digest",
      label: "digest",
      source: "observed",
      model: { id: null, name: "scripted-digest", provider: null, offline: false },
      output: {
        mode: "unknown",
        allows_text: null,
        type_name: null,
        json_schema: null,
        tool_names: [],
      },
      capabilities: [],
      settings: {
        model_settings: null,
        end_strategy: null,
        tool_retries: null,
        output_retries: null,
        tool_timeout: null,
      },
      ...extra,
    });
  const observedFull = observed({
    model: { id: null, name: "scripted-digest", provider: "function", offline: true },
    instructions: [{ kind: "instructions", dynamic: false, text: instructions, function: null }],
    toolsets: [
      {
        id: "ts0",
        toolset_id: "<agent>",
        label: "Agent tools",
        kind: "agent",
        wrappers: [],
        opaque: false,
      },
      {
        id: "ts1",
        toolset_id: "feeds",
        label: "Toolset 'feeds'",
        kind: "other",
        wrappers: [],
        opaque: false,
      },
    ],
    tools: [
      tool("fetch_feed", "ts1", "Download one RSS or Atom feed.", feedSchema, {
        takes_ctx: null,
        is_async: null,
        origin: "observed",
      }),
      tool(
        "save_digest",
        "ts0",
        "Store the finished digest.",
        objectSchema({ text: { type: "string" } }),
        {
          takes_ctx: null,
          is_async: null,
          origin: "observed",
        },
      ),
    ],
    output: { mode: "text", allows_text: true, type_name: null, json_schema: null, tool_names: [] },
    warnings: [
      "Built from observed runs. Register the agent with serve() or record() for full detail.",
    ],
  });

  // --- run 1: started before capture was attached, so only span.ended events exist ---
  const trace0 = rec.hex(32);
  const early = { trace: trace0, agent: "digest", runId: rec.uuid() };
  const earlyRoot = rec.hex(16);
  const earlyOut = "Nothing new since yesterday.";
  const ended = (ms, base, data, attributes) => {
    rec.emit(ms, {
      type: "span.ended",
      span: {
        trace_id: base.trace,
        span_id: base.id,
        parent_id: base.parent ?? null,
        kind: base.kind,
        name: base.name,
        start_ms: round(rec.t0 + base.start),
        end_ms: round(rec.t0 + ms),
        status: "ok",
        agent_name: base.agent,
        run_id: base.runId,
        error: null,
        attributes,
        data,
      },
    });
  };
  const earlyMessages = [
    { role: "user", parts: [{ type: "text", content: "Anything new in the last hour?" }] },
  ];
  const earlyReply = [{ role: "assistant", parts: [{ type: "text", content: earlyOut }] }];
  ended(
    212.4,
    {
      ...early,
      id: rec.hex(16),
      parent: earlyRoot,
      kind: "model_request",
      name: "scripted-digest",
      start: -310,
    },
    {
      model_name: "scripted-digest",
      provider: "function",
      response_model: "scripted-digest",
      settings: {},
      instructions,
      input_messages: earlyMessages,
      output_messages: earlyReply,
      usage: { input_tokens: 64, output_tokens: 9 },
    },
    { "gen_ai.operation.name": "chat" },
  );
  rec.emit(213, { type: "blueprint.agent", agent: observedFull });
  ended(
    218.9,
    { ...early, id: earlyRoot, kind: "agent_run", name: "digest", start: -318 },
    {
      model_name: "scripted-digest",
      conversation_id: rec.uuid(),
      instructions,
      output: earlyOut,
      usage: { input_tokens: 64, output_tokens: 9 },
      messages: [...earlyMessages, ...earlyReply],
    },
    { "gen_ai.operation.name": "invoke_agent", "logfire.msg": "digest run" },
  );

  // --- run 2: one of two parallel fetches times out and takes the run down with it ---
  const trace1 = rec.hex(32);
  const run = new AgentRun(rec, {
    trace: trace1,
    agent: "digest",
    model: "scripted-digest",
    start: 2000,
    instructions,
    prompt: "Build tonight's digest from the release feeds.",
  });
  const feedA = {
    id: "call_feed1",
    name: "fetch_feed",
    args: { url: "https://feeds.example.test/pydantic-ai.atom" },
  };
  const feedB = {
    id: "call_feed2",
    name: "fetch_feed",
    args: { url: "https://feeds.example.test/logfire.atom" },
  };
  run.request(2003.1, 2760, {
    text: "Fetching both feeds.",
    calls: [feedA, feedB],
    usage: { input_tokens: 231, output_tokens: 44 },
  });
  const a = run.tool(2766.2, feedA);
  const b = run.tool(2767, feedB);
  // A plain child span from another instrumented library: kind "other".
  const http = rec.span(
    {
      trace: trace1,
      parent: b.id,
      kind: "other",
      name: "GET feeds.example.test",
      start: 2770.5,
      agent: null,
      runId: null,
    },
    null,
  );
  a.done(3120.4, {
    result: {
      title: "pydantic-ai releases",
      entries: [{ title: "v2.70.0", updated: "2026-11-17T09:12:00Z" }],
    },
  });
  const timeout = {
    type: "httpx.ConnectTimeout",
    message: "timed out connecting to feeds.example.test:443",
    stacktrace:
      "Traceback (most recent call last):\n" +
      '  File "digest/feeds.py", line 41, in fetch_feed\n' +
      "    response = await client.get(url, timeout=5)\n" +
      '  File "httpx/_client.py", line 1768, in get\n' +
      "    return await self.request(...)\n" +
      "httpx.ConnectTimeout: timed out connecting to feeds.example.test:443\n",
  };
  http.end(7771.8, {
    status: "error",
    error: { type: "Error", message: "ConnectTimeout", stacktrace: null },
    attributes: {
      "http.request.method": "GET",
      "server.address": "feeds.example.test",
      "server.port": 443,
    },
  });
  b.done(7774.3, { error: timeout });
  run.finish(7780.9, { error: timeout });

  // --- run 3: the recording was saved while a request was still streaming ---
  const trace2 = rec.hex(32);
  const open = new AgentRun(rec, {
    trace: trace2,
    agent: "digest",
    model: "scripted-digest",
    start: 9000,
    instructions,
    prompt: "Try again with the pydantic-ai feed only.",
  });
  open.request(9002.7, null, {
    thinking: "Only one feed this time. Fetch it, then summarise the newest entries.",
    text: "Fetching the pydantic-ai feed",
    until: 9900,
  });

  return rec;
}

// ---------- redacted.pviz.jsonl ----------

function redacted() {
  const rec = new Recording({
    title: "Claims intake (content capture off)",
    seed: 0x5eed0003,
    t0: 1795219200000,
    content: false,
    stream: false,
  });
  rec.emit(1.4, {
    type: "blueprint.agent",
    agent: agent({
      id: "a1",
      name: "intake",
      label: "intake",
      description: "Files insurance claims from customer messages",
      model: { id: "openai:gpt-5.2", name: "gpt-5.2", provider: "openai", offline: false },
      instructions: [
        {
          kind: "instructions",
          dynamic: false,
          text: "Extract the claim and file it. Never guess a policy number.",
          function: null,
        },
      ],
      toolsets: [
        agentTools,
        {
          id: "ts1",
          toolset_id: null,
          label: "MCPServerStdio",
          kind: "mcp",
          wrappers: ["PrefixedToolset"],
          opaque: true,
        },
      ],
      tools: [
        tool(
          "file_claim",
          "ts0",
          "Create a claim in the claims system.",
          objectSchema(
            {
              policy_number: { type: "string" },
              incident: { type: "string" },
              amount_eur: { anyOf: [{ type: "number" }, { type: "null" }], default: null },
            },
            ["policy_number", "incident"],
          ),
          { takes_ctx: true },
        ),
      ],
      warnings: ["output mode unavailable (pydantic-ai internals changed)"],
    }),
  });

  const run = new AgentRun(rec, {
    trace: rec.hex(32),
    agent: "intake",
    model: "gpt-5.2",
    provider: "openai",
    start: 500,
    instructions: "redacted",
    prompt: "redacted",
  });
  const file = { id: "call_Zk3fQ9", name: "file_claim", args: {} };
  run.request(503.2, 1904.5, { calls: [file], usage: { input_tokens: 318, output_tokens: 41 } });
  run.tool(1909.8, file).done(2377.1, { result: "redacted" });
  run.request(2384, 3120.6, { text: "redacted", usage: { input_tokens: 402, output_tokens: 27 } });
  run.finish(3126.2, { output: "redacted" });
  return rec;
}

// ---------- blueprint/newsroom.pviz.jsonl ----------

/**
 * What `pydantic-ai-viz view newsroom/` writes: a blueprint-only session read from source
 * code. Four agents and a graph in four files, with everything static analysis can say and
 * everything it cannot: a model and settings that are expressions, an f-string and a computed
 * instruction, an opaque toolset, a type it could not resolve, delegation across files, and a
 * file it had to skip. It has no spans, so it lives in a folder of its own: the checks on the
 * recordings next to it are about runs.
 */
function newsroom() {
  const rec = new Recording({
    title: "newsroom",
    seed: 0x5eed0004,
    t0: 1795046400000,
    stream: false,
  });
  rec.header.mode = "blueprint";
  rec.header.notices = [
    "newsroom/legacy.py was skipped: it is not valid Python (invalid syntax on line 12)",
  ];

  const at = (file, line) => ({ file: `newsroom/${file}`, line });
  const fromCode = (name, toolset, description, parameters, file, line, extra = {}) =>
    tool(name, toolset, description, parameters, {
      origin: "code",
      function: `newsroom.${file.replace(/\.py$/, "")}.${name}`,
      location: at(file, line),
      return_schema: { type: "string" },
      ...extra,
    });
  const literal = (text, file, line, kind = "instructions") => ({
    kind,
    dynamic: false,
    text,
    function: null,
    expression: null,
    location: at(file, line),
  });
  const computedBy = (what, code) =>
    `${what} is set by \`${code}\`, so it is only known when the code runs`;

  const story = {
    type: "object",
    properties: {
      headline: { type: "string", description: "At most twelve words." },
      body: { type: "string" },
      sources: { type: "array", items: { type: "string", format: "uri" }, minItems: 1 },
      section: { enum: ["world", "business", "science"], default: "world" },
    },
    required: ["headline", "body", "sources"],
  };

  const editor = codeAgent({
    id: "a1",
    name: "editor",
    label: "editor",
    description: "Runs the desk: assigns stories and decides what is published",
    location: at("desk.py", 24),
    model: { id: "openai:gpt-5.2", name: "gpt-5.2", provider: "openai", offline: false },
    deps_type: "newsroom.desk.Desk",
    instructions: [
      literal(
        "You run a small news desk. Assign each story to the reporter, have the facts " +
          "checked, and publish only what the fact checker has passed.",
        "desk.py",
        27,
      ),
      {
        kind: "instructions",
        dynamic: true,
        text: "Today is {today:%A}. Follow the {STYLE.name} style guide.",
        function: null,
        expression: null,
        location: at("desk.py", 44),
      },
      {
        kind: "system_prompt",
        dynamic: true,
        text: null,
        function: "newsroom.desk.deadline_reminder",
        expression: null,
        location: at("desk.py", 49),
      },
    ],
    toolsets: [agentTools],
    tools: [
      fromCode(
        "assign_story",
        "ts0",
        "Ask the reporter to research and draft a story.",
        objectSchema({ topic: { type: "string", description: "What the story is about." } }),
        "desk.py",
        55,
        { takes_ctx: true },
      ),
      fromCode(
        "check_facts",
        "ts0",
        "Have the fact checker go through a draft.",
        objectSchema({ draft: { type: "string" } }),
        "desk.py",
        68,
        { takes_ctx: true },
      ),
      fromCode(
        "publish",
        "ts0",
        "Publish a story that has passed its checks.",
        objectSchema(
          { story_id: { type: "string" }, embargo: { type: "string", format: "date-time" } },
          ["story_id"],
        ),
        "desk.py",
        81,
        { is_async: false, max_retries: 0, return_schema: { type: "boolean" } },
      ),
    ],
    output: {
      mode: "tool",
      allows_text: false,
      type_name: "Story",
      json_schema: story,
      tool_names: ["final_result"],
      location: at("desk.py", 12),
    },
    settings: {
      model_settings: { max_tokens: 2000 },
      tool_retries: 2,
      expressions: {
        output_retries: "MAX_RETRIES",
        "model_settings.temperature": "DESK.temperature",
      },
    },
    delegates: [
      { tool_name: "assign_story", agent_id: "a2", evidence: "code" },
      { tool_name: "check_facts", agent_id: "a4", evidence: "code" },
    ],
    warnings: [
      computedBy("output_retries", "MAX_RETRIES"),
      computedBy("the model setting temperature", "DESK.temperature"),
    ],
  });

  const archive = 'MCPServerStdio("uvx", args=["mcp-archive"])';
  const reporter = codeAgent({
    id: "a2",
    name: "reporter",
    label: "reporter",
    description: "Researches a topic and drafts the story",
    location: at("reporters.py", 31),
    model: {
      id: null,
      name: "get_model()",
      provider: null,
      offline: false,
      expression: "get_model()",
      model_class: "OpenAIChatModel",
      env: { variable: "NEWSROOM_MODEL", default: "gpt-5.2-mini" },
    },
    instructions: [
      {
        kind: "instructions",
        dynamic: true,
        text: null,
        function: null,
        expression: 'PROMPTS["reporter"]',
        location: at("reporters.py", 33),
      },
    ],
    toolsets: [
      agentTools,
      {
        id: "ts1",
        toolset_id: "wires",
        label: "FunctionToolset 'wires'",
        kind: "function",
        wrappers: [],
        opaque: false,
        location: at("reporters.py", 12),
      },
      {
        id: "ts2",
        toolset_id: null,
        label: "MCPServerStdio",
        kind: "mcp",
        wrappers: ["PrefixedToolset"],
        opaque: true,
        expression: archive,
        location: at("reporters.py", 22),
      },
    ],
    tools: [
      fromCode(
        "ask_stringer",
        "ts0",
        "Ask the stringer on the ground for a quote.",
        objectSchema({ question: { type: "string" } }),
        "reporters.py",
        40,
        { takes_ctx: true },
      ),
      fromCode(
        "search_wires",
        "ts1",
        "Search the wire services.",
        objectSchema(
          {
            query: { type: "string" },
            since: { type: "string", format: "date", description: "Earliest date to include." },
            limit: { type: "integer", default: 5, minimum: 1, maximum: 20 },
          },
          ["query"],
        ),
        "reporters.py",
        14,
        { is_async: false },
      ),
      fromCode(
        "fetch_article",
        "ts1",
        "Fetch the full text of a wire article.",
        objectSchema({ url: { type: "string", format: "uri" } }),
        "reporters.py",
        18,
      ),
    ],
    delegates: [{ tool_name: "ask_stringer", agent_id: "a3", evidence: "code" }],
    warnings: [
      "the model name of `OpenAIChatModel` comes from the environment variable `NEWSROOM_MODEL` " +
        "when the code runs; the source's default is `gpt-5.2-mini`",
      computedBy("an instruction", 'PROMPTS["reporter"]'),
      `the tools of \`${archive}\` are listed by the server, so they are only known when the code runs`,
    ],
  });

  const stringer = codeAgent({
    id: "a3",
    // No `name=`: the label is the variable the agent is assigned to.
    name: null,
    label: "stringer",
    location: at("reporters.py", 58),
    model: {
      id: null,
      name: "claude-sonnet-4-5",
      provider: null,
      offline: false,
      expression: 'AnthropicModel("claude-sonnet-4-5", provider=provider)',
      model_class: "AnthropicModel",
    },
    instructions: [literal("Answer in one short quote, in the first person.", "reporters.py", 60)],
    toolsets: [agentTools],
    tools: [
      fromCode(
        "duckduckgo_search",
        "ts0",
        "Searches DuckDuckGo for the given query and returns the results.",
        {
          additionalProperties: false,
          properties: { query: { description: "The query to search for.", type: "string" } },
          required: ["query"],
          type: "object",
        },
        "reporters.py",
        58,
        {
          return_schema: {
            $defs: {
              DuckDuckGoResult: {
                description: "A DuckDuckGo search result.",
                properties: {
                  title: { type: "string" },
                  href: { type: "string" },
                  body: { type: "string" },
                },
                required: ["title", "href", "body"],
                title: "DuckDuckGoResult",
                type: "object",
              },
            },
            items: { $ref: "#/$defs/DuckDuckGoResult" },
            type: "array",
          },
          function: "pydantic_ai.common_tools.duckduckgo.DuckDuckGoSearchTool.__call__",
          library_version: "2.70.0",
        },
      ),
    ],
  });

  const checker = codeAgent({
    id: "a4",
    name: "fact_checker",
    label: "fact_checker",
    description: "Checks every claim in a draft against its sources",
    location: at("checks.py", 19),
    model: { id: "openai:gpt-5.2-mini", name: "gpt-5.2-mini", provider: "openai", offline: false },
    instructions: [
      literal(
        "Check each claim against the sources. Never guess.",
        "checks.py",
        21,
        "system_prompt",
      ),
    ],
    toolsets: [agentTools],
    tools: [
      fromCode(
        "lookup_source",
        "ts0",
        "Open one of the sources a claim cites.",
        objectSchema({ claim: { "x-python-type": "Claim[Source]" }, index: { type: "integer" } }),
        "checks.py",
        27,
      ),
      // Imported from a package that was not among the analysed files: only its name is known.
      tool("archive_search", "ts0", null, null, {
        origin: "code",
        is_async: null,
        takes_ctx: null,
        function: "newsdesk_tools.archive_search",
      }),
    ],
    output: {
      mode: "native",
      allows_text: false,
      type_name: "Verdict[Claim]",
      json_schema: { "x-python-type": "Verdict[Claim]" },
      tool_names: [],
      location: at("checks.py", 8),
    },
    warnings: [
      "the output type `Verdict[Claim]` is generic, so its schema is only known when the code runs",
    ],
  });

  const node = (id, kind, label, line, note = null) => ({
    id,
    kind,
    label,
    note,
    location: line === null ? null : at("pipeline.py", line),
  });
  const publishing = {
    id: "g1",
    name: "publishing",
    label: "publishing",
    source: "code",
    location: at("pipeline.py", 44),
    state_type: "newsroom.pipeline.PublishState",
    input_type: "str",
    output_type: "Story",
    nodes: [
      node("__start__", "start", null, null),
      node("draft", "step", "Draft", 16),
      node("review", "step", "Review", 24),
      node("decision", "decision", null, 47, "approved?"),
      node("publish", "step", "Publish", 33),
      node("__end__", "end", null, null),
    ],
    edges: [
      { source: "__start__", target: "draft", label: null },
      { source: "draft", target: "review", label: null },
      { source: "review", target: "decision", label: null },
      { source: "decision", target: "draft", label: "revise" },
      { source: "decision", target: "publish", label: "approve" },
      { source: "publish", target: "__end__", label: null },
    ],
    mermaid:
      "---\ntitle: publishing\n---\nstateDiagram-v2\n  direction LR\n  [*] --> draft\n" +
      "  draft --> review\n  state decision <<choice>>\n  review --> decision\n" +
      "  decision --> draft: revise\n  decision --> publish: approve\n  publish --> [*]",
    warnings: [],
  };

  rec.emit(0, { type: "blueprint.agent", agent: editor });
  rec.emit(0, { type: "blueprint.agent", agent: reporter });
  rec.emit(0, { type: "blueprint.agent", agent: stringer });
  rec.emit(0, { type: "blueprint.agent", agent: checker });
  rec.emit(0, { type: "blueprint.graph", graph: publishing });
  return rec;
}

// ---------- blueprint/capabilities.pviz.jsonl ----------

/**
 * One agent read from source whose capabilities span eight docs categories: a configured one,
 * one that is only loaded on demand, ones that give the agent toolsets (a drawn one with a tool,
 * and harness ones whose tools are only known at run time), one that gives provider-side tools
 * only, and the built-in one that stays folded away.
 */
function capabilities() {
  const rec = new Recording({
    title: "briefing",
    seed: 0x5eed0005,
    t0: 1795046400000,
    stream: false,
  });
  rec.header.mode = "blueprint";

  const at = (line) => ({ file: "briefing.py", line });
  const capability = (name, category, fields = {}) => ({
    name,
    builtin: false,
    category,
    package: "pydantic_ai",
    deferred: false,
    details: {},
    expressions: {},
    toolsets: [],
    native_tools: [],
    ...fields,
  });
  const harness = { package: "pydantic_ai_harness" };
  const toolset = (id, toolsetId, label, line, fields = {}) => ({
    id,
    toolset_id: toolsetId,
    label,
    kind: "function",
    wrappers: ["CapabilityOwnedToolset"],
    opaque: false,
    expression: null,
    location: at(line),
    ...fields,
  });
  const fromCode = (name, toolsetId, description, parameters, line, returns) =>
    tool(name, toolsetId, description, parameters, {
      origin: "code",
      function: `briefing.${name}`,
      location: at(line),
      return_schema: returns,
    });
  const unknownTools = (what) =>
    `the tools of \`${what}\` depend on how it is set up, so they are only known when the code runs`;

  const briefing = codeAgent({
    id: "a1",
    name: "briefing",
    label: "briefing",
    description: "Writes a short brief on a question, from the archive and the web",
    location: at(31),
    model: { id: "openai:gpt-5.2", name: "gpt-5.2", provider: "openai", offline: false },
    instructions: [
      {
        kind: "instructions",
        dynamic: false,
        text: "Answer the question in a brief of five lines. Cite the pages you read.",
        function: null,
        expression: null,
        location: at(34),
      },
    ],
    toolsets: [
      agentTools,
      toolset("ts1", "web_search", "FunctionToolset 'web_search'", 12, {
        wrappers: ["CapabilityOwnedToolset", "PreparedToolset"],
      }),
      toolset("ts2", "web_fetch", "FunctionToolset 'web_fetch'", 20, {
        wrappers: ["CapabilityOwnedToolset", "PreparedToolset"],
      }),
      toolset("ts3", "memory", "MemoryToolset 'memory'", 38, {
        opaque: true,
        expression: "Memory(store=InMemoryMemoryStore())",
      }),
      toolset("ts4", "planning", "PlanningToolset 'planning'", 39, {
        opaque: true,
        expression: "Planning()",
      }),
    ],
    tools: [
      fromCode(
        "search_archive",
        "ts1",
        "Search the archive of past briefs.",
        objectSchema({ query: { type: "string" } }),
        12,
        { type: "array", items: { type: "string" } },
      ),
      fromCode(
        "fetch_page",
        "ts2",
        "Fetch the text of a page.",
        objectSchema({ url: { type: "string", format: "uri" } }),
        20,
        { type: "string" },
      ),
    ],
    native_tools: ["web_search", "mcp_server"],
    capabilities: [
      ...BUILTIN_CAPABILITIES,
      capability("Thinking", "reasoning", { details: { effort: "high" } }),
      capability("WebSearch", "web", {
        toolsets: ["ts1"],
        native_tools: ["web_search"],
        expressions: { local: "search_archive" },
      }),
      capability("WebFetch", "web", {
        toolsets: ["ts2"],
        expressions: { local: "fetch_page" },
        details: { native: false },
      }),
      capability("MCP", "tools", {
        native_tools: ["mcp_server"],
        details: { url: "https://docs.example.com/mcp", native: true, local: false },
      }),
      capability("Skills", "knowledge", {
        ...harness,
        deferred: true,
        details: { directories: ["skills"] },
      }),
      capability("Memory", "knowledge", { ...harness, toolsets: ["ts3"] }),
      capability("Planning", "reasoning", { ...harness, toolsets: ["ts4"] }),
      capability("InputGuardrail", "safety", { ...harness, expressions: { guard: "check_topic" } }),
      capability("ModelFallback", "loop", { expressions: { models: "[backup_model]" } }),
    ],
    warnings: [unknownTools("Memory(store=InMemoryMemoryStore())"), unknownTools("Planning()")],
  });

  rec.emit(0, { type: "blueprint.agent", agent: briefing });
  return rec;
}

// ---------- write ----------

const files = {
  "sample.pviz.jsonl": sample().jsonl(),
  "failed.pviz.jsonl": failed().jsonl(),
  "redacted.pviz.jsonl": redacted().jsonl(),
  "index.json": `${JSON.stringify(
    {
      recordings: [
        {
          id: "sample",
          title: "Support triage and a graph pipeline",
          description:
            "A triage agent runs three tools in parallel, retries one, delegates a refund and returns structured output. Then a graph run loops through write and review.",
          src: "sample.pviz.jsonl",
        },
        {
          id: "failed",
          title: "A run that fails",
          description:
            "An unregistered agent, observed from its spans. One fetch times out and fails the run; the recording ends with a request still streaming.",
          src: "failed.pviz.jsonl",
        },
        {
          id: "redacted",
          title: "Content capture off",
          description:
            "The same structure with include_content=False: no prompts, arguments or results are stored.",
          src: "redacted.pviz.jsonl",
        },
      ],
    },
    null,
    2,
  )}\n`,
};

// Blueprint-only sessions are kept apart from the recordings: see `newsroom`.
files["blueprint/capabilities.pviz.jsonl"] = capabilities().jsonl();
files["blueprint/newsroom.pviz.jsonl"] = newsroom().jsonl();

for (const [name, text] of Object.entries(files)) {
  mkdirSync(path.dirname(path.join(fixturesDir, name)), { recursive: true });
  writeFileSync(path.join(fixturesDir, name), text, "utf8");
  console.log(`gen-fixtures: ${name} (${text.split("\n").length - 1} lines, ${text.length} bytes)`);
}
