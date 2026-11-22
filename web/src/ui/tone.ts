/** Shared vocabulary for colour: a tone is the accent, a node kind or a status. */
import type { DisplayStatus } from "../data/selectors";
import type { SpanKind } from "../wire";
import type { IconName } from "./Icon";

export type Tone =
  | "neutral"
  | "accent"
  | "agent"
  | "model"
  | "tool"
  | "output"
  | "graph"
  | "ok"
  | "retry"
  | "error";

/** Joins class names, skipping falsy entries. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** The class that sets `--tone`. Written out in full so Tailwind and grep can both find them. */
export const toneClass: Record<Tone, string> = {
  neutral: "tone-neutral",
  accent: "tone-accent",
  agent: "tone-agent",
  model: "tone-model",
  tool: "tone-tool",
  output: "tone-output",
  graph: "tone-graph",
  ok: "tone-ok",
  retry: "tone-retry",
  error: "tone-error",
};

export type Status = DisplayStatus | "idle";

/** Status is never colour alone: each has its own icon shape and a word. */
export const statusMeta: Record<Status, { label: string; icon: IconName; tone: Tone }> = {
  idle: { label: "Idle", icon: "idle", tone: "neutral" },
  running: { label: "Running", icon: "running", tone: "accent" },
  ok: { label: "OK", icon: "check", tone: "ok" },
  waiting: { label: "Waiting", icon: "pause", tone: "retry" },
  retry: { label: "Retried", icon: "retry", tone: "retry" },
  error: { label: "Error", icon: "error", tone: "error" },
};

export const kindMeta: Record<SpanKind, { label: string; icon: IconName; tone: Tone }> = {
  agent_run: { label: "Agent run", icon: "agent", tone: "agent" },
  model_request: { label: "Model request", icon: "model", tone: "model" },
  tool_call: { label: "Tool call", icon: "tool", tone: "tool" },
  graph_run: { label: "Graph run", icon: "graph", tone: "graph" },
  graph_node: { label: "Graph node", icon: "graph", tone: "graph" },
  other: { label: "Span", icon: "span", tone: "neutral" },
};
