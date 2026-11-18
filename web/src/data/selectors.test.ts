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
import type { Event, Span } from "../wire";
import {
  assembleStream,
  blueprintForAgentName,
  canvasModel,
  displayStatus,
  graphCanvasModel,
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

  test("delegation cycles terminate and depth is capped", () => {
    const chain = ["a1", "a2", "a3", "a4", "a5"].map((id, i, ids) =>
      agentBlueprint({
        id,
        delegates: [
          { tool_name: "next", agent_id: ids[(i + 1) % ids.length] ?? id, evidence: "static" },
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
