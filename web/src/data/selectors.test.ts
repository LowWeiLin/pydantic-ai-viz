// @vitest-environment node
import { describe, expect, test } from "vitest";
import {
  agentBlueprint,
  agentRun,
  delta,
  fixtureState,
  graphBlueprint,
  lifecycle,
  modelRequest,
  sid,
  stateOf,
  TRACE,
  toolCall,
} from "../test/factory";
import type { Event, Json, RunEvent, Span } from "../wire";
import {
  assembleStream,
  BLUEPRINT_FILTER_FROM,
  blueprintForAgentName,
  blueprintIndex,
  canvasModel,
  conversationSoFar,
  deferredOutcomes,
  displayStatus,
  fileName,
  graphCanvasModel,
  loadedId,
  projectSpan,
  runBoundaries,
  runBounds,
  runSubjects,
  runs,
  runTotals,
  runView,
} from "./selectors";

const LIVE = Number.POSITIVE_INFINITY;

/** root(0-100) > req1(10-30), t1(35-50 retry), t2(35-60), req2(65-90). */
function simpleRun() {
  const root = agentRun({
    id: "root",
    start: 0,
    end: 100,
    usage: { input_tokens: 30, output_tokens: 12 },
  });
  const req1 = modelRequest({
    id: "req1",
    parent: "root",
    start: 10,
    end: 30,
    usage: { input_tokens: 10, output_tokens: 4 },
  });
  const t1 = toolCall({
    id: "t1",
    parent: "root",
    start: 35,
    end: 50,
    tool: "get_order",
    status: "error",
    retry: true,
    error: { type: "ToolRetryError", message: "bad id", stacktrace: null },
  });
  const t2 = toolCall({ id: "t2", parent: "root", start: 35, end: 60, tool: "search_kb" });
  const req2 = modelRequest({
    id: "req2",
    parent: "root",
    start: 65,
    end: 90,
    usage: { input_tokens: 20, output_tokens: 8 },
  });
  return { root, req1, t1, t2, req2, all: [root, req1, t1, t2, req2] as Span[] };
}

describe("displayStatus", () => {
  test("a failed tool call with retry info is a retry; without it, an error", () => {
    const retried = toolCall({
      id: "a",
      start: 0,
      end: 1,
      tool: "x",
      status: "error",
      retry: true,
    });
    const failed = toolCall({ id: "b", start: 0, end: 1, tool: "x", status: "error" });
    expect(displayStatus(retried)).toBe("retry");
    expect(displayStatus(failed)).toBe("error");
    expect(displayStatus(toolCall({ id: "c", start: 0, end: 1, tool: "x" }))).toBe("ok");
    expect(displayStatus(toolCall({ id: "d", start: 0, tool: "x" }))).toBe("running");
    expect(displayStatus(agentRun({ id: "e", start: 0, end: 1, status: "error" }))).toBe("error");
  });
});

describe("projectSpan", () => {
  const { req1, t1, root } = simpleRun();

  test("a span is hidden before it starts", () => {
    expect(projectSpan(req1, 9.9)).toBeNull();
    expect(projectSpan(req1, 10)).not.toBeNull();
  });

  test("before its end a span is running and end-only fields are hidden", () => {
    expect(projectSpan(req1, 29.9)).toEqual({
      ...req1,
      end_ms: null,
      status: "running",
      error: null,
      attributes: {},
      data: {
        model_name: "m",
        provider: null,
        response_model: null,
        settings: {},
        instructions: null,
        input_messages: null,
        output_messages: null,
        usage: null,
      },
    });
    const tool = projectSpan(t1, 40);
    expect(tool).toMatchObject({ status: "running", error: null });
    expect(tool?.kind === "tool_call" && tool.data).toEqual({
      tool_name: "get_order",
      call_id: `call_${sid("t1")}`,
      arguments: { q: 1 },
      result: null,
      retry: null,
      deferral: null,
    });
    const agent = projectSpan(root, 50);
    expect(agent?.kind === "agent_run" && agent.data).toMatchObject({
      model_name: "m",
      output: null,
      usage: null,
      messages: null,
    });
  });

  test("at and after its end a span is returned as is", () => {
    expect(projectSpan(req1, 30)).toBe(req1);
    expect(projectSpan(t1, LIVE)).toBe(t1);
  });
});

describe("assembleStream", () => {
  const state = stateOf(
    ...lifecycle(modelRequest({ id: "req", start: 10 })),
    [11, delta("req", 0, "Let me ")],
    [12, delta("req", 1, '{"order_id": ', "tool_call", "get_order")],
    [13, delta("req", 0, "check.")],
    [14, delta("req", 1, '"1042"}', "tool_call")],
  );
  const deltas = state.deltas[sid("req")] ?? [];

  test("concatenates by part_index, in part order", () => {
    expect(assembleStream(deltas, LIVE)).toEqual([
      { index: 0, kind: "text", tool_name: null, text: "Let me check." },
      { index: 1, kind: "tool_call", tool_name: "get_order", text: '{"order_id": "1042"}' },
    ]);
  });

  test("only deltas at or before t are visible", () => {
    expect(assembleStream(deltas, 12)).toEqual([
      { index: 0, kind: "text", tool_name: null, text: "Let me " },
      { index: 1, kind: "tool_call", tool_name: "get_order", text: '{"order_id": ' },
    ]);
    expect(assembleStream(deltas, 10)).toEqual([]);
  });
});

describe("runView", () => {
  const { all } = simpleRun();
  const state = stateOf(...lifecycle(...all), [20, delta("req1", 0, "thinking...")]);

  test("orders spans depth first with parallel siblings in call order", () => {
    const view = runView(state, TRACE, LIVE);
    expect(view?.order).toEqual(["root", "req1", "t1", "t2", "req2"].map(sid));
    expect(view?.depth[sid("root")]).toBe(0);
    expect(view?.depth[sid("t2")]).toBe(1);
    expect(view?.children[sid("root")]).toHaveLength(4);
    expect(view).toMatchObject({ rootId: sid("root"), status: "ok", startMs: 0, endMs: 100 });
    expect(view?.streams).toEqual({});
  });

  test("scrubbing back hides later spans and reopens earlier ones", () => {
    const view = runView(state, TRACE, 40);
    expect(view?.order).toEqual(["root", "req1", "t1", "t2"].map(sid));
    expect(view?.status).toBe("running");
    expect(view?.endMs).toBeNull();
    expect(view?.spans[sid("req1")]?.status).toBe("ok");
    expect(view?.spans[sid("t1")]?.status).toBe("running");
  });

  test("a running model request shows its stream; an ended one does not", () => {
    expect(runView(state, TRACE, 25)?.streams[sid("req1")]).toEqual([
      { index: 0, kind: "text", tool_name: null, text: "thinking..." },
    ]);
    expect(runView(state, TRACE, 30)?.streams).toEqual({});
  });

  test("before the run starts the view is empty; an unknown trace is null", () => {
    const view = runView(state, TRACE, -1);
    expect(view).toMatchObject({ rootId: null, order: [], status: "running" });
    expect(runView(state, "nope", LIVE)).toBeNull();
  });

  test("a span whose parent was never seen becomes a top-level row", () => {
    const orphan = modelRequest({ id: "req", parent: "gone", start: 5, end: 9 });
    const view = runView(stateOf(...lifecycle(orphan)), TRACE, LIVE);
    expect(view).toMatchObject({ rootId: sid("req"), order: [sid("req")], status: "ok" });
  });
});

describe("conversationSoFar", () => {
  const user = { role: "user" as const, parts: [{ type: "text" as const, content: "hi" }] };
  const first = modelRequest({ id: "m1", parent: "root", start: 110, end: 200, text: "one" });
  first.data.input_messages = [user];
  const second = modelRequest({ id: "m2", parent: "root", start: 300, end: 400, text: "two" });
  second.data.input_messages = [user, ...(first.data.output_messages ?? [])];
  const events = [
    ...lifecycle(agentRun({ id: "root", start: 100, end: 500 }), first, second),
    [320, delta("m2", 0, "tw")] as [number, Event],
  ];
  const state = stateOf(...events.sort((a, b) => a[0] - b[0]));
  const at = (t: number) => {
    const view = runView(state, TRACE, t);
    if (!view) throw new Error("expected a view");
    return conversationSoFar(view, sid("root"));
  };
  const texts = (messages: readonly { parts: readonly unknown[] }[] | null) =>
    messages?.flatMap((m) => m.parts.map((p) => (p as { content: string }).content));

  test("before any request has answered there is nothing to show", () => {
    expect(at(105)).toEqual({ messages: null, stream: null });
    expect(at(150)).toEqual({ messages: null, stream: null });
  });

  test("a finished request gives the history up to its response", () => {
    expect(texts(at(250).messages)).toEqual(["hi", "one"]);
    expect(at(250).stream).toBeNull();
  });

  test("a request in flight adds its stream after the history so far", () => {
    const mid = at(350);
    expect(texts(mid.messages)).toEqual(["hi", "one"]);
    expect(mid.stream).toEqual([{ index: 0, kind: "text", tool_name: null, text: "tw" }]);
  });

  test("the latest finished request wins and ends the stream", () => {
    expect(texts(at(450).messages)).toEqual(["hi", "one", "two"]);
    expect(at(450).stream).toBeNull();
  });

  test("a span with no requests has nothing to say", () => {
    const view = runView(state, TRACE, 450);
    expect(view && conversationSoFar(view, sid("m1"))).toEqual({ messages: null, stream: null });
  });
});

describe("runs and totals", () => {
  test("totals come from model requests only, never from agent runs", () => {
    // The sub-agent shares usage with its parent, so the parent's figure already includes it.
    const root = agentRun({
      id: "root",
      start: 0,
      end: 100,
      usage: { input_tokens: 35, output_tokens: 14 },
    });
    const req = modelRequest({
      id: "req",
      parent: "root",
      start: 5,
      end: 20,
      usage: { input_tokens: 10, output_tokens: 4 },
    });
    const tool = toolCall({ id: "tool", parent: "root", start: 25, end: 80, tool: "delegate" });
    const sub = agentRun({
      id: "sub",
      parent: "tool",
      agent: "refunds",
      start: 30,
      end: 75,
      usage: { input_tokens: 25, output_tokens: 10 },
    });
    const subReq = modelRequest({
      id: "subreq",
      parent: "sub",
      agent: "refunds",
      start: 35,
      end: 70,
      usage: { input_tokens: 25, output_tokens: 10 },
    });
    const state = stateOf(...lifecycle(root, req, tool, sub, subReq));

    expect(runTotals(state, TRACE, LIVE)).toEqual({
      usage: { input_tokens: 35, output_tokens: 14 },
      requests: 2,
      toolCalls: 1,
      retries: 0,
      errors: 0,
      durationMs: 100,
    });
    expect(runs(state)[0]?.usage).toEqual({ input_tokens: 35, output_tokens: 14 });
  });

  test("totals follow the projection time", () => {
    const { all } = simpleRun();
    const state = stateOf(...lifecycle(...all));
    expect(runTotals(state, TRACE, 55)).toEqual({
      usage: { input_tokens: 10, output_tokens: 4 },
      requests: 1,
      toolCalls: 2,
      retries: 1,
      errors: 0,
      durationMs: 55,
    });
    expect(runTotals(state, TRACE, LIVE).usage).toEqual({ input_tokens: 30, output_tokens: 12 });
    expect(runTotals(state, "nope", LIVE).requests).toBe(0);
  });

  test("a failure is counted once, at the innermost span", () => {
    const boom = { type: "ValueError", message: "boom", stacktrace: null };
    const root = agentRun({ id: "root", start: 0, end: 50, status: "error", error: boom });
    const tool = toolCall({
      id: "t",
      parent: "root",
      start: 5,
      end: 40,
      tool: "x",
      status: "error",
      error: boom,
    });
    const totals = runTotals(stateOf(...lifecycle(root, tool)), TRACE, LIVE);
    expect(totals).toMatchObject({ errors: 1, retries: 0 });
  });

  test("runs are listed newest first with status, duration and bounds", () => {
    const other = "b".repeat(32);
    const first = agentRun({ id: "r1", start: 0, end: 40 });
    const second = agentRun({ id: "r2", trace: other, start: 100 });
    const secondReq = modelRequest({ id: "q2", trace: other, parent: "r2", start: 110, end: 160 });
    const state = stateOf(...lifecycle(first, second, secondReq));

    const list = runs(state);
    expect(list.map((r) => r.traceId)).toEqual([other, TRACE]);
    expect(list[0]).toMatchObject({ status: "running", endMs: null, durationMs: 60, spanCount: 2 });
    expect(list[1]).toMatchObject({
      status: "ok",
      name: "triage",
      kind: "agent_run",
      durationMs: 40,
    });
    expect(runBounds(state, other)).toEqual({ start: 100, end: 160 });
    expect(runBounds(state, TRACE)).toEqual({ start: 0, end: 40 });
    expect(runBoundaries(state, other)).toEqual([100, 110, 160]);
    expect(runBounds(state, "nope")).toBeNull();
  });
});

describe("blueprints and canvas models", () => {
  const sample = fixtureState("sample.pviz.jsonl");
  const [graphRunSummary, triageRun] = runs(sample);
  if (!graphRunSummary || !triageRun) throw new Error("the sample fixture should hold two runs");

  test("blueprintForAgentName returns the first blueprint with that name", () => {
    expect(blueprintForAgentName(sample, "refunds")?.id).toBe("a2");
    expect(blueprintForAgentName(sample, "nobody")).toBeNull();
    expect(blueprintForAgentName(sample, null)).toBeNull();
  });

  test("without a run the canvas is the blueprint and its delegation closure", () => {
    const model = canvasModel(sample, "a1", null);
    expect(model?.agents.map((a) => [a.blueprint.id, a.depth])).toEqual([
      ["a1", 0],
      ["a2", 1],
    ]);
    const triage = model?.agents[0];
    expect(triage?.toolsets.map((g) => [g.toolset.id, g.tools.map((t) => t.tool.name)])).toEqual([
      ["ts0", ["get_order", "get_customer", "escalate_refund"]],
      ["ts1", ["search_kb"]],
    ]);
    expect(triage?.run.state).toBe("idle");
    expect(triage?.outputReady).toBe(false);
    expect(model?.delegations).toEqual([
      {
        fromAgentId: "a1",
        toolName: "escalate_refund",
        toAgentId: "a2",
        evidence: "observed",
        active: false,
      },
    ]);
    expect(canvasModel(sample, "missing", null)).toBeNull();
  });

  test("the finished run lights tools, the retry and the output", () => {
    const view = runView(sample, triageRun.traceId, LIVE);
    const model = canvasModel(sample, "a1", view);
    const triage = model?.agents[0];
    const tools = Object.fromEntries(
      triage?.toolsets.flatMap((g) => g.tools.map((t) => [t.tool.name, t.activity])) ?? [],
    );
    expect(tools.get_order).toMatchObject({ state: "ok", calls: 2, retries: 1, errors: 0 });
    expect(tools.search_kb).toMatchObject({ state: "ok", calls: 1 });
    expect(triage?.model).toMatchObject({ state: "ok", calls: 4 });
    expect(triage?.outputReady).toBe(true);
    expect(model?.agents[1]?.run).toMatchObject({ state: "ok", calls: 1 });
    expect(model?.agents[1]?.model.calls).toBe(2);
  });

  test("mid-run the retried tool is amber and the delegation edge is active", () => {
    const span = (name: string, nth = 0): Span => {
      const found = Object.values(sample.spans).filter(
        (s) => s.name === name && s.trace_id === triageRun.traceId,
      )[nth];
      if (!found) throw new Error(`no span ${name}`);
      return found;
    };
    const firstOrder = span("get_order");
    const afterRetry = runView(sample, triageRun.traceId, (firstOrder.end_ms ?? 0) + 1);
    const early = canvasModel(sample, "a1", afterRetry)?.agents[0];
    const order = early?.toolsets[0]?.tools.find((t) => t.tool.name === "get_order");
    expect(order?.activity).toMatchObject({ state: "retry", calls: 1, retries: 1 });
    expect(early?.outputReady).toBe(false);

    const refunds = span("refunds");
    const during = canvasModel(
      sample,
      "a1",
      runView(sample, triageRun.traceId, refunds.start_ms + 5),
    );
    expect(during?.delegations[0]?.active).toBe(true);
    expect(during?.agents[1]?.run.state).toBe("running");
    expect(during?.agents[0]?.model.state).toBe("ok");
  });

  test("a delegation counts as observed from the moment a run first shows it", () => {
    const view = runView(sample, triageRun.traceId, LIVE);
    const refunds = view?.order
      .flatMap((id) => view.spans[id] ?? [])
      .find((s) => s.name === "refunds");
    if (!refunds) throw new Error("expected the refunds run");
    const evidenceAt = (t: number | null) =>
      canvasModel(sample, "a1", t === null ? null : runView(sample, triageRun.traceId, t))
        ?.delegations[0]?.evidence;
    expect(evidenceAt(refunds.start_ms - 1)).toBe("pending");
    expect(evidenceAt(refunds.start_ms)).toBe("observed");
    expect(evidenceAt(LIVE)).toBe("observed");
    // The plain blueprint shows what the session knows in the end.
    expect(evidenceAt(null)).toBe("observed");

    // A later run starts with the edge already solid: an earlier run showed it.
    const later = structuredClone(sample);
    const root = later.spans[triageRun.rootSpanId];
    if (!root) throw new Error("expected the root span");
    const again = {
      ...root,
      span_id: sid("again"),
      trace_id: "c".repeat(32),
      start_ms: root.start_ms + 60_000,
      end_ms: null,
      status: "running" as const,
    };
    const state = {
      ...later,
      spans: { ...later.spans, [again.span_id]: again as Span },
      traces: { ...later.traces, [again.trace_id]: [again.span_id] },
      traceOrder: [...later.traceOrder, again.trace_id],
    };
    const second = canvasModel(state, "a1", runView(state, again.trace_id, again.start_ms + 1));
    expect(second?.delegations[0]?.evidence).toBe("observed");
  });

  test("calls to tools the blueprint does not list are kept, not dropped", () => {
    const root = agentRun({ id: "root", start: 0, end: 50 });
    const tool = toolCall({ id: "t", parent: "root", start: 5, end: 9, tool: "mcp_search" });
    const state = stateOf(
      [0, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      ...lifecycle(root, tool),
    );
    const model = canvasModel(state, "a1", runView(state, TRACE, LIVE));
    expect(model?.agents[0]?.unlisted).toEqual([
      {
        name: "mcp_search",
        activity: { state: "ok", calls: 1, retries: 0, errors: 0, spanIds: [sid("t")] },
      },
    ]);
  });

  test("load_capability calls are counted by the id they load, when their arguments are known", () => {
    const root = agentRun({ id: "root", start: 0, end: 50 });
    const load = (id: string, start: number, args: Json) => {
      const span = toolCall({ id, parent: "root", start, end: start + 2, tool: "load_capability" });
      return { ...span, data: { ...span.data, arguments: args } } as Span;
    };
    const state = stateOf(
      [0, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      ...lifecycle(
        root,
        load("l1", 5, { id: "house-style" }),
        load("l2", 10, { id: "house-style" }),
        load("l3", 15, { id: "memory" }),
        // Captured without content, or with arguments that name no id: nothing is linked.
        load("l4", 20, null),
        load("l5", 25, { name: "x" }),
      ),
    );
    const model = canvasModel(state, "a1", runView(state, TRACE, LIVE));
    const loads = model?.agents[0]?.loads ?? {};
    expect(Object.keys(loads)).toEqual(["house-style", "memory"]);
    expect(loads["house-style"]).toEqual({
      state: "ok",
      calls: 2,
      retries: 0,
      errors: 0,
      spanIds: [sid("l1"), sid("l2")],
    });
    expect(loadedId(state.spans[sid("l3")] as Span)).toBe("memory");
    expect(loadedId(state.spans[sid("l4")] as Span)).toBeNull();
    expect(loadedId(root)).toBeNull();
    // Before the run they are not there; without a run, neither.
    expect(canvasModel(state, "a1", runView(state, TRACE, 4))?.agents[0]?.loads).toEqual({});
    expect(canvasModel(state, "a1", null)?.agents[0]?.loads).toEqual({});
  });

  test("events are counted by the capability that emitted them, up to the view's time", () => {
    const event = (ts: number, capabilityId: string | null): RunEvent => ({
      kind: "probe.ping",
      event_kind: capabilityId === null ? "custom" : "capability",
      capability_id: capabilityId,
      tool_call_id: null,
      tool_name: null,
      ts_ms: ts,
      data: null,
    });
    const root = agentRun({
      id: "root",
      start: 0,
      end: 50,
      events: [event(5, "emitter"), event(30, "emitter"), event(31, null)],
    });
    const tool = toolCall({
      id: "t1",
      parent: "root",
      start: 10,
      end: 20,
      tool: "lookup",
      events: [event(12, "audit")],
    });
    const state = stateOf(
      [0, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      ...lifecycle(root, tool),
    );
    const at = (t: number) => canvasModel(state, "a1", runView(state, TRACE, t))?.agents[0]?.events;
    expect(at(LIVE)).toEqual({
      emitter: {
        state: "ok",
        calls: 2,
        retries: 0,
        errors: 0,
        spanIds: [sid("root"), sid("root")],
      },
      audit: { state: "ok", calls: 1, retries: 0, errors: 0, spanIds: [sid("t1")] },
    });
    // A span that has not ended at the replay position shows only the events before it.
    expect(projectSpan(root, 10)?.events.map((e) => e.ts_ms)).toEqual([5]);
    expect(Object.keys(at(25) ?? {})).toEqual(["emitter", "audit"]);
    expect(at(25)?.emitter?.calls).toBe(1);
    expect(canvasModel(state, "a1", null)?.agents[0]?.events).toEqual({});
  });

  test("delegation cycles terminate and depth is capped", () => {
    const chain = ["a1", "a2", "a3", "a4", "a5"].map((id, i, ids) =>
      agentBlueprint({
        id,
        delegates: [
          { tool_name: "next", agent_id: ids[(i + 1) % ids.length] ?? id, evidence: "object" },
        ],
      }),
    );
    const state = stateOf(
      ...chain.map((agent, i) => [i, { type: "blueprint.agent", agent }] as [number, Event]),
    );
    const model = canvasModel(state, "a1", null);
    expect(model?.agents.map((a) => a.blueprint.id)).toEqual(["a1", "a2", "a3", "a4"]);
    expect(model?.delegations.map((d) => d.toAgentId)).toEqual(["a2", "a3", "a4"]);
  });

  test("a graph run counts node visits and marks the active node", () => {
    const view = runView(sample, graphRunSummary.traceId, LIVE);
    const model = graphCanvasModel(sample, "g1", view);
    const visits = Object.fromEntries(model?.nodes.map((n) => [n.node.id, n.activity.calls]) ?? []);
    expect(visits).toEqual({
      __start__: 0,
      write: 2,
      review: 2,
      decision: 0,
      publish: 1,
      __end__: 1,
    });
    expect(model?.run.state).toBe("ok");
    expect(model?.unlisted).toEqual([]);

    const mid = graphCanvasModel(
      sample,
      "g1",
      runView(sample, graphRunSummary.traceId, graphRunSummary.startMs + 500),
    );
    const active = mid?.nodes.filter((n) => n.activity.state === "running").map((n) => n.node.id);
    expect(active).toEqual(["write"]);
    expect(graphCanvasModel(sample, "g9", view)).toBeNull();
    expect(
      graphCanvasModel(sample, "g1", null)?.nodes.every((n) => n.activity.state === "idle"),
    ).toBe(true);
  });

  test("a graph run marks the edges it took, through decisions and round the loop", () => {
    const view = runView(sample, graphRunSummary.traceId, LIVE);
    const model = graphCanvasModel(sample, "g1", view);
    const taken = Object.fromEntries(
      model?.edges.map((edge, i) => [`${edge.source}>${edge.target}`, model.edgeVisits[i] ?? 0]) ??
        [],
    );
    expect(taken).toEqual({
      "__start__>write": 1,
      "write>review": 2,
      "review>decision": 2,
      "decision>publish": 1,
      "decision>write": 1,
      "publish>__end__": 1,
    });
    // Nodes without a span of their own are passed on the way to one.
    const passes = Object.fromEntries(model?.nodes.map((n) => [n.node.id, n.passes]) ?? []);
    expect(passes).toEqual({
      __start__: 1,
      write: 0,
      review: 0,
      decision: 2,
      publish: 0,
      __end__: 0,
    });
    expect(model?.activeEdges).toEqual([]);

    // Half a second in, only the edge into the running node is active.
    const mid = graphCanvasModel(
      sample,
      "g1",
      runView(sample, graphRunSummary.traceId, graphRunSummary.startMs + 500),
    );
    expect(mid?.activeEdges.map((i) => mid.edges[i]?.target)).toEqual(["write"]);
    expect(mid?.edgeVisits.reduce((a, b) => a + b, 0)).toBe(1);

    // Without a run nothing is taken.
    const still = graphCanvasModel(sample, "g1", null);
    expect(still?.edgeVisits.every((n) => n === 0)).toBe(true);
    expect(still?.nodes.every((n) => n.passes === 0)).toBe(true);
  });

  test("a finished run reaches an end node that has no span of its own", () => {
    const graph = graphBlueprint("g1", ["__start__", "work", "__end__"]);
    for (const node of graph.nodes) {
      if (node.id === "__start__") node.kind = "start";
      if (node.id === "__end__") node.kind = "end";
    }
    const node = (id: string, start: number, end: number | null): Span => ({
      trace_id: TRACE,
      span_id: sid(id),
      parent_id: sid("run"),
      name: "work",
      start_ms: start,
      end_ms: end,
      status: end === null ? "running" : "ok",
      agent_name: null,
      run_id: null,
      error: null,
      attributes: {},
      events: [],
      kind: "graph_node",
      data: { graph_id: "g1", node_id: "work", inputs: null },
    });
    const run = (end: number | null): Span => ({
      ...node("run", 10, end),
      parent_id: null,
      name: "g1",
      kind: "graph_run",
      data: { graph_id: "g1", graph_name: "g1", inputs: null, output: null },
    });
    const done = stateOf(
      [1, { type: "blueprint.graph", graph }],
      ...lifecycle(run(40), node("n1", 12, 30)),
    );
    const model = graphCanvasModel(done, "g1", runView(done, TRACE, LIVE));
    expect(model?.edgeVisits).toEqual([1, 1]);
    expect(model?.nodes.map((n) => n.passes)).toEqual([1, 0, 1]);

    const going = stateOf(
      [1, { type: "blueprint.graph", graph }],
      ...lifecycle(run(null), node("n1", 12, null)),
    );
    const live = graphCanvasModel(going, "g1", runView(going, TRACE, LIVE));
    expect(live?.edgeVisits).toEqual([1, 0]);
    expect(live?.activeEdges).toEqual([0]);
  });

  test("run subjects list the blueprints involved, root first", () => {
    const triageView = runView(sample, triageRun.traceId, LIVE);
    const graphView = runView(sample, graphRunSummary.traceId, LIVE);
    if (!triageView || !graphView) throw new Error("views should exist");
    expect(runSubjects(sample, triageView)).toEqual([
      { kind: "agent", id: "a1", label: "triage" },
      { kind: "agent", id: "a2", label: "refunds" },
    ]);
    expect(runSubjects(sample, graphView).map((s) => s.id)).toEqual(["g1", "a3", "a4"]);
  });

  test("unknown graph ids on spans do not break a graph model", () => {
    const state = stateOf([
      0,
      { type: "blueprint.graph", graph: graphBlueprint("g1", ["a", "b"]) },
    ]);
    expect(graphCanvasModel(state, "g1", null)?.edges).toHaveLength(1);
  });
});

describe("fixtures through the selectors", () => {
  test("sample totals equal the sum over model requests", () => {
    const sample = fixtureState("sample.pviz.jsonl");
    const triage = runs(sample)[1];
    expect(triage).toMatchObject({ name: "triage", status: "ok", kind: "agent_run" });
    const totals = runTotals(sample, triage?.traceId ?? "", LIVE);
    expect(totals).toMatchObject({
      usage: { input_tokens: 3109, output_tokens: 253 },
      requests: 6,
      toolCalls: 6,
      retries: 1,
      errors: 0,
    });
  });

  test("failed fixture: an error run, a run without start events, and one still streaming", () => {
    const failed = fixtureState("failed.pviz.jsonl");
    const [streaming, errored, early] = runs(failed);
    expect(errored).toMatchObject({ status: "error", name: "digest" });
    expect(early).toMatchObject({ status: "ok", spanCount: 2 });
    expect(streaming).toMatchObject({ status: "running", endMs: null });

    const totals = runTotals(failed, errored?.traceId ?? "", LIVE);
    expect(totals).toMatchObject({ errors: 2, retries: 0, toolCalls: 2 });

    const view = runView(failed, streaming?.traceId ?? "", LIVE);
    const parts = Object.values(view?.streams ?? {})[0];
    expect(parts?.map((p) => p.kind)).toEqual(["thinking", "text"]);
    expect(parts?.[1]?.text).toBe("Fetching the pydantic-ai feed");
    expect(blueprintForAgentName(failed, "digest")?.source).toBe("observed");
  });
});

describe("deferred tool calls", () => {
  const T1 = "1".repeat(32);
  const T2 = "2".repeat(32);
  const T3 = "3".repeat(32);
  const waiting = [
    { call_id: "c1", tool_name: "delete_file", kind: "approval" as const },
    { call_id: "c2", tool_name: "delete_file", kind: "approval" as const },
    { call_id: "c3", tool_name: "restart", kind: "external" as const },
  ];
  const first = agentRun({ id: "r1", trace: T1, start: 0, end: 10, deferred: waiting });
  const answers = [
    {
      role: "tool" as const,
      parts: [
        { type: "tool_result" as const, id: "c1", name: "delete_file", result: "deleted" },
        { type: "tool_result" as const, id: "c2", name: "delete_file", result: null },
        { type: "tool_result" as const, id: "c3", name: "restart", result: null },
      ],
    },
  ];
  const resume = (input: Parameters<typeof modelRequest>[0]["input"]) => [
    agentRun({ id: "r2", trace: T2, start: 20, end: 40 }),
    toolCall({
      id: "t1",
      trace: T2,
      parent: "r2",
      start: 21,
      end: 22,
      tool: "delete_file",
      callId: "c1",
    }),
    modelRequest({ id: "q2", trace: T2, parent: "r2", start: 23, end: 30, input }),
  ];

  test("a run that ended waiting lists the calls; none has an outcome until a run resumes it", () => {
    const state = stateOf(...lifecycle(first));
    expect(runs(state)[0]).toMatchObject({ waiting, resumes: null });
    expect(deferredOutcomes(state, T1).map((e) => e.outcome)).toEqual([
      "waiting",
      "waiting",
      "waiting",
    ]);
  });

  test("the next run of the conversation resumes it: approved, denied, result supplied", () => {
    const state = stateOf(...lifecycle(first, ...resume(answers)));
    const [second] = runs(state);
    expect(second).toMatchObject({ traceId: T2, resumes: T1, waiting: [] });
    expect(deferredOutcomes(state, T1).map((e) => e.outcome)).toEqual([
      "approved",
      "denied",
      "result supplied",
    ]);
  });

  test("without the request's messages a call with no span is said not to have run", () => {
    const state = stateOf(...lifecycle(first, ...resume(null)));
    expect(deferredOutcomes(state, T1).map((e) => e.outcome)).toEqual([
      "approved",
      "not run",
      "not run",
    ]);
  });

  test("a call that defers again in the resuming run was not approved", () => {
    const again = toolCall({
      id: "t2",
      trace: T2,
      parent: "r2",
      start: 22,
      end: 23,
      tool: "restart",
      callId: "c3",
      deferral: "external",
    });
    const state = stateOf(...lifecycle(first, ...resume(null), again));
    expect(deferredOutcomes(state, T1).map((e) => e.outcome)).toEqual([
      "approved",
      "not run",
      "not run",
    ]);
  });

  test("only the run right after one that waited resumes it, and only in its conversation", () => {
    const other = agentRun({ id: "r3", trace: T3, start: 30, end: 35, conversation: "elsewhere" });
    const third = agentRun({ id: "r4", trace: "4".repeat(32), start: 50, end: 60 });
    const state = stateOf(...lifecycle(first, ...resume(answers), other, third));
    const byTrace = Object.fromEntries(runs(state).map((r) => [r.traceId, r.resumes]));
    expect(byTrace).toEqual({ [T1]: null, [T2]: T1, [T3]: null, ["4".repeat(32)]: null });
  });

  test("a tool span that deferred is waiting, and so is the canvas tool of a call with no span", () => {
    const deferredTool = toolCall({
      id: "t9",
      trace: T1,
      parent: "r1",
      start: 2,
      end: 3,
      tool: "restart",
      callId: "c3",
      deferral: "external",
    });
    expect(displayStatus(deferredTool)).toBe("waiting");
    const blueprint = agentBlueprint({
      id: "a1",
      name: "triage",
      tools: [
        {
          name: "delete_file",
          toolset: "ts0",
          description: null,
          parameters_schema: null,
          return_schema: null,
          kind: "unapproved",
          takes_ctx: null,
          is_async: null,
          max_retries: null,
          function: null,
          origin: "object",
          location: null,
          library_version: null,
        },
      ],
    });
    const state = stateOf(
      [0, { type: "blueprint.agent", agent: blueprint }],
      ...lifecycle(first, deferredTool),
    );
    const model = canvasModel(state, "a1", runView(state, T1, LIVE));
    const tools = model?.agents[0]?.toolsets.flatMap((group) => group.tools);
    expect(tools?.[0]?.activity).toMatchObject({ state: "waiting", calls: 2 });
    expect(model?.agents[0]?.unlisted[0]).toMatchObject({
      name: "restart",
      activity: { state: "waiting", calls: 1 },
    });
  });

  test("approvals fixture: run 1 waits on three calls, run 2 resumes it", () => {
    const state = fixtureState("approvals.pviz.jsonl");
    const [second, firstRun] = runs(state);
    expect(firstRun?.waiting.map((call) => call.kind)).toEqual([
      "external",
      "approval",
      "approval",
    ]);
    expect(second?.resumes).toBe(firstRun?.traceId);
    expect(deferredOutcomes(state, firstRun?.traceId ?? "").map((e) => e.outcome)).toEqual([
      "result supplied",
      "approved",
      "denied",
    ]);
  });
});

describe("blueprintIndex", () => {
  const agentAt = (id: string, file: string | null, fields: object = {}): [number, Event] => [
    1,
    {
      type: "blueprint.agent",
      agent: agentBlueprint({
        id,
        source: file === null ? "object" : "code",
        location: file === null ? null : { file, line: 1 },
        ...fields,
      }),
    },
  ];
  const graphAt = (id: string, file: string | null): [number, Event] => [
    2,
    {
      type: "blueprint.graph",
      graph: {
        ...graphBlueprint(id, ["a", "b"]),
        location: file === null ? null : { file, line: 1 },
        warnings: ["w"],
      },
    },
  ];
  const shape = (index: ReturnType<typeof blueprintIndex>) =>
    index.groups.map((group) => [group.file, group.entries.map((entry) => entry.id)]);

  test("one group, not by file, when no blueprint comes from source", () => {
    const index = blueprintIndex(fixtureState("sample.pviz.jsonl"));
    expect(index).toMatchObject({ byFile: false, total: 5, matched: 5 });
    expect(shape(index)).toEqual([[null, ["a1", "a2", "a3", "a4", "g1"]]]);
  });

  test("one file is one group too: its name would be on every row", () => {
    const index = blueprintIndex(stateOf(agentAt("a1", "app.py"), agentAt("a2", "app.py")));
    expect(index.byFile).toBe(false);
    expect(shape(index)).toEqual([["app.py", ["a1", "a2"]]]);
  });

  test("several files: grouped in first-seen order, agents before graphs in each", () => {
    const data = stateOf(
      agentAt("a1", "pkg/desk.py", { warnings: ["x", "y"] }),
      agentAt("a2", "pkg/reporters.py"),
      agentAt("a3", "pkg/desk.py"),
      agentAt("a4", null),
      graphAt("g1", "pkg/desk.py"),
      graphAt("g2", "pkg/flows.py"),
    );
    const index = blueprintIndex(data);
    expect(index).toMatchObject({ byFile: true, total: 6, matched: 6 });
    // What has no file is not from source: it is listed last, whenever it arrived.
    expect(shape(index)).toEqual([
      ["pkg/desk.py", ["a1", "a3", "g1"]],
      ["pkg/reporters.py", ["a2"]],
      ["pkg/flows.py", ["g2"]],
      [null, ["a4"]],
    ]);
    expect(index.groups[0]?.entries).toEqual([
      { kind: "agent", id: "a1", label: "a1", file: "pkg/desk.py", warnings: 2 },
      { kind: "agent", id: "a3", label: "a3", file: "pkg/desk.py", warnings: 0 },
      { kind: "graph", id: "g1", label: "g1", file: "pkg/desk.py", warnings: 1 },
    ]);
  });

  test("a query matches label, name or file, in any case, and keeps the grouping", () => {
    const data = stateOf(
      agentAt("a1", "billing/refunds.py", { name: "refund_agent", label: "Refunds" }),
      agentAt("a2", "billing/invoices.py", { name: null, label: "invoicer" }),
      agentAt("a3", "support/triage.py", { name: "triage", label: "triage" }),
      graphAt("g1", "support/flows.py"),
    );
    expect(shape(blueprintIndex(data, "BILLING"))).toEqual([
      ["billing/refunds.py", ["a1"]],
      ["billing/invoices.py", ["a2"]],
    ]);
    expect(shape(blueprintIndex(data, "  refund_agent "))).toEqual([
      ["billing/refunds.py", ["a1"]],
    ]);
    expect(shape(blueprintIndex(data, "invoicer"))).toEqual([["billing/invoices.py", ["a2"]]]);
    expect(shape(blueprintIndex(data, "g1"))).toEqual([["support/flows.py", ["g1"]]]);
    // Still "by file" with one group left: the heading says which file the match is in.
    expect(blueprintIndex(data, "g1")).toMatchObject({ byFile: true, total: 4, matched: 1 });
    expect(blueprintIndex(data, "nothing like it")).toMatchObject({
      groups: [],
      total: 4,
      matched: 0,
    });
    expect(blueprintIndex(data, "   ").matched).toBe(4);
  });

  test("an empty session, and the size from which a filter is offered", () => {
    expect(blueprintIndex(stateOf())).toEqual({ groups: [], byFile: false, total: 0, matched: 0 });
    expect(BLUEPRINT_FILTER_FROM).toBeGreaterThan(5);
  });

  test("fileName is the last part of a path", () => {
    expect(fileName("pkg/agents/desk.py")).toBe("desk.py");
    expect(fileName("desk.py")).toBe("desk.py");
    expect(fileName("")).toBe("");
  });
});
