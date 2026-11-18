/** Pure maths for the timeline: where ticks go and where a span's bar sits. */
import type { Bounds } from "../data/playback";

const STEPS = [1, 2, 5];

/**
 * Tick offsets in ms from the start of a range, at a "nice" interval (1, 2 or 5 times a power
 * of ten) chosen so that there are at most `maxTicks` of them. Always starts with 0.
 */
export function rulerTicks(durationMs: number, maxTicks = 8): number[] {
  if (!(durationMs > 0) || maxTicks < 1) return [0];
  let step = 1;
  for (let exponent = -3; ; exponent++) {
    const magnitude = 10 ** exponent;
    const found = STEPS.find((s) => durationMs / (s * magnitude) <= maxTicks);
    if (found !== undefined) {
      step = found * magnitude;
      break;
    }
  }
  const ticks: number[] = [];
  // Multiplying by the index (not adding the step repeatedly) keeps the values exact.
  for (let i = 0; i * step <= durationMs * (1 + 1e-9); i++) ticks.push(i * step);
  return ticks;
}

/** A tick label: "0", "250 ms", "1.5 s", "2 s", "1m 30s". Shorter than `formatDuration`. */
export function tickLabel(ms: number): string {
  if (ms === 0) return "0";
  if (ms < 1) return `${Number(ms.toPrecision(2))} ms`;
  if (ms < 1000) return `${Number(ms.toFixed(1))} ms`;
  if (ms < 60_000) return `${Number((ms / 1000).toFixed(2))} s`;
  const seconds = Math.round(ms / 1000);
  const rest = seconds % 60;
  return rest === 0 ? `${seconds / 60}m` : `${Math.floor(seconds / 60)}m ${rest}s`;
}

/** Position of `t` within the range, 0 to 1. A zero-length range maps everything to 0. */
export function fraction(t: number, bounds: Bounds): number {
  const span = bounds.end - bounds.start;
  if (!(span > 0)) return 0;
  return Math.min(1, Math.max(0, (t - bounds.start) / span));
}

/**
 * Where a span's bar sits on the waterfall, as fractions of the track. A span that is still
 * running ends at `now`. A span that starts at or after the end of the range (a recording
 * that stops mid-run) is pinned to the right edge rather than drawn outside.
 */
export function barExtent(
  startMs: number,
  endMs: number | null,
  now: number,
  bounds: Bounds,
): { left: number; width: number } {
  const left = fraction(startMs, bounds);
  const right = fraction(Math.max(startMs, endMs ?? now), bounds);
  return { left, width: Math.max(0, right - left) };
}
