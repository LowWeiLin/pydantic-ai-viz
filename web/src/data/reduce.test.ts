import { describe, expect, test } from "vitest";
import {
  agentBlueprint,
  agentRun,
  asStarted,
  capability,
  delta,
  ended,
  graphBlueprint,
  log,
  modelRequest,
  sessionStarted,
  sid,
  started,
  stateOf,
  TRACE,
  toolCall,
} from "../test/factory";
import type { AgentBlueprint, Capability, Envelope, Span } from "../wire";
import { initialData, reduce, reduceAll } from "./reduce";

describe("reduce", () => {
  test("session.started records the session and never mutates its input", () => {
    const [first] = log();
    const next = reduce(initialData, first as Envelope);
    expect(next.session).toMatchObject({ title: "Test session", pviz: 1, started_ts: 0 });
    expect(next.lastSeq).toBe(0);
    expect(initialData.session).toBeNull();
    expect(initialData.eventCount).toBe(0);
  });

  test("blueprints are upserted by id and keep first-seen order", () => {
    const unnamed = agentBlueprint({ id: "a1", name: null, label: "agent-1" });
    const state = stateOf(
      [1, { type: "blueprint.agent", agent: unnamed }],
      [2, { type: "blueprint.agent", agent: agentBlueprint({ id: "a2" }) }],
      [3, { type: "blueprint.graph", graph: graphBlueprint("g1", ["write"]) }],
      [4, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      [5, { type: "blueprint.graph", graph: graphBlueprint("g1", ["write", "review"]) }],
    );
    expect(state.agentOrder).toEqual(["a1", "a2"]);
    expect(state.agents.a1?.name).toBe("triage");
    expect(state.graphOrder).toEqual(["g1"]);
    expect(state.graphs.g1?.nodes).toHaveLength(2);
  });

  test("capabilities from a recording made before they were grouped load as custom", () => {
    const old = agentBlueprint({ id: "a1" });
    old.capabilities = [{ name: "Hooks", builtin: false }] as unknown as Capability[];
    const current = capability({
      name: "Thinking",
      category: "reasoning",
      details: { effort: "high" },
    });
    const state = stateOf(
      [1, { type: "blueprint.agent", agent: old }],
      [
        2,
        { type: "blueprint.agent", agent: agentBlueprint({ id: "a2", capabilities: [current] }) },
      ],
    );
    expect(state.agents.a1?.capabilities).toEqual([capability({ name: "Hooks" })]);
    expect(state.agents.a1?.capabilities[0]).toMatchObject({
      category: "custom",
      details: {},
      toolsets: [],
    });
    expect(state.agents.a2?.capabilities).toEqual([current]);
  });

  test("a recording made before skills, hooks, stacks, default settings and output validators were read has none", () => {
    const { settings, ...rest } = agentBlueprint({ id: "a1" });
    const { defaults: _defaults, ...olderSettings } = settings;
    const {
      capability_id: _id,
      stack: _stack,
      skills: _skills,
      hooks: _hooks,
      ...olderCapability
    } = capability({
      name: "Skills",
      package: "pydantic_ai_harness",
    });
    const { validators: _validators, ...olderOutput } = rest.output;
    const { wrapper: _wrapper, ...older } = rest;
    const old = {
      ...older,
      output: olderOutput,
      settings: olderSettings,
      capabilities: [olderCapability],
    };
    const state = stateOf([1, { type: "blueprint.agent", agent: old as AgentBlueprint }]);
    expect(state.agents.a1?.capabilities[0]).toMatchObject({
      capability_id: null,
      stack: null,
      skills: [],
      hooks: [],
    });
    expect(state.agents.a1?.settings.defaults).toEqual([]);
    expect(state.agents.a1?.output.validators).toEqual([]);
    expect(state.agents.a1?.wrapper).toBeNull();
    // What a newer recording says is kept.
    const newer = agentBlueprint({ id: "a2" });
    newer.settings.defaults = ["end_strategy"];
    const kept = stateOf([1, { type: "blueprint.agent", agent: newer }]);
    expect(kept.agents.a2?.settings.defaults).toEqual(["end_strategy"]);
  });

  test("span.ended replaces span.started by span_id", () => {
    const root = agentRun({ id: "root", start: 10, end: 90 });
    const running = stateOf(started(root));
    expect(running.spans[sid("root")]).toMatchObject({ status: "running", end_ms: null });

    const done = stateOf(started(root), ended(root));
    expect(done.spans[sid("root")]).toEqual(root);
    expect(done.traces[TRACE]).toEqual([sid("root")]);
    expect(done.traceOrder).toEqual([TRACE]);
    // Where the trace entered the log: its first span event, not a later one.
    expect(done.traceSeq).toEqual({ [TRACE]: 1 });
  });

  test("span.ended without span.started is accepted", () => {
    const req = modelRequest({ id: "req", parent: "gone", start: 10, end: 20 });
    const state = stateOf(ended(req));
    expect(state.spans[sid("req")]?.status).toBe("ok");
    expect(state.traces[TRACE]).toEqual([sid("req")]);
  });

  test("a late span.started does not reopen a span that has ended", () => {
    const req = modelRequest({ id: "req", start: 10, end: 20 });
    const state = stateOf(ended(req), [21, { type: "span.started", span: asStarted(req) }]);
    expect(state.spans[sid("req")]?.end_ms).toBe(20);
  });

  test("deltas are appended per span with their envelope time", () => {
    const req = modelRequest({ id: "req", start: 10 });
    const state = stateOf(
      started(req),
      [11, delta("req", 0, "Hel")],
      [12, delta("req", 0, "lo")],
      [13, delta("req", 1, "{", "tool_call", "get_order")],
    );
    expect(state.deltas[sid("req")]).toEqual([
      { seq: 2, ts: 11, part_index: 0, part_kind: "text", tool_name: null, delta: "Hel" },
      { seq: 3, ts: 12, part_index: 0, part_kind: "text", tool_name: null, delta: "lo" },
      { seq: 4, ts: 13, part_index: 1, part_kind: "tool_call", tool_name: "get_order", delta: "{" },
    ]);
  });

  test("a new session_id discards everything", () => {
    const first = stateOf(
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1" }) }],
      started(agentRun({ id: "root", start: 2 })),
    );
    const other = "f".repeat(32);
    const next = reduce(first, { seq: 0, ts: 500, ...sessionStarted(other) });
    expect(next.session?.session_id).toBe(other);
    expect(next.agentOrder).toEqual([]);
    expect(next.spans).toEqual({});
    expect(next.traceOrder).toEqual([]);
    expect(next.lastSeq).toBe(0);
    expect(next.eventCount).toBe(1);
  });

  test("a replay of the same session after a reconnect changes nothing", () => {
    const events = log(
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1" }) }],
      started(agentRun({ id: "root", start: 2 })),
    );
    const state = reduceAll(initialData, events);
    expect(reduceAll(state, events)).toBe(state);
  });

  test("unknown event types and malformed envelopes are ignored", () => {
    const state = stateOf(started(agentRun({ id: "root", start: 2 })));
    const future = { seq: 2, ts: 3, type: "span.linked", link: {} } as unknown as Envelope;
    const next = reduce(state, future);
    expect(next.spans).toBe(state.spans);
    expect(next.lastSeq).toBe(2);

    const junk = [
      null,
      7,
      { type: "span.started" },
      { seq: 9, ts: 9, type: "span.started", span: null },
    ];
    const after = reduceAll(next, junk as unknown as Envelope[]);
    expect(after.spans).toBe(state.spans);
  });

  test("spans from a recording made before deferred tool calls were read get none", () => {
    const run = agentRun({ id: "root", start: 0, end: 9 });
    const tool = toolCall({ id: "t1", parent: "root", start: 1, end: 2, tool: "lookup" });
    const { deferred: _deferred, ...oldRun } = run.data;
    const { deferral: _deferral, ...oldTool } = tool.data;
    const state = stateOf(
      [2, { type: "span.ended", span: { ...tool, data: oldTool } as unknown as Span }],
      [9, { type: "span.ended", span: { ...run, data: oldRun } as unknown as Span }],
    );
    const root = state.spans[sid("root")];
    const call = state.spans[sid("t1")];
    expect(root?.kind === "agent_run" && root.data.deferred).toEqual([]);
    expect(call?.kind === "tool_call" && call.data.deferral).toBeNull();
  });

  test("spans from a recording made before events were recorded get none", () => {
    const { events: _events, ...old } = toolCall({ id: "t1", start: 1, end: 2, tool: "lookup" });
    const state = stateOf([2, { type: "span.ended", span: old as unknown as Span }]);
    expect(state.spans[sid("t1")]?.events).toEqual([]);
  });

  test("reduce one at a time equals reduceAll", () => {
    const events = log(
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1" }) }],
      started(agentRun({ id: "root", start: 2 })),
      started(modelRequest({ id: "req", parent: "root", start: 3 })),
      [4, delta("req", 0, "hi")],
      ended(modelRequest({ id: "req", parent: "root", start: 3, end: 5 })),
      ended(agentRun({ id: "root", start: 2, end: 6 })),
    );
    const stepwise = events.reduce(reduce, initialData);
    expect(stepwise).toEqual(reduceAll(initialData, events));
    expect(stepwise.lastTs).toBe(6);
    expect(stepwise.eventCount).toBe(events.length);
  });
});
