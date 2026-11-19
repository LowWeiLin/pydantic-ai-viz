/**
 * How a blueprint's model is named in a line of text: the canvas chip, a Mermaid label, an
 * accessible name. A model read from an environment variable is named by that variable, never by
 * the default the source gives for it.
 */
import type { ModelInfo } from "../wire";

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
