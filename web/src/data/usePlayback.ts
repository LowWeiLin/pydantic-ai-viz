/** React bindings for the playback clock: read the time, and drive it while playing. */
import { useEffect, useMemo, useState } from "react";
import { type Bounds, effectiveTime, playFromStart, seek, tick } from "./playback";
import { runBounds, runs } from "./selectors";
import { useViewer } from "./store";

/** The projection time every time-aware view should use. `Infinity` in live mode. */
export function usePlaybackTime(): number {
  return useViewer((state) => effectiveTime(state.playback));
}

/** How often a live clock is redrawn. Durations are shown to a hundredth of a second at most. */
const LIVE_TICK_MS = 100;

/**
 * When the newest event arrived, on the page's own clock. Kept outside React, so a screen that
 * is mounted later still counts from the arrival and not from its own mount.
 */
let arrival = { ts: Number.NaN, at: 0 };
useViewer.subscribe((state) => {
  if (state.data.lastTs !== arrival.ts) arrival = { ts: state.data.lastTs, at: performance.now() };
});

/**
 * "Now" on the event clock for a run that is being watched live; null for anything else.
 *
 * Events are the only clock the log has. Between two of them (a slow tool, a model request that
 * is not streamed) nothing on screen would move, and a healthy run would look hung. So while
 * the view is pinned to now, the stream is connected and the run has not ended, time counts on
 * from the arrival of the newest event. Measuring from the arrival, not from the event's own
 * timestamp, keeps this independent of how the browser's clock compares with the server's.
 */
export function useLiveNow(traceId: string): number | null {
  const lastTs = useViewer((state) => state.data.lastTs);
  const active = useViewer(
    (state) =>
      state.source.kind === "live" &&
      state.playback.mode === "live" &&
      state.connection === "open" &&
      runs(state.data).find((run) => run.traceId === traceId)?.endMs === null,
  );
  const [, redraw] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => redraw((n) => n + 1), LIVE_TICK_MS);
    return () => clearInterval(timer);
  }, [active]);
  return active ? lastTs + (performance.now() - arrival.at) : null;
}

/**
 * The time range of a run as the clock sees it. For a recording that is the run itself. A run
 * watched live reaches to now (see `useLiveNow`), and a live view that was paused there keeps
 * the moment it was paused at, although no event has that time yet.
 */
export function useRunBounds(traceId: string): Bounds | null {
  const data = useViewer((state) => state.data);
  const live = useViewer((state) => state.source.kind === "live");
  const playback = useViewer((state) => state.playback);
  const liveNow = useLiveNow(traceId);
  const recorded = useMemo(() => runBounds(data, traceId), [data, traceId]);
  const reach = playback.mode === "live" ? liveNow : playback.t;
  const end = live && recorded && reach !== null && reach > recorded.end ? reach : null;
  return useMemo(
    () => (recorded && end !== null ? { ...recorded, end } : recorded),
    [recorded, end],
  );
}

/**
 * Advances the clock on animation frames while it is playing. Mount it in the view that owns
 * the range (the run screen), with that run's bounds. Motion preferences do not apply: this
 * moves data time, not decoration.
 */
export function usePlaybackDriver(bounds: Bounds | null): void {
  const playing = useViewer((state) => state.playback.mode === "playing");
  const atEnd = useViewer((state) => (state.source.kind === "live" ? "live" : "pause"));
  const start = bounds?.start;
  const end = bounds?.end;

  useEffect(() => {
    if (!playing || start === undefined || end === undefined) return;
    let frame = 0;
    let last = performance.now();
    const loop = (now: number): void => {
      // A background tab can hand back a huge gap; never jump more than a quarter second.
      const elapsed = Math.min(250, now - last);
      last = now;
      useViewer
        .getState()
        .updatePlayback((playback) => tick(playback, elapsed, { start, end }, atEnd));
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [playing, start, end, atEnd]);
}

/**
 * Recordings and exports replay from the start when a run is opened; live sessions stay
 * pinned to now. Runs once per run id.
 *
 * A run opened on a span (a shared link, a reload) is held at its end instead: a replay would
 * hide the very span the link points at until the clock got there.
 */
export function useAutoReplay(traceId: string, bounds: Bounds | null, spanId: string | null): void {
  const replayable = useViewer((state) => state.source.kind !== "live");
  const known = bounds !== null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: restart only when the run changes, not when its bounds grow or the selection moves.
  useEffect(() => {
    if (!replayable || !bounds) return;
    useViewer
      .getState()
      .updatePlayback((playback) =>
        spanId ? seek(playback, bounds.end, bounds) : playFromStart(playback, bounds),
      );
  }, [traceId, replayable, known]);
}
