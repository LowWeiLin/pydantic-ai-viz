/**
 * Following a live session: when a new run starts, the viewer goes to it, the way a log tail
 * scrolls. It does so only when that cannot interrupt anything:
 *
 * - the view is pinned to now (pausing, scrubbing or stepping stops the following);
 * - the new run is still going (the backlog a late viewer receives is history, not news);
 * - the user is watching a run with no span selected, or waiting on a blueprint for the
 *   session's first run. Someone reading a blueprint while runs come and go is left alone.
 */
import { useEffect, useRef } from "react";
import { type RunSummary, runs } from "../data/selectors";
import { useViewer } from "../data/store";
import { navigate, type Route } from "./router";

/** What had been seen when the question was last asked. */
export type Seen = { newest: string | null; count: number };

export const nothingSeen: Seen = { newest: null, count: 0 };

/** `list` is newest first, as `runs()` returns it. */
export const seenOf = (list: readonly RunSummary[]): Seen => ({
  newest: list[0]?.traceId ?? null,
  count: list.length,
});

/** The trace id to go to, or null to stay. */
export function followTarget(
  before: Seen,
  list: readonly RunSummary[],
  context: { live: boolean; pinned: boolean; route: Route },
): string | null {
  const newest = list[0];
  if (!context.live || !context.pinned || !newest) return null;
  if (newest.traceId === before.newest || newest.status !== "running") return null;
  const { route } = context;
  if (route.name === "run") {
    return route.spanId === null && route.traceId !== newest.traceId ? newest.traceId : null;
  }
  const waiting = route.name === "agent" || route.name === "graph" || route.name === "home";
  return waiting && before.count === 0 ? newest.traceId : null;
}

/** Mount once, in the shell. */
export function useFollowLive(route: Route): void {
  const list = useViewer((state) => runs(state.data));
  const live = useViewer((state) => state.source.kind === "live");
  const pinned = useViewer((state) => state.playback.mode === "live");
  const seen = useRef<Seen>(nothingSeen);
  useEffect(() => {
    const target = followTarget(seen.current, list, { live, pinned, route });
    seen.current = seenOf(list);
    if (target === null) return;
    // From one run to the next the history keeps a single entry; Back leaves the runs.
    navigate({ name: "run", traceId: target, spanId: null }, { replace: route.name === "run" });
  }, [list, live, pinned, route]);
}
