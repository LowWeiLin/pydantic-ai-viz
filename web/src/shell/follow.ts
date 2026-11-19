/**
 * Following a live session: when a new run starts, the viewer goes to it, the way a log tail
 * scrolls. It does so only when that cannot interrupt anything:
 *
 * - the view is pinned to now (pausing, scrubbing or stepping stops the following);
 * - the new run is news: it entered the log after the server's backlog for this page ended,
 *   or it is still going. Finished runs in the backlog a late viewer receives are history. A
 *   run that ends within one batch of events (a fast agent, a busy page) is news all the same;
 * - the user is watching a run with no span selected, or waiting on a blueprint for the
 *   session's first run. Someone reading a blueprint while runs come and go is left alone.
 */
import { useEffect, useRef } from "react";
import { goLive } from "../data/playback";
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
  context: { live: boolean; pinned: boolean; route: Route; backlogEnd: number | null },
): string | null {
  const newest = list[0];
  if (!context.live || !context.pinned || !newest) return null;
  if (newest.traceId === before.newest) return null;
  const { backlogEnd } = context;
  const news = newest.status === "running" || (backlogEnd !== null && newest.firstSeq > backlogEnd);
  if (!news) return null;
  const { route } = context;
  if (route.name === "run") {
    return route.spanId === null && route.traceId !== newest.traceId ? newest.traceId : null;
  }
  const waiting = route.name === "agent" || route.name === "graph" || route.name === "home";
  return waiting && before.count === 0 ? newest.traceId : null;
}

/**
 * A live viewer outlives the program it watches: after edit, Ctrl+C and run again on the same
 * port, the open tab reconnects into a new session. The reducer starts the data over; the route
 * and the clock are state of the old session too. A run of the old session no longer exists, so
 * the viewer goes home (the new session's latest run, or its first blueprint) and is pinned to
 * now again. A blueprint route stays: the same program gives its agents the same ids.
 *
 * Mount once, in the shell.
 */
export function useSessionRestart(route: Route): void {
  const sessionId = useViewer((state) => state.data.session?.session_id ?? null);
  const live = useViewer((state) => state.source.kind === "live");
  const previous = useRef<string | null>(null);
  useEffect(() => {
    const before = previous.current;
    previous.current = sessionId;
    if (!live || before === null || sessionId === null || before === sessionId) return;
    useViewer.getState().updatePlayback(goLive);
    if (route.name === "run") navigate({ name: "home" }, { replace: true });
  }, [sessionId, live, route]);
}

/** Mount once, in the shell. */
export function useFollowLive(route: Route): void {
  const list = useViewer((state) => runs(state.data));
  const live = useViewer((state) => state.source.kind === "live");
  const pinned = useViewer((state) => state.playback.mode === "live");
  const backlogEnd = useViewer((state) => state.backlogEnd);
  const seen = useRef<Seen>(nothingSeen);
  useEffect(() => {
    const target = followTarget(seen.current, list, { live, pinned, route, backlogEnd });
    seen.current = seenOf(list);
    if (target === null) return;
    // From one run to the next the history keeps a single entry; Back leaves the runs.
    navigate({ name: "run", traceId: target, spanId: null }, { replace: route.name === "run" });
  }, [list, live, pinned, route, backlogEnd]);
}
