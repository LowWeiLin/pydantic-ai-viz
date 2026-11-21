// @vitest-environment node
import { describe, expect, test } from "vitest";
import { canvasModel } from "../data/selectors";
import { agentBlueprint, capability, fixtureState, graphBlueprint, stateOf } from "../test/factory";
import type { AgentBlueprint, Event, GraphBlueprint } from "../wire";
import { arcPath } from "./edges";
import {
  agentLayoutKey,
  backEdges,
  DELEGATION_INLET,
  graphLayoutKey,
  graphNodeLabel,
  graphNodeSize,
  type LayoutNode,
  layoutAgentCanvas,
  layoutGraph,
  nodeKind,
  SEEN_GROUP,
  SIZE,
  toolGroups,
} from "./layout";
import {
  agentNodeId,
  capabilityNodeId,
  graphNodeId,
  outputNodeId,
  toolNodeId,
  toolsetNodeId,
} from "./targets";

const sample = fixtureState("sample.pviz.jsonl");

const stateWith = (...agents: AgentBlueprint[]) =>
  stateOf(
    ...agents.map((agent, i) => [i + 1, { type: "blueprint.agent", agent }] as [number, Event]),
  );

const tool = (name: string, toolset = "ts0"): AgentBlueprint["tools"][number] => ({
  name,
  toolset,
  description: null,
  parameters_schema: {},
  return_schema: null,
  kind: "function",
  takes_ctx: null,
  is_async: null,
  max_retries: null,
  function: null,
  origin: "object",
  location: null,
  library_version: null,
});

const toolset = (id: string, opaque = false): AgentBlueprint["toolsets"][number] => ({
  id,
  toolset_id: id,
  label: id,
  kind: opaque ? "dynamic" : "agent",
  wrappers: [],
  opaque,
  expression: null,
  location: null,
});

type Box = { id: string; x: number; y: number; width: number; height: number };

const apart = (a: Box, b: Box): boolean =>
  a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;

function expectNoOverlap(nodes: Box[]): void {
  for (const [i, a] of nodes.entries()) {
    for (const b of nodes.slice(i + 1)) expect(apart(a, b), `${a.id} over ${b.id}`).toBe(true);
  }
}

describe("backEdges", () => {
  const edges = (pairs: string): [string, string][] =>
    pairs.split(" ").map((pair) => pair.split(">") as [string, string]);

  test("a chain has none", () => {
    expect(backEdges(["a", "b", "c"], edges("a>b b>c"), ["a"])).toEqual(new Set());
  });

  test("the edge that returns to an earlier node is the back edge, wherever it is listed", () => {
    // start > write > review > decision > (publish | write)
    const list = edges("s>w w>r r>d d>p d>w p>e");
    expect(backEdges(["s", "w", "r", "d", "p", "e"], list, ["s"])).toEqual(new Set([4]));
    // Listing the loop first, and the nodes in another order, changes nothing.
    const shuffled = edges("d>w p>e d>p r>d w>r s>w");
    expect(backEdges(["e", "p", "d", "r", "w", "s"], shuffled, ["s"])).toEqual(new Set([0]));
  });

  test("a diamond is not a cycle; a self loop is", () => {
    expect(backEdges(["a", "b", "c", "d"], edges("a>b a>c b>d c>d"), ["a"])).toEqual(new Set());
    expect(backEdges(["a"], edges("a>a"), ["a"])).toEqual(new Set([0]));
  });

  test("nodes the roots do not reach are still walked, and unknown roots are harmless", () => {
    expect(backEdges(["a", "x", "y"], edges("x>y y>x"), ["a", "ghost"])).toEqual(new Set([1]));
  });
});

describe("toolGroups", () => {
  const agentOf = (blueprint: AgentBlueprint, unlisted: string[] = []) => {
    const model = canvasModel(stateWith(blueprint), blueprint.id, null);
    const agent = model?.agents[0];
    if (!agent) throw new Error("expected an agent");
    const idle = { state: "idle" as const, calls: 0, retries: 0, errors: 0, spanIds: [] };
    return { ...agent, unlisted: unlisted.map((name) => ({ name, activity: idle })) };
  };

  test("groups tools by toolset and leaves empty plain toolsets out", () => {
    const agent = agentOf(
      agentBlueprint({
        id: "a1",
        toolsets: [toolset("ts0"), toolset("ts1"), toolset("empty")],
        tools: [tool("a"), tool("b", "ts1"), tool("c")],
      }),
    );
    expect(toolGroups(agent)).toEqual([
      { toolsetId: "ts0", tools: ["a", "c"], placeholder: false },
      { toolsetId: "ts1", tools: ["b"], placeholder: false },
    ]);
  });

  test("an opaque toolset shows a placeholder until a run reveals its tools", () => {
    const blueprint = agentBlueprint({ id: "a1", toolsets: [toolset("dyn", true)] });
    expect(toolGroups(agentOf(blueprint))).toEqual([
      { toolsetId: "dyn", tools: [], placeholder: true },
    ]);
    expect(toolGroups(agentOf(blueprint, ["fetch"]))).toEqual([
      { toolsetId: "dyn", tools: ["fetch"], placeholder: false },
    ]);
  });

  test("unlisted tools with no single opaque home get a group of their own", () => {
    const blueprint = agentBlueprint({ id: "a1", toolsets: [toolset("ts0")], tools: [tool("a")] });
    expect(toolGroups(agentOf(blueprint, ["mystery"])).at(-1)).toEqual({
      toolsetId: SEEN_GROUP,
      tools: ["mystery"],
      placeholder: false,
    });
  });
});

describe("layoutAgentCanvas", () => {
  const model = canvasModel(sample, "a1", null);
  if (!model) throw new Error("expected the triage canvas");
  const layout = layoutAgentCanvas(model);
  const byId = new Map(layout.nodes.map((node) => [node.id, node]));
  const node = (id: string): LayoutNode => {
    const found = byId.get(id);
    if (!found) throw new Error(`no node ${id}`);
    return found;
  };

  test("is deterministic", () => {
    expect(layoutAgentCanvas(model)).toEqual(layout);
  });

  test("a staircase: tools and output right of the agent, the sub-agent's row underneath", () => {
    const triage = node(agentNodeId("a1"));
    const refunds = node(agentNodeId("a2"));
    const output = node(outputNodeId("a1"));
    const escalate = node(toolNodeId("a1", "escalate_refund"));
    const group = node(escalate.parentId ?? "");
    expect(triage).toMatchObject({ x: 0, y: 0 });
    expect(output.x).toBeGreaterThanOrEqual(triage.x + triage.width);
    expect(group.x).toBe(output.x);
    // The output closes the column, under the toolsets.
    expect(output.y).toBeGreaterThanOrEqual(group.y + group.height);
    // The sub-agent: below everything of triage, one step right, with its own column.
    expect(refunds.y).toBeGreaterThan(output.y + output.height);
    expect(refunds.x).toBeGreaterThan(triage.x);
    expect(node(outputNodeId("a2")).x).toBeGreaterThanOrEqual(refunds.x + refunds.width);
    // Half the width of a side-by-side chain: the picture fits a canvas at a readable zoom.
    const right = Math.max(...layout.nodes.filter((n) => !n.parentId).map((n) => n.x + n.width));
    expect(right).toBeLessThanOrEqual(880);
  });

  test("the delegation lane runs clear of the column and into the sub-agent's inlet", () => {
    const refunds = node(agentNodeId("a2"));
    const group = node(node(toolNodeId("a1", "escalate_refund")).parentId ?? "");
    const delegation = layout.edges.find((edge) => edge.kind === "delegation");
    expect(delegation?.lane).toBe(refunds.x + DELEGATION_INLET);
    expect(delegation?.lane).toBeGreaterThan(group.x + group.width);
    expect(layout.edges.filter((e) => e.kind !== "delegation").every((e) => e.lane === null)).toBe(
      true,
    );
  });

  test("several sub-agents share an indent; later lanes pass between cards and columns", () => {
    const names = ["b", "c", "d"];
    const hub = agentBlueprint({
      id: "hub",
      toolsets: [toolset("ts0")],
      tools: [...names.map((name) => tool(`ask_${name}`)), tool("ask_b_again")],
      delegates: [
        ...names.map((name) => ({
          tool_name: `ask_${name}`,
          agent_id: name,
          evidence: "object" as const,
        })),
        { tool_name: "ask_b_again", agent_id: "b", evidence: "object" as const },
      ],
    });
    const subs = names.map((id) =>
      agentBlueprint({ id, toolsets: [toolset("ts0")], tools: [tool("work")] }),
    );
    const fan = canvasModel(stateWith(hub, ...subs), "hub", null);
    if (!fan) throw new Error("expected a model");
    const result = layoutAgentCanvas(fan);
    const blocks = result.nodes.filter((n) => n.parentId === null);
    expectNoOverlap(blocks);
    const card = (id: string) => blocks.find((n) => n.id === agentNodeId(id)) as LayoutNode;
    expect(new Set(names.map((id) => card(id).x)).size).toBe(1);
    expect(names.map((id) => card(id).y)).toEqual(
      [...names.map((id) => card(id).y)].sort((a, b) => a - b),
    );

    const delegations = result.edges.filter((edge) => edge.kind === "delegation");
    expect(delegations.every((edge) => !edge.back)).toBe(true);
    const lane = (to: string) => delegations.filter((e) => e.toAgentId === to).map((e) => e.lane);
    // Two tools that reach the same agent share its lane.
    expect(lane("b")).toEqual([card("b").x + DELEGATION_INLET, card("b").x + DELEGATION_INLET]);
    // No lane runs through a block it passes on the way down.
    for (const [index, id] of names.entries()) {
      const x = lane(id)[0] as number;
      for (const above of blocks) {
        if (above.agentId === "hub" || above.id === agentNodeId(id)) continue;
        if (above.y >= card(id).y) continue;
        expect(x <= above.x || x >= above.x + above.width, `${id} lane through ${above.id}`).toBe(
          true,
        );
      }
      if (index > 0) expect(x).toBeGreaterThan(lane(names[index - 1] as string)[0] as number);
    }
  });

  test("no two blocks overlap, and tool rows stay inside their toolset box", () => {
    expectNoOverlap(layout.nodes.filter((n) => n.parentId === null));
    const rows = layout.nodes.filter((n) => n.parentId !== null);
    expect(rows).toHaveLength(5);
    for (const row of rows) {
      const box = node(row.parentId ?? "");
      expect(row.x).toBeGreaterThanOrEqual(0);
      expect(row.y).toBeGreaterThanOrEqual(SIZE.groupHeader);
      expect(row.x + row.width).toBeLessThanOrEqual(box.width);
      expect(row.y + row.height).toBeLessThanOrEqual(box.height);
    }
  });

  test("every tool has a flow edge, every agent an output edge; delegation starts at the tool", () => {
    const kinds = layout.edges.map((edge) => edge.kind);
    expect(kinds.filter((k) => k === "flow")).toHaveLength(5);
    expect(kinds.filter((k) => k === "output")).toHaveLength(2);
    const delegation = layout.edges.find((edge) => edge.kind === "delegation");
    expect(delegation).toMatchObject({
      source: toolNodeId("a1", "escalate_refund"),
      target: agentNodeId("a2"),
      toAgentId: "a2",
      back: false,
    });
  });

  test("a delegation cycle keeps the subject on top and draws the way back as an arc", () => {
    const a = agentBlueprint({
      id: "a1",
      toolsets: [toolset("ts0")],
      tools: [tool("ask_b")],
      delegates: [{ tool_name: "ask_b", agent_id: "a2", evidence: "object" }],
    });
    const b = agentBlueprint({
      id: "a2",
      toolsets: [toolset("ts0")],
      tools: [tool("ask_a")],
      delegates: [{ tool_name: "ask_a", agent_id: "a1", evidence: "object" }],
    });
    for (const subject of ["a1", "a2"]) {
      const other = subject === "a1" ? "a2" : "a1";
      const cyclic = canvasModel(stateWith(a, b), subject, null);
      if (!cyclic) throw new Error("expected a model");
      const result = layoutAgentCanvas(cyclic);
      const at = (id: string) => result.nodes.find((n) => n.id === agentNodeId(id));
      expect(at(subject)).toMatchObject({ x: 0, y: 0 });
      expect(at(other)?.y).toBeGreaterThan(0);
      const back = result.edges.filter((edge) => edge.kind === "delegation").map((e) => e.back);
      expect(back).toEqual([false, true]);
      expectNoOverlap(result.nodes.filter((n) => n.parentId === null));
    }
  });

  test("the layout key changes with structure only", () => {
    const key = agentLayoutKey(model);
    const again = canvasModel(sample, "a1", null);
    expect(again && agentLayoutKey(again)).toBe(key);
    const other = canvasModel(sample, "a2", null);
    expect(other && agentLayoutKey(other)).not.toBe(key);
  });
});

describe("capabilities on the agent canvas", () => {
  const builtins = [
    capability({ name: "ToolSearch", builtin: true, category: "context", native_tools: ["x"] }),
    capability({ name: "PendingMessageDrainCapability", builtin: true, category: "runtime" }),
  ];
  const layoutOf = (...agents: AgentBlueprint[]) => {
    const model = canvasModel(stateWith(...agents), agents[0]?.id ?? "", null);
    if (!model) throw new Error("expected a model");
    return { model, layout: layoutAgentCanvas(model) };
  };
  const find = (nodes: LayoutNode[], id: string): LayoutNode => {
    const found = nodes.find((n) => n.id === id);
    if (!found) throw new Error(`no node ${id}`);
    return found;
  };

  test("an agent with only the built-in ones is laid out as one with none", () => {
    const plain = agentBlueprint({ id: "a1", toolsets: [toolset("ts0")], tools: [tool("a")] });
    const folded = { ...plain, capabilities: builtins };
    const without = layoutOf(plain);
    const withBuiltins = layoutOf(folded);
    expect(withBuiltins.layout).toEqual(without.layout);
    expect(withBuiltins.layout.nodes.map((n) => n.kind)).not.toContain("capabilities");
  });

  test("a box under the card lists them by docs group, and links each to the toolsets it gives", () => {
    const { layout } = layoutOf(
      agentBlueprint({
        id: "a1",
        toolsets: [toolset("ts0"), toolset("ts1"), toolset("empty")],
        tools: [tool("a"), tool("b", "ts1")],
        capabilities: [
          ...builtins,
          capability({ name: "Memory", category: "knowledge", toolsets: ["ts1"] }),
          capability({ name: "Thinking", category: "reasoning" }),
          // Gives a toolset with no tools, which the canvas leaves out: no edge.
          capability({ name: "Hollow", category: "tools", toolsets: ["empty"] }),
        ],
      }),
    );
    const card = find(layout.nodes, agentNodeId("a1"));
    const box = find(layout.nodes, "capabilities:a1");
    expect(box).toMatchObject({
      kind: "capabilities",
      x: card.x,
      y: card.y + card.height + 12,
      width: card.width,
      parentId: null,
    });
    const rows = layout.nodes.filter((n) => n.kind === "capability");
    // tools, reasoning, knowledge: the docs' order; the index stays the one in the blueprint.
    expect(rows.map((n) => n.id)).toEqual([4, 3, 2].map((i) => capabilityNodeId("a1", i)));
    expect(rows.map((n) => n.ref)).toEqual(["4", "3", "2"]);
    for (const row of rows) {
      expect(row.parentId).toBe(box.id);
      expect(row.x).toBeGreaterThanOrEqual(0);
      expect(row.y).toBeGreaterThanOrEqual(SIZE.groupHeader);
      expect(row.x + row.width).toBeLessThanOrEqual(box.width);
      expect(row.y + row.height).toBeLessThanOrEqual(box.height);
    }
    expect(layout.edges.filter((e) => e.kind === "gives")).toEqual([
      {
        id: "gives:a1:2:ts1",
        kind: "gives",
        source: capabilityNodeId("a1", 2),
        target: toolsetNodeId("a1", "ts1"),
        agentId: "a1",
        ref: "ts1",
        toAgentId: null,
        back: false,
        lane: null,
      },
    ]);
    expectNoOverlap(layout.nodes.filter((n) => n.parentId === null));
  });

  test("a tall list pushes the next row down and stays clear of the lanes to sub-agents", () => {
    const many = (n: number) =>
      Array.from({ length: n }, (_, i) => capability({ name: `Cap${i}`, category: "safety" }));
    const names = ["b", "c"];
    const hub = agentBlueprint({
      id: "hub",
      toolsets: [toolset("ts0")],
      tools: names.map((name) => tool(`ask_${name}`)),
      delegates: names.map((name) => ({
        tool_name: `ask_${name}`,
        agent_id: name,
        evidence: "object" as const,
      })),
      capabilities: many(12),
    });
    const subs = names.map((id) =>
      agentBlueprint({
        id,
        toolsets: [toolset("ts0")],
        tools: [tool("work")],
        capabilities: many(6),
      }),
    );
    const { layout } = layoutOf(hub, ...subs);
    const blocks = layout.nodes.filter((n) => n.parentId === null);
    expectNoOverlap(blocks);
    const hubBox = find(layout.nodes, "capabilities:hub");
    const bBox = find(layout.nodes, "capabilities:b");
    const b = find(layout.nodes, agentNodeId("b"));
    const c = find(layout.nodes, agentNodeId("c"));
    expect(b.y).toBeGreaterThanOrEqual(hubBox.y + hubBox.height);
    expect(c.y).toBeGreaterThanOrEqual(bBox.y + bBox.height);
    // Every lane passes the capability boxes above its sub-agent, never through one.
    for (const edge of layout.edges.filter((e) => e.kind === "delegation")) {
      const card = find(layout.nodes, agentNodeId(edge.toAgentId ?? ""));
      for (const box of blocks.filter((n) => n.kind === "capabilities" && n.y < card.y)) {
        const lane = edge.lane ?? Number.NaN;
        expect(lane <= box.x || lane >= box.x + box.width, `lane through ${box.id}`).toBe(true);
      }
    }
  });

  test("a delegation cycle back into an agent with capabilities arcs in under their box", () => {
    const a = agentBlueprint({
      id: "a1",
      toolsets: [toolset("ts0")],
      tools: [tool("ask_b")],
      delegates: [{ tool_name: "ask_b", agent_id: "a2", evidence: "object" }],
      capabilities: [
        capability({ name: "Thinking", category: "reasoning" }),
        capability({ name: "Memory", category: "knowledge" }),
      ],
    });
    const b = agentBlueprint({
      id: "a2",
      toolsets: [toolset("ts0")],
      tools: [tool("ask_a")],
      delegates: [{ tool_name: "ask_a", agent_id: "a1", evidence: "object" }],
    });
    const { layout } = layoutOf(a, b);
    const loop = layout.edges.find((edge) => edge.back);
    expect(loop).toMatchObject({ source: toolNodeId("a2", "ask_a"), target: "capabilities:a1" });
    // The arc as drawn, from the bottom of the tool row to the bottom of the box, misses the
    // card and the box: it never comes between the rows of capabilities.
    const absolute = (node: LayoutNode): LayoutNode => {
      const parent = node.parentId ? find(layout.nodes, node.parentId) : null;
      return parent ? { ...node, x: parent.x + node.x, y: parent.y + node.y } : node;
    };
    const from = absolute(find(layout.nodes, toolNodeId("a2", "ask_a")));
    const box = find(layout.nodes, "capabilities:a1");
    const [path] = arcPath(
      from.x + from.width * 0.38,
      from.y + from.height,
      box.x + box.width * 0.62,
      box.y + box.height,
    );
    const points = [...path.matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map((m) => ({
      x: Number(m[1]),
      y: Number(m[2]),
    }));
    expect(points).toHaveLength(4);
    const along = (t: number, axis: "x" | "y"): number => {
      const weights = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
      return points.reduce((sum, point, i) => sum + (weights[i] ?? 0) * point[axis], 0);
    };
    for (const blocked of [find(layout.nodes, agentNodeId("a1")), box]) {
      for (let step = 1; step < 100; step++) {
        const [x, y] = [along(step / 100, "x"), along(step / 100, "y")];
        const inside =
          x > blocked.x &&
          x < blocked.x + blocked.width &&
          y > blocked.y &&
          y < blocked.y + blocked.height;
        expect(inside, `arc through ${blocked.id} at t=${step / 100}`).toBe(false);
      }
    }
  });

  test("the layout key changes when the capabilities shown change", () => {
    const plain = agentBlueprint({ id: "a1" });
    const key = (agent: AgentBlueprint) => agentLayoutKey(layoutOf(agent).model);
    expect(key({ ...plain, capabilities: builtins })).toBe(key(plain));
    const one = { ...plain, capabilities: [capability({ name: "Thinking" })] };
    expect(key(one)).not.toBe(key(plain));
    // A name is not structure; what it gives is.
    expect(key({ ...plain, capabilities: [capability({ name: "Other" })] })).toBe(key(one));
    expect(
      key({ ...plain, capabilities: [capability({ name: "Thinking", toolsets: ["ts0"] })] }),
    ).not.toBe(key(one));
  });
});

describe("graph layout", () => {
  const graph = sample.graphs.g1 as GraphBlueprint;
  const layout = layoutGraph(graph, []);
  const x = (id: string): number => layout.nodes.find((n) => n.nodeId === id)?.x ?? Number.NaN;

  test("follows the flow from start to end, even though the graph has a loop", () => {
    const order = ["__start__", "write", "review", "decision", "publish", "__end__"];
    const xs = order.map(x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(new Set(xs).size).toBe(order.length);
  });

  test("only the edge that goes back is drawn as a loop", () => {
    const back = layout.edges.filter((edge) => edge.back);
    expect(back).toHaveLength(1);
    expect(back[0]).toMatchObject({
      source: graphNodeId("decision"),
      target: graphNodeId("write"),
      label: "needs work",
    });
  });

  test("nodes do not overlap, and a self loop does not break the layout", () => {
    const looped: GraphBlueprint = {
      ...graphBlueprint("g2", ["a", "b"]),
      edges: [
        { source: "a", target: "b", label: null },
        { source: "b", target: "b", label: "again" },
      ],
    };
    const result = layoutGraph(looped, []);
    expect(result.edges.map((edge) => edge.back)).toEqual([false, true]);
    expectNoOverlap(layout.nodes);
    expectNoOverlap(result.nodes);
  });

  test("nodes a run visited but the blueprint does not list are added as steps", () => {
    const result = layoutGraph(graph, ["mystery", "write"]);
    expect(result.nodes).toHaveLength(graph.nodes.length + 1);
    expect(result.nodes.at(-1)).toMatchObject({ nodeId: "mystery", kind: "step" });
    expect(graphLayoutKey(graph, ["mystery"])).not.toBe(graphLayoutKey(graph, []));
  });

  test("edges to nodes that do not exist are dropped", () => {
    const broken: GraphBlueprint = {
      ...graphBlueprint("g3", ["a", "b"]),
      edges: [
        { source: "a", target: "b", label: null },
        { source: "a", target: "ghost", label: null },
      ],
    };
    expect(layoutGraph(broken, []).edges).toHaveLength(1);
  });

  test("labels and sizes: terminals are named, unknown kinds are steps, decisions are wider", () => {
    expect(graphNodeLabel({ id: "__start__", kind: "start", label: null })).toBe("Start");
    expect(graphNodeLabel({ id: "__end__", kind: "end", label: null })).toBe("End");
    expect(graphNodeLabel({ id: "write", kind: "step", label: "Write draft" })).toBe("Write draft");
    expect(graphNodeLabel({ id: "write", kind: "step", label: null })).toBe("write");
    expect(nodeKind("teleport")).toBe("step");
    expect(nodeKind("decision")).toBe("decision");
    const step = graphNodeSize({ id: "x", kind: "step", label: "approved?", note: null });
    const decision = graphNodeSize({ id: "x", kind: "decision", label: null, note: "approved?" });
    expect(decision.width).toBeGreaterThan(step.width);
    const long = graphNodeSize({ id: "x", kind: "step", label: "x".repeat(200), note: null });
    expect(long.width).toBe(240);
  });
});
