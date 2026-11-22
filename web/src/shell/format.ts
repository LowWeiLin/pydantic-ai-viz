/** Number and time formatting shared by every screen. Output is locale-independent. */
import type { DeferredEntry, DeferredOutcome, RunSummary } from "../data/selectors";
import type { DeferredCall } from "../wire";

/** "0 ms", "412 ms", "1.24 s", "12.4 s", "1m 04s". */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "-";
  if (ms < 1) return ms === 0 ? "0 ms" : "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  return `${minutes}m ${String(totalSeconds % 60).padStart(2, "0")}s`;
}

/** "0", "842", "3,109", "12.4k", "1.2M". */
export function formatCount(n: number): string {
  if (!Number.isFinite(n)) return "-";
  if (n < 10_000) return Math.round(n).toLocaleString("en-US");
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** "1 run", "3 runs". */
export function plural(n: number, one: string, many: string = `${one}s`): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** Wall-clock time of an event, "14:03:07", in the viewer's time zone. */
export function formatClock(epochMs: number): string {
  const d = new Date(epochMs);
  if (Number.isNaN(d.getTime())) return "-";
  const two = (v: number): string => String(v).padStart(2, "0");
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

// ---------- deferred tool calls ----------

/** "waiting for approval: 2 calls", "waiting for a result: 1 call". */
export function waitingWords(calls: readonly DeferredCall[]): string {
  const approvals = calls.some((call) => call.kind === "approval");
  const external = calls.some((call) => call.kind === "external");
  const what =
    approvals && external
      ? "approval and results"
      : approvals
        ? "approval"
        : calls.length === 1
          ? "a result"
          : "results";
  return `waiting for ${what}: ${plural(calls.length, "call")}`;
}

const OUTCOME_ORDER: readonly DeferredOutcome[] = [
  "approved",
  "denied",
  "result supplied",
  "not run",
  "waiting",
];

/** "1 approved, 1 denied": how many calls had each outcome, in a fixed order. */
export function outcomeWords(entries: readonly DeferredEntry[]): string {
  return OUTCOME_ORDER.flatMap((outcome) => {
    const n = entries.filter((entry) => entry.outcome === outcome).length;
    return n > 0 ? [`${formatCount(n)} ${outcome}`] : [];
  }).join(", ");
}

/** What tells one run of an agent from the next: its name and when it started. */
export function runLabel(run: RunSummary): string {
  return `${run.name} ${formatClock(run.startMs)}`;
}
