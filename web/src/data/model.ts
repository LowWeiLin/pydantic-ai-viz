/**
 * How a blueprint's model and capabilities are named in a line of text: the canvas, a Mermaid
 * label, an accessible name, the inspector. A model read from an environment variable is named
 * by that variable, never by the default the source gives for it.
 */
import type { AgentBlueprint, Capability, CapabilityCategory, ModelInfo } from "../wire";

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

/** A capability as listed, with its index in `blueprint.capabilities`. */
export type ShownCapability = { index: number; capability: Capability };

/** The capabilities that are not folded away, in the docs' category order, then as listed. */
export function shownCapabilities(agent: AgentBlueprint): ShownCapability[] {
  return agent.capabilities
    .map((capability, index) => ({ index, capability }))
    .filter((entry) => !foldedAway(entry.capability))
    .sort(
      (a, b) =>
        (categoryOrder.get(capabilityCategory(a.capability)) ?? 0) -
          (categoryOrder.get(capabilityCategory(b.capability)) ?? 0) || a.index - b.index,
    );
}
