/** React bindings for the playback clock: read the time, and drive it while playing. */
import { useEffect } from "react";
import { type Bounds, effectiveTime, playFromStart, tick } from "./playback";
import { useViewer } from "./store";

/** The projection time every time-aware view should use. `Infinity` in live mode. */
export function usePlaybackTime(): number {
  return useViewer((state) => effectiveTime(state.playback));
}

/**
 * Advances the clock on animation frames while it is playing. Mount it in the view that owns
 * the range (the run screen), with that run's bounds. Motion preferences do not apply: this
 * moves data time, not decoration.
 */
export function usePlaybackDriver(bounds: Bounds | null): void {
  const playing = useViewer((state) => state.playback.mode === "playing");
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
      useViewer.getState().updatePlayback((playback) => tick(playback, elapsed, { start, end }));
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [playing, start, end]);
}

/**
 * Recordings and exports replay from the start when a run is opened; live sessions stay
 * pinned to now. Runs once per run id.
 */
export function useAutoReplay(traceId: string, bounds: Bounds | null): void {
  const replayable = useViewer((state) => state.source.kind !== "live");
  const known = bounds !== null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: restart only when the run changes, not when its bounds grow.
  useEffect(() => {
    if (!replayable || !bounds) return;
    useViewer.getState().updatePlayback((playback) => playFromStart(playback, bounds));
  }, [traceId, replayable, known]);
}
