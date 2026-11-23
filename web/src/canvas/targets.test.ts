// @vitest-environment node
/** Skills and hooks as parts of an agent, and where the spans that load them or use hidden tools show. */
import { describe, expect, test } from "vitest";
import { canvasModel, runView } from "../data/selectors";
import {
  agentBlueprint,
  agentRun,
  capability,
  hook,
  lifecycle,
  sid,
  skill,
  stateOf,
  TRACE,
  toolCall,
} from "../test/factory";
import type { Json, Span, Tool } from "../wire";
import { buildAgentFlow } from "./AgentCanvas";
import {
  agentHas,
  type CanvasTarget,
  hookNodeId,
  latestSpanFor,
  parsePart,
  partOf,
  skillNodeId,
  spanTargets,
  targetNodeId,
} from "./targets";

const tool = (name: string, toolset: string, fn: string): Tool => ({
  name,
  toolset,
  description: null,
  parameters_schema: {},
  return_schema: null,
  kind: "function",
  takes_ctx: null,
  is_async: null,
  max_retries: null,
  function: fn,
  origin: "object",
  location: null,
  library_version: null,
});

const briefing = agentBlueprint({
  id: "a1",
  name: "briefing",
  toolsets: [
    {
      id: "ts1",
      toolset_id: "memory",
      label: "MemoryToolset 'memory'",
      kind: "function",
      wrappers: [],
      opaque: false,
      expression: null,
      location: null,
    },
    {
      id: "ts2",
      toolset_id: "shell",
      label: "FunctionToolset 'shell'",
      kind: "function",
      wrappers: [],
      opaque: false,
      expression: null,
      location: null,
    },
  ],
  tools: [
    tool("write_memory", "ts1", "pydantic_ai_harness.memory.write_memory"),
    tool("run_shell", "ts2", "pydantic_ai_harness.shell.run_shell"),
  ],
  capabilities: [
    capability({ name: "ToolSearch", builtin: true, package: "pydantic_ai" }),
    capability({
      name: "Skills",
      package: "pydantic_ai_harness",
      skills: [skill({ name: "fact-check" }), skill({ name: "house-style" })],
    }),
    capability({ name: "Memory", package: "pydantic_ai_harness", toolsets: ["ts1"] }),
    capability({ name: "Search", deferred: true, capability_id: "search" }),
    capability({ name: "Coder", package: "pydantic_ai_harness" }),
    capability({
      name: "Shell",
      package: "pydantic_ai_harness",
      stack: "Coder",
      toolsets: ["ts2"],
    }),
    capability({
      name: "Hooks",
      category: "loop",
      hooks: [hook({ point: "before_model_request" }), hook({ point: "before_tool_execute" })],
    }),
  ],
});

const call = (id: string, start: number, name: string, args: Json): Span => {
  const span = toolCall({
    id,
    parent: "root",
    start,
    end: start + 2,
    tool: name,
    agent: "briefing",
  });
  return { ...span, data: { ...span.data, arguments: args } };
};
const root = agentRun({ id: "root", start: 0, end: 50, agent: "briefing" });
const spans = [
  root,
  call("load", 5, "load_capability", { id: "house-style" }),
  call("search", 10, "load_capability", { id: "search" }),
  call("other", 15, "load_capability", { id: "nothing-by-that-name" }),
  call("write", 20, "write_memory", { content: "x" }),
  call("shell", 25, "run_shell", { command: "ls" }),
];
const state = stateOf([0, { type: "blueprint.agent", agent: briefing }], ...lifecycle(...spans));
const view = runView(state, TRACE, Number.POSITIVE_INFINITY);
if (!view) throw new Error("expected a view");

describe("a skill as a part of its agent", () => {
  const house = { kind: "skill", agentId: "a1", index: 1, name: "house-style" } as const;

  test("is written into the agent's link and read back from it", () => {
    const parts: [CanvasTarget, string][] = [
      [house, "skill:1:house-style"],
      // A colon in a name stays in the name.
      [{ kind: "skill", agentId: "a1", index: 12, name: "ns:x" }, "skill:12:ns:x"],
    ];
    for (const [target, part] of parts) {
      expect(partOf(target)).toBe(part);
      expect(parsePart("a1", part)).toEqual(target);
    }
    for (const part of ["skill:", "skill:1", "skill:1:", "skill:x:house-style", "skill::a"]) {
      expect(parsePart("a1", part)).toBeNull();
    }
    expect(targetNodeId(house)).toBe(skillNodeId("a1", 1, "house-style"));
    expect(skillNodeId("a1", 1, "house-style")).toBe("skill:a1:1:house-style");
  });

  test("counts only when that capability offers a skill of that name", () => {
    expect(agentHas(briefing, house)).toBe(true);
    expect(agentHas(briefing, { ...house, name: "ghost" })).toBe(false);
    expect(agentHas(briefing, { ...house, index: 2 })).toBe(false);
    expect(agentHas(briefing, { ...house, index: 9 })).toBe(false);
    expect(agentHas(briefing, { ...house, agentId: "a2" })).toBe(false);
  });

  test("is no span of its own: picking it in a run shows its blueprint", () => {
    const model = canvasModel(state, "a1", view);
    if (!model) throw new Error("expected a model");
    expect(latestSpanFor(model, house)).toBeNull();
  });
});

describe("a hook as a part of its agent", () => {
  const guard = { kind: "hook", agentId: "a1", index: 6, ordinal: 1 } as const;

  test("is written into the agent's link and read back from it", () => {
    expect(partOf(guard)).toBe("hook:6:1");
    expect(parsePart("a1", "hook:6:1")).toEqual(guard);
    for (const part of ["hook:", "hook:6", "hook:6:", "hook:x:1", "hook:6:guard", "hook::1"]) {
      expect(parsePart("a1", part)).toBeNull();
    }
    expect(targetNodeId(guard)).toBe(hookNodeId("a1", 6, 1));
    expect(hookNodeId("a1", 6, 1)).toBe("hook:a1:6:1");
  });

  test("counts only when that capability registers a hook at that place", () => {
    expect(agentHas(briefing, guard)).toBe(true);
    expect(agentHas(briefing, { ...guard, ordinal: 2 })).toBe(false);
    expect(agentHas(briefing, { ...guard, index: 1 })).toBe(false);
    expect(agentHas(briefing, { ...guard, agentId: "a2" })).toBe(false);
  });

  test("is no span of its own: picking it in a run shows its blueprint", () => {
    const model = canvasModel(state, "a1", view);
    if (!model) throw new Error("expected a model");
    expect(latestSpanFor(model, guard)).toBeNull();
  });
});

describe("where a span shows", () => {
  const targets = (id: string) => spanTargets(state, view, sid(id));

  test("a load_capability call is first the skill it loads, then its tool", () => {
    expect(targets("load").slice(0, 2)).toEqual([
      { kind: "skill", agentId: "a1", index: 1, name: "house-style" },
      { kind: "tool", agentId: "a1", name: "load_capability" },
    ]);
  });

  test("or the on-demand capability whose id it names; an id nothing has is only the tool", () => {
    expect(targets("search")[0]).toEqual({ kind: "capability", agentId: "a1", index: 3 });
    expect(targets("other")[0]).toEqual({ kind: "tool", agentId: "a1", name: "load_capability" });
  });

  test("a call to a tool is the tool, then the capability that gives its toolset, then the run", () => {
    expect(targets("write")).toEqual([
      { kind: "tool", agentId: "a1", name: "write_memory" },
      { kind: "capability", agentId: "a1", index: 2 },
      { kind: "agent", agentId: "a1" },
    ]);
  });

  test("a tool of a harness stack's member is then the stack's entry, which stands for both", () => {
    expect(targets("shell").slice(0, 3)).toEqual([
      { kind: "tool", agentId: "a1", name: "run_shell" },
      { kind: "capability", agentId: "a1", index: 5 },
      { kind: "capability", agentId: "a1", index: 4 },
    ]);
    const model = canvasModel(state, "a1", view);
    if (!model) throw new Error("expected a model");
    const selected = buildAgentFlow(model, true, targets("shell"))
      .nodes.filter((node) => node.data.selected)
      .map((node) => node.id);
    expect(selected).toEqual(["capability:a1:4"]);
  });

  test("with defaults hidden the Memory row stands in for its tool; shown, the tool is outlined", () => {
    const model = canvasModel(state, "a1", view);
    if (!model) throw new Error("expected a model");
    const selected = (showDefaults: boolean) =>
      buildAgentFlow(model, true, targets("write"), showDefaults)
        .nodes.filter((node) => node.data.selected)
        .map((node) => node.id);
    expect(selected(false)).toEqual(["capability:a1:2"]);
    expect(selected(true)).toEqual(["tool:a1:write_memory"]);
    // The skill a load selects is outlined on the canvas.
    const loaded = buildAgentFlow(model, true, targets("load")).nodes.filter(
      (node) => node.data.selected,
    );
    expect(loaded.map((node) => node.id)).toEqual(["skill:a1:1:house-style"]);
  });
});
