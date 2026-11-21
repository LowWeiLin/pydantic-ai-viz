import { describe, expect, test } from "vitest";
import { agentBlueprint, capability, fixtureState } from "../test/factory";
import type { ModelInfo, Tool, Toolset } from "../wire";
import {
  defaultCapability,
  defaultsOf,
  environmentWords,
  LIBRARY_TOOL_NAMES,
  libraryTool,
  libraryToolsets,
  modelLabel,
  shownCapabilities,
} from "./model";

const model = (fields: Partial<ModelInfo>): ModelInfo => ({
  id: null,
  name: "get_model()",
  provider: null,
  offline: false,
  expression: "get_model()",
  model_class: null,
  env: null,
  ...fields,
});

describe("how a model is named in a line", () => {
  test("by its name, or by the environment variable it is read from, never the default", () => {
    expect(modelLabel(model({ name: "gpt-5.2", expression: null }))).toBe("gpt-5.2");
    expect(modelLabel(model({}))).toBe("get_model()");
    const fromEnv = model({ env: { variable: "CLINE_MODEL", default: "cline-pass/x" } });
    expect(modelLabel(fromEnv)).toBe("env CLINE_MODEL");
    expect(environmentWords(model({}))).toBeNull();
    expect(environmentWords(fromEnv)).toBe(
      "From environment variable CLINE_MODEL at run time; default cline-pass/x if CLINE_MODEL is not set",
    );
    expect(environmentWords({ ...fromEnv, model_class: "OpenAIChatModel" })).toMatch(
      /^OpenAIChatModel, model name from environment variable CLINE_MODEL/,
    );
  });
});

const toolset = (id: string, fields: Partial<Toolset> = {}): Toolset => ({
  id,
  toolset_id: id,
  label: `FunctionToolset '${id}'`,
  kind: "function",
  wrappers: [],
  opaque: false,
  expression: null,
  location: null,
  ...fields,
});

const tool = (name: string, toolsetId: string, fn: string | null): Tool => ({
  name,
  toolset: toolsetId,
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

const MEMORY = ["read_memory", "write_memory", "edit_memory", "delete_memory"];
const PLANNING = ["write_plan", "read_plan", "add_task", "update_task", "remove_task", "clear"];

/** The capabilities demo as a live agent gives it. */
const demo = agentBlueprint({
  id: "a1",
  toolsets: [
    toolset("ts0", { kind: "agent", toolset_id: "<agent>" }),
    toolset("ts1"),
    toolset("ts2"),
    toolset("ts3"),
    toolset("ts4"),
  ],
  tools: [
    tool("search_archive", "ts1", "pydantic_ai_viz.demos.capabilities.search_archive"),
    tool("fetch_page", "ts2", "pydantic_ai_viz.demos.capabilities.fetch_page"),
    ...MEMORY.map((name) => tool(name, "ts3", `pydantic_ai_harness.memory.MemoryToolset.${name}`)),
    ...PLANNING.map((name) => tool(name, "ts4", `pydantic_ai_harness.planning._tools.${name}`)),
  ],
  capabilities: [
    capability({ name: "ToolSearch", builtin: true, package: "pydantic_ai", native_tools: ["x"] }),
    capability({ name: "PendingMessageDrainCapability", builtin: true, package: "pydantic_ai" }),
    capability({ name: "Thinking", package: "pydantic_ai", details: { effort: "high" } }),
    capability({ name: "WebSearch", package: "pydantic_ai", toolsets: ["ts1"] }),
    capability({ name: "WebFetch", package: "pydantic_ai", toolsets: ["ts2"] }),
    capability({ name: "Skills", package: "pydantic_ai_harness" }),
    capability({ name: "Memory", package: "pydantic_ai_harness", toolsets: ["ts3"] }),
    capability({ name: "Planning", package: "pydantic_ai_harness", toolsets: ["ts4"] }),
  ],
  settings: {
    ...agentBlueprint({ id: "x" }).settings,
    end_strategy: "graceful",
    tool_retries: 1,
    output_retries: 1,
    defaults: ["end_strategy", "tool_retries", "output_retries"],
  },
});

describe("what Pydantic AI adds by default", () => {
  test("a tool it defines: by version, by the module of its function, or by name", () => {
    const of = (fields: Partial<Tool>) => libraryTool({ ...tool("t", "ts0", null), ...fields });
    expect(of({ library_version: "2.71.0" })).toBe(true);
    expect(of({ function: "pydantic_ai.common_tools.duckduckgo.search" })).toBe(true);
    expect(of({ function: "pydantic_ai_harness.memory.read" })).toBe(true);
    expect(of({ name: "load_capability" })).toBe(true);
    expect(LIBRARY_TOOL_NAMES.has("load_capability")).toBe(true);
    // The author's code, also in a package whose name starts the same.
    expect(of({ function: "pydantic_ai_viz.demos.capabilities.search_archive" })).toBe(false);
    expect(of({ function: "briefing.search_archive" })).toBe(false);
    expect(of({})).toBe(false);
  });

  test("a toolset it gives holds only its own tools; the author's function, code or server keep it the author's", () => {
    expect(libraryToolsets(demo)).toEqual(new Set(["ts3", "ts4"]));
    const harness = { package: "pydantic_ai_harness" } as const;
    const agent = agentBlueprint({
      id: "a1",
      toolsets: [
        toolset("own", { kind: "agent" }),
        toolset("built", { expression: "Toolset(make())" }),
        toolset("mcp", { kind: "mcp", opaque: true }),
        toolset("authored"),
        toolset("opaque", { opaque: true }),
        toolset("nobody"),
      ],
      tools: [tool("mine", "authored", "app.mine"), tool("theirs", "nobody", "pydantic_ai.x")],
      capabilities: [
        capability({ name: "A", ...harness, toolsets: ["own", "built", "mcp", "authored"] }),
        capability({ name: "Memory", ...harness, toolsets: ["opaque"] }),
        // A capability of the author's own gives its toolset as the author's too.
        capability({ name: "Mine", toolsets: ["nobody"] }),
      ],
    });
    expect(libraryToolsets(agent)).toEqual(new Set(["opaque"]));
  });

  test("a capability it adds: a built-in nobody configured, or a member of a harness stack", () => {
    expect(defaultCapability(capability({ name: "ToolSearch", builtin: true }))).toBe(true);
    expect(
      defaultCapability(
        capability({ name: "ToolSearch", builtin: true, details: { max_results: 3 } }),
      ),
    ).toBe(false);
    expect(defaultCapability(capability({ name: "Shell", stack: "Coder" }))).toBe(true);
    // The stack itself, and a harness capability the author lists, are the author's.
    expect(defaultCapability(capability({ name: "Coder", package: "pydantic_ai_harness" }))).toBe(
      false,
    );
    expect(defaultCapability(capability({ name: "Memory", package: "pydantic_ai_harness" }))).toBe(
      false,
    );
  });

  test("the demo as a live agent: two built-ins and the ten tools of Memory and Planning", () => {
    expect(defaultsOf(demo)).toEqual({
      capabilities: [0, 1],
      toolsets: ["ts3", "ts4"],
      tools: [...MEMORY, ...PLANNING],
      settings: ["end_strategy", "tool_retries", "output_retries"],
      count: 12,
    });
    expect(shownCapabilities(demo).map((entry) => entry.index)).not.toContain(0);
    expect(shownCapabilities(demo, true)).toHaveLength(demo.capabilities.length);
  });

  test("the demo read from source: no built-ins, and each toolset only known at run time counts once", () => {
    const fixture = fixtureState("blueprint/capabilities.pviz.jsonl").agents.a1;
    if (!fixture) throw new Error("expected the capabilities fixture");
    const found = defaultsOf(fixture);
    expect(found.tools).toEqual([]);
    expect(found.settings).toEqual([]);
    expect(found.toolsets.map((id) => fixture.toolsets.find((t) => t.id === id)?.label)).toEqual([
      "MemoryToolset 'memory'",
      "PlanningToolset 'planning'",
    ]);
    // The fixture lists the two built-ins as an object would, which source never does.
    expect(found.count).toBe(found.capabilities.length + 2);
  });

  test("a stack's helper toolset is a default, which lists nothing to draw or count", () => {
    const researcher = agentBlueprint({
      id: "a1",
      toolsets: [toolset("ts0", { label: "FunctionToolset 'researcher_instructions'" })],
      capabilities: [
        capability({ name: "Researcher", package: "pydantic_ai_harness", toolsets: ["ts0"] }),
      ],
    });
    expect(defaultsOf(researcher)).toMatchObject({ toolsets: ["ts0"], count: 0 });
  });

  test("the demo after a run: the load_capability it was only seen calling counts too", () => {
    const observed = { origin: "observed" } as const;
    const ran = {
      ...demo,
      toolsets: [...demo.toolsets, toolset("ts5", { kind: "other", label: "Observed tools" })],
      tools: [...demo.tools, { ...tool("load_capability", "ts5", null), ...observed }],
    };
    expect(defaultsOf(ran).toolsets).toEqual(["ts3", "ts4", "ts5"]);
    expect(defaultsOf(ran).count).toBe(13);
    // A tool of the author's seen beside it keeps the group the author's.
    const mixed = {
      ...ran,
      tools: [...ran.tools, { ...tool("lookup", "ts5", null), ...observed }],
    };
    expect(libraryToolsets(mixed).has("ts5")).toBe(false);
  });
});
