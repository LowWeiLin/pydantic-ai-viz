/**
 * How a blueprint's model and capabilities are named in a line of text: the canvas, a Mermaid
 * label, an accessible name, the inspector. A model read from an environment variable is named
 * by that variable, never by the default the source gives for it. Also which parts Pydantic AI
 * added by default.
 */
import type { AgentBlueprint, Capability, CapabilityCategory, ModelInfo, Tool } from "../wire";

/** "gpt-5.2", "get_model()", or "env CLINE_MODEL" for a name the environment gives. */
export const modelLabel = (model: ModelInfo): string =>
  model.env ? `env ${model.env.variable}` : model.name;

/** A sentence about where an environment-read model comes from, with its default as a default. */
export function environmentWords(model: ModelInfo): string | null {
  const { env } = model;
  if (!env) return null;
  const what = model.model_class ? `${model.model_class}, model name from` : "From";
  const fallback =
    env.default === null ? "no default" : `default ${env.default} if ${env.variable} is not set`;
  return `${what} environment variable ${env.variable} at run time; ${fallback}`;
}

/** The groups of the Pydantic AI capabilities docs, in the docs' order, with a short name. */
export const CAPABILITY_CATEGORIES: [CapabilityCategory, label: string, short: string][] = [
  ["harness", "Harnesses", "Harness"],
  ["environment", "Execution environments", "Environment"],
  ["tools", "Tools & native abilities", "Tools"],
  ["integration", "Integrations", "Integration"],
  ["web", "Web & research", "Web"],
  ["reasoning", "Reasoning, planning & delegation", "Reasoning"],
  ["context", "Context management", "Context"],
  ["knowledge", "Knowledge & memory", "Knowledge"],
  ["safety", "Control & safety", "Safety"],
  ["self_extension", "Self-extension", "Self-extension"],
  ["runtime", "Execution runtime", "Runtime"],
  ["loop", "Loop customization", "Loop"],
  ["custom", "Custom", "Custom"],
];
const categoryOrder = new Map(CAPABILITY_CATEGORIES.map(([id], i) => [id as string, i]));

/** A category this viewer does not know is read as "custom" (the set is open). */
export const capabilityCategory = (item: Capability): CapabilityCategory =>
  categoryOrder.has(item.category) ? item.category : "custom";

/** The category's name in the docs and its short name. */
export function categoryNames(item: Capability): { label: string; short: string } {
  const entry = CAPABILITY_CATEGORIES[categoryOrder.get(capabilityCategory(item)) ?? 0];
  return { label: entry?.[1] ?? "Custom", short: entry?.[2] ?? "Custom" };
}

/**
 * A capability Pydantic AI adds to every agent is folded away, unless the agent configures it
 * (`ToolSearch(max_results=3)`). Native tools do not count: the default `ToolSearch` gives one.
 */
export const foldedAway = (item: Capability): boolean =>
  item.builtin &&
  Object.keys(item.details).length === 0 &&
  Object.keys(item.expressions).length === 0 &&
  item.toolsets.length === 0;

// ---------- what Pydantic AI adds by default ----------
//
// The viewer shows what the author wrote, and collapses what Pydantic AI or the harness added
// on its own into counts that one toggle reveals. Which parts those are follows from the wire.

/** The tool Pydantic AI adds to load an on-demand capability or skill by its id. */
export const LOAD_CAPABILITY = "load_capability";

/** Tools Pydantic AI adds by name, also when a blueprint only saw them in a run. */
export const LIBRARY_TOOL_NAMES: ReadonlySet<string> = new Set([LOAD_CAPABILITY]);

/** A tool Pydantic AI or the harness defines, not the author. */
export const libraryTool = (tool: Pick<Tool, "name" | "function" | "library_version">): boolean =>
  tool.library_version !== null ||
  /^pydantic_ai(?:_harness)?\./.test(tool.function ?? "") ||
  LIBRARY_TOOL_NAMES.has(tool.name);

/**
 * The toolsets a Pydantic AI or harness capability gives the agent with only its own tools in
 * them, by id. One that holds the author's function (`WebSearch(local=search_archive)`), an MCP
 * server or a toolset the source builds is the author's; so is the agent's own, which no
 * capability gives. One whose tools are only known at run time (`Memory()` read from source)
 * lists none of the author's, so it counts. So does the group of tools only seen in a run when
 * all of them are ones Pydantic AI adds by name (`load_capability`).
 */
export function libraryToolsets(agent: AgentBlueprint): Set<string> {
  const given = new Set(
    agent.capabilities.filter((item) => item.package !== null).flatMap((item) => item.toolsets),
  );
  const toolsOf = (id: string) => agent.tools.filter((tool) => tool.toolset === id);
  const added = (id: string) => {
    const tools = toolsOf(id);
    return (
      tools.length > 0 &&
      tools.every((tool) => tool.origin === "observed" && LIBRARY_TOOL_NAMES.has(tool.name))
    );
  };
  return new Set(
    agent.toolsets
      .filter(
        (toolset) =>
          (given.has(toolset.id) &&
            toolset.kind === "function" &&
            toolset.expression === null &&
            toolsOf(toolset.id).every(libraryTool)) ||
          (toolset.kind === "other" && !given.has(toolset.id) && added(toolset.id)),
      )
      .map((toolset) => toolset.id),
  );
}

/**
 * A capability Pydantic AI adds to every agent and the agent does not configure, or one a
 * harness stack (`Coder()`) put there. A configured built-in, and a harness capability the
 * author lists (`Memory()`), are the author's.
 */
export const defaultCapability = (item: Capability): boolean =>
  foldedAway(item) || item.stack !== null;

export type Defaults = {
  /** Indices into `blueprint.capabilities`. */
  capabilities: number[];
  /** `Toolset.id`s. */
  toolsets: string[];
  /** Names of the tools in those toolsets. */
  tools: string[];
  /** Fields of `AgentSettings` whose value is the default. */
  settings: string[];
  /** The parts the canvas leaves out: capabilities and tools, a toolset that lists none as one. */
  count: number;
};

/** What Pydantic AI added to this agent by default. */
export function defaultsOf(agent: AgentBlueprint): Defaults {
  const capabilities = agent.capabilities.flatMap((item, index) =>
    defaultCapability(item) ? [index] : [],
  );
  const toolsets = [...libraryToolsets(agent)];
  const tools = agent.tools.filter((tool) => toolsets.includes(tool.toolset)).map((t) => t.name);
  // A toolset that lists no tool is one part when its tools are only known at run time, and is
  // not drawn otherwise (`Researcher`'s instructions).
  const rows = toolsets.reduce((sum, id) => {
    const listed = agent.tools.filter((tool) => tool.toolset === id).length;
    const opaque = agent.toolsets.find((toolset) => toolset.id === id)?.opaque ?? false;
    return sum + (listed > 0 ? listed : opaque ? 1 : 0);
  }, 0);
  return {
    capabilities,
    toolsets,
    tools,
    settings: agent.settings.defaults,
    count: capabilities.length + rows,
  };
}

/** A capability as listed, with its index in `blueprint.capabilities`. */
export type ShownCapability = { index: number; capability: Capability };

/**
 * The capabilities the canvas draws, in the docs' category order, then as listed: the author's,
 * and with `showDefaults` the ones Pydantic AI added too.
 */
export function shownCapabilities(agent: AgentBlueprint, showDefaults = false): ShownCapability[] {
  return agent.capabilities
    .map((capability, index) => ({ index, capability }))
    .filter((entry) => showDefaults || !defaultCapability(entry.capability))
    .sort(
      (a, b) =>
        (categoryOrder.get(capabilityCategory(a.capability)) ?? 0) -
          (categoryOrder.get(capabilityCategory(b.capability)) ?? 0) || a.index - b.index,
    );
}
