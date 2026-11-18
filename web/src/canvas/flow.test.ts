/** What the canvases hand to React Flow, and how spans map onto canvas nodes. */
import { describe, expect, test } from "vitest";
import { canvasModel, graphCanvasModel, type RunView, runs, runView } from "../data/selectors";
import { fixtureState } from "../test/factory";
import type { Span } from "../wire";
import { buildAgentFlow } from "./AgentCanvas";
import { arcPath, type FlowEdgeData, lanePath } from "./edges";
import { buildGraphFlow } from "./GraphCanvas";
import { activityPhrase, graphNodeState } from "./nodes";
import { agentModelSignature, graphModelSignature } from "./signature";
import {
  agentNodeId,
  graphNodeId,
  latestSpanFor,
  outputNodeId,
  sameTarget,
  spanTargets,
  targetNodeId,
  toolNodeId,
} from "./targets";

const LIVE = Number.POSITIVE_INFINITY;
const sample = fixtureState("sample.pviz.jsonl");
const [graphRun, triageRun] = runs(sample);
if (!graphRun || !triageRun) throw new Error("expected two runs in the sample");

const viewAt = (traceId: string, t: number): RunView => {
  const view = runView(sample, traceId, t);
  if (!view) throw new Error("expected a view");
  return view;
};
const triageEnd = viewAt(triageRun.traceId, LIVE);
const spanNamed = (view: RunView, name: string, nth = 0): Span => {
  const found = view.order.flatMap((id) => view.spans[id] ?? []).filter((s) => s.name === name);
  const span = found[nth];
  if (!span) throw new Error(`no span ${name}`);
  return span;
};

function agentFlow(view: RunView | null, subject = "a1") {
  const model = canvasModel(sample, subject, view);
  if (!model) throw new Error("expected a model");
  return { model, ...buildAgentFlow(model, view !== null, []) };
}

const edgeData = (edges: { id: string; data?: FlowEdgeData }[], id: string): FlowEdgeData => {
  const data = edges.find((edge) => edge.id === id)?.data;
  if (!data) throw new Error(`no edge ${id}`);
  return data;
};

describe("agent canvas flow", () => {
  test("the blueprint alone: every node, idle edges, tool rows inside their toolset", () => {
    const { nodes, edges } = agentFlow(null);
    const types = nodes.map((node) => node.type);
    expect(types.filter((t) => t === "agent")).toHaveLength(2);
    expect(types.filter((t) => t === "tool")).toHaveLength(5);
    expect(types.filter((t) => t === "toolset")).toHaveLength(3);
    expect(types.filter((t) => t === "output")).toHaveLength(2);
    const row = nodes.find((node) => node.id === toolNodeId("a1", "get_order"));
    expect(row?.parentId).toMatch(/^toolset:a1:/);
    expect(
      edges.filter((edge) => edge.type === "flow").every((e) => e.data?.state === "idle"),
    ).toBe(true);
  });

  test("a delegation seen in a run is solid, one only found in code is dashed", () => {
    const { edges } = agentFlow(null);
    const seen = edgeData(edges, "delegation:a1:escalate_refund:a2");
    expect(seen).toMatchObject({ dashed: false, label: "in a run", state: "done" });
    // It runs down a lane into the top of the sub-agent's card.
    expect(seen.lane).toBeGreaterThan(0);
    expect(edges.find((edge) => edge.id === "delegation:a1:escalate_refund:a2")).toMatchObject({
      sourceHandle: "out",
      targetHandle: "down",
    });

    const staticOnly = structuredClone(sample);
    const delegate = staticOnly.agents.a1?.delegates[0];
    if (!delegate) throw new Error("expected a delegate");
    delegate.evidence = "static";
    const model = canvasModel(staticOnly, "a1", null);
    if (!model) throw new Error("expected a model");
    const found = edgeData(
      buildAgentFlow(model, false, []).edges,
      "delegation:a1:escalate_refund:a2",
    );
    expect(found).toMatchObject({ dashed: true, label: "in code", state: "idle" });
  });

  test("in a replay the delegation edge turns solid when the sub-agent first runs", () => {
    const refunds = spanNamed(triageEnd, "refunds");
    const id = "delegation:a1:escalate_refund:a2";
    const before = agentFlow(viewAt(triageRun.traceId, refunds.start_ms - 1)).edges;
    expect(edgeData(before, id)).toMatchObject({ dashed: true, label: "not yet", state: "idle" });
    const after = agentFlow(viewAt(triageRun.traceId, refunds.start_ms)).edges;
    expect(edgeData(after, id)).toMatchObject({ dashed: false, label: "in a run" });
  });

  test("mid-run: edges to running tools move, finished ones are done, the output waits", () => {
    const firstTools = spanNamed(triageEnd, "get_customer");
    const { edges } = agentFlow(viewAt(triageRun.traceId, firstTools.start_ms + 1));
    expect(edgeData(edges, "flow:a1:get_customer").state).toBe("active");
    expect(edgeData(edges, "flow:a1:search_kb").state).toBe("active");
    expect(edgeData(edges, "flow:a1:escalate_refund").state).toBe("idle");
    expect(edgeData(edges, "output:a1").state).toBe("idle");
  });

  test("while the sub-agent runs its delegation edge is active; at the end everything is done", () => {
    const refunds = spanNamed(triageEnd, "refunds");
    const during = agentFlow(viewAt(triageRun.traceId, refunds.start_ms + 1)).edges;
    expect(edgeData(during, "delegation:a1:escalate_refund:a2").state).toBe("active");
    expect(edgeData(during, "flow:a1:escalate_refund").state).toBe("active");

    const after = agentFlow(triageEnd).edges;
    expect(edgeData(after, "delegation:a1:escalate_refund:a2").state).toBe("done");
    expect(edgeData(after, "output:a1").state).toBe("done");
    expect(edgeData(after, "output:a2").state).toBe("done");
    expect(after.filter((e) => e.type === "flow" && e.data?.state === "active")).toEqual([]);
  });

  test("the first highlight target that is on the canvas is the one outlined", () => {
    const model = canvasModel(sample, "a2", triageEnd);
    if (!model) throw new Error("expected a model");
    // A triage tool is not on the refunds canvas; the refunds model chip is.
    const { nodes } = buildAgentFlow(model, true, [
      { kind: "tool", agentId: "a1", name: "get_order" },
      { kind: "model", agentId: "a2" },
    ]);
    const selected = nodes.filter((node) => node.data.selected);
    expect(selected.map((node) => [node.id, node.data.selected])).toEqual([
      [agentNodeId("a2"), "model"],
    ]);
  });
});

describe("graph canvas flow", () => {
  const model = (view: RunView | null) => {
    const built = graphCanvasModel(sample, "g1", view);
    if (!built) throw new Error("expected a graph model");
    return built;
  };

  test("nodes are drawn by kind; the loop leaves and enters from below", () => {
    const { nodes, edges } = buildGraphFlow(model(null), false, []);
    const type = (id: string) => nodes.find((node) => node.id === graphNodeId(id))?.type;
    expect(type("__start__")).toBe("terminal");
    expect(type("__end__")).toBe("terminal");
    expect(type("decision")).toBe("decision");
    expect(type("write")).toBe("step");
    const loop = edges.find((edge) => edge.data?.label === "needs work");
    expect(loop).toMatchObject({ sourceHandle: "loop-out", targetHandle: "loop-in" });
    expect(edges.filter((edge) => edge.sourceHandle === "out")).toHaveLength(5);
    expect(edges.every((edge) => edge.data?.state === "idle")).toBe(true);
  });

  test("a finished run marks every edge it took; mid-run one edge is active", () => {
    const done = buildGraphFlow(model(viewAt(graphRun.traceId, LIVE)), true, []);
    expect(done.edges.every((edge) => edge.data?.state === "done")).toBe(true);

    const mid = buildGraphFlow(model(viewAt(graphRun.traceId, graphRun.startMs + 500)), true, []);
    const states = mid.edges.map((edge) => edge.data?.state);
    expect(states.filter((s) => s === "active")).toHaveLength(1);
    expect(states.filter((s) => s === "done")).toHaveLength(0);
  });

  test("a waypoint that was passed counts as done", () => {
    const entries = model(viewAt(graphRun.traceId, LIVE)).nodes;
    const decision = entries.find((entry) => entry.node.id === "decision");
    expect(decision && graphNodeState(decision)).toBe("ok");
    const before = model(null).nodes.find((entry) => entry.node.id === "decision");
    expect(before && graphNodeState(before)).toBe("idle");
  });
});

describe("targets", () => {
  test("a span points at its own node, then at the nodes of its ancestors", () => {
    const refund = spanNamed(triageEnd, "issue_refund");
    expect(spanTargets(sample, triageEnd, refund.span_id)).toEqual([
      { kind: "tool", agentId: "a2", name: "issue_refund" },
      { kind: "agent", agentId: "a2" },
      { kind: "tool", agentId: "a1", name: "escalate_refund" },
      { kind: "agent", agentId: "a1" },
    ]);
    const request = spanNamed(triageEnd, "scripted-support");
    expect(spanTargets(sample, triageEnd, request.span_id)[0]).toEqual({
      kind: "model",
      agentId: "a1",
    });
    expect(spanTargets(sample, triageEnd, "nope")).toEqual([]);
  });

  test("spans inside a graph step point at the graph node last", () => {
    const view = viewAt(graphRun.traceId, LIVE);
    const writer = spanNamed(view, "writer");
    const targets = spanTargets(sample, view, writer.span_id);
    expect(targets[0]).toMatchObject({ kind: "agent" });
    expect(targets.at(-1)).toEqual({ kind: "graphNode", nodeId: "write" });
  });

  test("a click selects the latest span on that node", () => {
    const { model } = agentFlow(triageEnd);
    const latest = latestSpanFor(model, { kind: "tool", agentId: "a1", name: "get_order" });
    expect(latest).toBe(spanNamed(triageEnd, "get_order", 1).span_id);
    expect(latestSpanFor(model, { kind: "agent", agentId: "a1" })).toBe(triageEnd.rootId);
    expect(latestSpanFor(model, { kind: "output", agentId: "a1" })).toBe(triageEnd.rootId);
    expect(latestSpanFor(model, { kind: "tool", agentId: "a1", name: "ghost" })).toBeNull();
    expect(latestSpanFor(model, { kind: "graphNode", nodeId: "write" })).toBeNull();

    const graph = graphCanvasModel(sample, "g1", viewAt(graphRun.traceId, LIVE));
    if (!graph) throw new Error("expected a graph model");
    const view = viewAt(graphRun.traceId, LIVE);
    expect(latestSpanFor(graph, { kind: "graphNode", nodeId: "write" })).toBe(
      spanNamed(view, "write", 1).span_id,
    );
    expect(latestSpanFor(graph, { kind: "graphNode", nodeId: "decision" })).toBeNull();

    const untouched = agentFlow(null).model;
    expect(latestSpanFor(untouched, { kind: "agent", agentId: "a1" })).toBeNull();
  });

  test("node ids and equality", () => {
    expect(targetNodeId({ kind: "model", agentId: "a1" })).toBe(agentNodeId("a1"));
    expect(targetNodeId({ kind: "output", agentId: "a1" })).toBe(outputNodeId("a1"));
    expect(sameTarget({ kind: "agent", agentId: "a1" }, { kind: "agent", agentId: "a1" })).toBe(
      true,
    );
    // The model chip lives on the agent card but is a different target.
    expect(sameTarget({ kind: "agent", agentId: "a1" }, { kind: "model", agentId: "a1" })).toBe(
      false,
    );
    expect(sameTarget(null, { kind: "agent", agentId: "a1" })).toBe(false);
  });
});

describe("signatures", () => {
  test("stay the same between span boundaries and change when a span starts or ends", () => {
    const tools = spanNamed(triageEnd, "get_customer");
    const at = (t: number) => {
      const model = canvasModel(sample, "a1", viewAt(triageRun.traceId, t));
      if (!model) throw new Error("expected a model");
      return agentModelSignature(model);
    };
    expect(at(tools.start_ms + 1)).toBe(at(tools.start_ms + 2));
    expect(at(tools.start_ms + 1)).not.toBe(at(tools.start_ms - 1));
    expect(at(LIVE)).not.toBe(at(tools.start_ms + 1));

    const graphAt = (t: number) => {
      const model = graphCanvasModel(sample, "g1", viewAt(graphRun.traceId, t));
      if (!model) throw new Error("expected a graph model");
      return graphModelSignature(model);
    };
    expect(graphAt(graphRun.startMs + 500)).toBe(graphAt(graphRun.startMs + 501));
    expect(graphAt(graphRun.startMs + 500)).not.toBe(graphAt(LIVE));
  });
});

describe("words and paths", () => {
  test("activity is spoken in full, and not at all on a plain blueprint", () => {
    const idle = { state: "idle" as const, calls: 0, retries: 0, errors: 0, spanIds: [] };
    expect(activityPhrase(idle, false)).toBe("");
    expect(activityPhrase(idle, true)).toBe(", not used yet");
    expect(activityPhrase({ ...idle, state: "ok", calls: 2, retries: 1 }, true)).toBe(
      ", retried once, 2 calls, OK",
    );
    expect(activityPhrase({ ...idle, state: "running", calls: 1 }, true)).toBe(", 1 call, running");
    expect(activityPhrase({ ...idle, state: "error", calls: 3, errors: 1 }, true)).toBe(
      ", 3 calls, failed",
    );
    expect(activityPhrase({ ...idle, state: "retry", calls: 1, retries: 1 }, true)).toBe(
      ", retried once, 1 call, waiting for a retry",
    );
  });

  test("a loop arc dips below both ends and puts its label at the lowest point", () => {
    const [path, labelX, labelY] = arcPath(300, 100, 100, 120);
    expect(path).toBe("M 300,100 C 300,192 100,192 100,120");
    expect(labelX).toBe(200);
    expect(labelY).toBeGreaterThan(120);
    expect(labelY).toBeLessThan(192);
  });

  test("a delegation lane: straight into the inlet, or a jog just above the card", () => {
    const [straight, x, y] = lanePath(500, 100, 532, 400, 532);
    expect(straight).toBe("M 500,100 H 524 Q 532,100 532,108 V 400");
    expect([x, y]).toEqual([532, 378]);
    // The inlet React Flow measures can be a pixel off: still one straight lane.
    expect(lanePath(500, 100, 533.2, 400, 532)[0]).toBe(straight);

    const [jogged, labelX, labelY] = lanePath(500, 100, 532, 400, 574);
    expect(jogged).toBe(
      "M 500,100 H 566 Q 574,100 574,108 V 380 Q 574,388 566,388 H 540 Q 532,388 532,396 V 400",
    );
    // The label rides the lane, above the jog.
    expect(labelX).toBe(574);
    expect(labelY).toBeLessThan(388);
  });
});
