/**
 * The playback clock, as a pure state machine. The store holds one `Playback`; a
 * `requestAnimationFrame` driver feeds `tick`, and every view reads `effectiveTime`.
 *
 * - `live`: pinned to "now". Views project at `Infinity`, so nothing is hidden.
 * - `playing`: `t` advances by wall time x speed and stops at the end of the range.
 * - `paused`: `t` stays where the user left it.
 */

export type PlaybackMode = "live" | "playing" | "paused";

export type Playback = {
  mode: PlaybackMode;
  /** Epoch ms on the event clock. Meaningless in live mode. */
  t: number;
  speed: number;
};

export type Bounds = { start: number; end: number };

export const SPEEDS = [0.5, 1, 2, 4, 8] as const;

export const livePlayback: Playback = { mode: "live", t: 0, speed: 1 };

const clamp = (t: number, bounds: Bounds): number =>
  Math.min(bounds.end, Math.max(bounds.start, t));

/** The projection time views should use. */
export function effectiveTime(playback: Playback): number {
  return playback.mode === "live" ? Number.POSITIVE_INFINITY : playback.t;
}

/** Starts playing from the beginning of the range (how a recording opens). */
export function playFromStart(playback: Playback, bounds: Bounds): Playback {
  return { ...playback, mode: "playing", t: bounds.start };
}

/** Resumes. From the end of the range (or from live) it restarts at the beginning. */
export function play(playback: Playback, bounds: Bounds): Playback {
  if (playback.mode === "live" || playback.t >= bounds.end) return playFromStart(playback, bounds);
  return { ...playback, mode: "playing", t: clamp(playback.t, bounds) };
}

/** Freezes the clock. Pausing from live freezes at the end of the range. */
export function pause(playback: Playback, bounds: Bounds): Playback {
  if (playback.mode === "paused") return playback;
  return { ...playback, mode: "paused", t: playback.mode === "live" ? bounds.end : playback.t };
}

/** Space bar: anything that moves (a replay, or a live view) pauses; a paused clock plays. */
export function togglePlay(playback: Playback, bounds: Bounds): Playback {
  return playback.mode === "paused" ? play(playback, bounds) : pause(playback, bounds);
}

/** Jumps to a time and holds there. */
export function seek(playback: Playback, t: number, bounds: Bounds): Playback {
  return { ...playback, mode: "paused", t: clamp(t, bounds) };
}

/** Re-pins to now. */
export function goLive(playback: Playback): Playback {
  return playback.mode === "live" ? playback : { ...playback, mode: "live" };
}

/** Advances a playing clock by `elapsedMs` of wall time. Other modes are returned unchanged. */
export function tick(playback: Playback, elapsedMs: number, bounds: Bounds): Playback {
  if (playback.mode !== "playing" || elapsedMs <= 0) return playback;
  const t = playback.t + elapsedMs * playback.speed;
  if (t >= bounds.end) return { ...playback, mode: "paused", t: bounds.end };
  return { ...playback, t: Math.max(t, bounds.start) };
}

/** Moves one notch along `SPEEDS`; a speed that is not on the list snaps to its neighbour. */
export function changeSpeed(playback: Playback, direction: 1 | -1): Playback {
  const next =
    direction === 1
      ? SPEEDS.find((s) => s > playback.speed)
      : SPEEDS.findLast((s) => s < playback.speed);
  return next === undefined ? playback : { ...playback, speed: next };
}

/**
 * Steps to the previous or next span boundary (`boundaries` sorted ascending) and pauses there.
 * From live, "previous" steps back from the end of the range.
 */
export function step(
  playback: Playback,
  boundaries: readonly number[],
  direction: 1 | -1,
  bounds: Bounds,
): Playback {
  const from = playback.mode === "live" ? bounds.end : playback.t;
  const target =
    direction === 1 ? boundaries.find((b) => b > from) : boundaries.findLast((b) => b < from);
  if (target === undefined)
    return seek(playback, direction === 1 ? bounds.end : bounds.start, bounds);
  return seek(playback, target, bounds);
}

/** Position of the clock within the range, 0 to 1. Live is 1. */
export function progress(playback: Playback, bounds: Bounds): number {
  if (playback.mode === "live") return 1;
  const span = bounds.end - bounds.start;
  if (span <= 0) return 1;
  return (clamp(playback.t, bounds) - bounds.start) / span;
}
