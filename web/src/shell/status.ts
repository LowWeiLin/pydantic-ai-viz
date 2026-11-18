/**
 * What the shell says about the session, derived in one place so that every fact is stated
 * once. The components that render it (`TopBar`, `Sidebar`, `states`) hold the words; this
 * holds the logic.
 *
 *   state                         top-bar chip   banner          blueprint strip   sidebar, lower part
 *   ----------------------------  -------------  --------------  ----------------  --------------------------
 *   nothing loaded                -              -               -                 (no sidebar)
 *   live, first connection        Connecting     -               -                 (skeleton)
 *   live, no blueprint, no run    Live           -               -                 (no sidebar; the welcome
 *                                                                                  screen says how to start)
 *   live, no run yet              Live           -               Waiting for a run Runs, empty, no sentence
 *   live, runs                    Live           -               -                 Runs
 *   live, connection lost         -              Reconnecting    as before         as before
 *   blueprint-only, served        Blueprint      -               -                 About this view
 *   blueprint-only, server gone   Blueprint      Server stopped  -                 About this view
 *   blueprint-only, in a file     Blueprint      -               -                 About this view
 *   recording                     Recording      -               -                 Runs, or "none in this recording"
 *   export                        Export         -               -                 Runs, or "none in this export"
 *
 * A blueprint-only session (`session.mode == "blueprint"`, what `pydantic-ai-viz view` makes)
 * never gets a run, so nothing in it may say "live", "connected" or "waiting": the chip names
 * what it is, and the sidebar explains it where the runs would have been.
 */
import { useMemo } from "react";
import type { DataState } from "../data/reduce";
import { useViewer, type ViewerState } from "../data/store";
import type { Provenance } from "../wire";

/** The mode chip in the top bar. */
export type ModeChip = "connecting" | "live" | "blueprint" | "recording" | "export";

/** How the blueprints of a blueprint-only session were obtained. */
export type AboutBlueprints = {
  /** "mixed" when some were read from source and some from live objects. */
  origin: "code" | "object" | "mixed";
};

export type ShellStatus = {
  /** Null when nothing is loaded, and while a live session is unreachable (the banner says so). */
  chip: ModeChip | null;
  /** The strip under the top bar. */
  banner: "reconnecting" | "server-stopped" | null;
  /** The strip above a blueprint screen. */
  strip: "waiting-for-run" | null;
  /** `skeleton` while a live session connects; `lists` once there is something to list. */
  sidebar: "skeleton" | "lists" | null;
  /** What an empty run list says. Null when another place already says it. */
  runsEmpty: "recording" | "export" | null;
  /** Set for a blueprint-only session: the sidebar explains it where the runs would be. */
  about: AboutBlueprints | null;
  /** Captured with `include_content=False`. Never set for a blueprint-only session. */
  contentOff: boolean;
  /** Messages about the session as a whole, e.g. files that static analysis skipped. */
  notices: readonly string[];
};

type Input = Pick<ViewerState, "source" | "phase" | "connection"> & {
  data: Pick<
    DataState,
    "session" | "agents" | "agentOrder" | "graphs" | "graphOrder" | "traceOrder"
  >;
};

function aboutOf(data: Input["data"]): AboutBlueprints {
  const sources = new Set<Provenance>();
  for (const id of data.agentOrder) {
    const source = data.agents[id]?.source;
    if (source) sources.add(source);
  }
  for (const id of data.graphOrder) {
    const source = data.graphs[id]?.source;
    if (source) sources.add(source);
  }
  return { origin: sources.size > 1 ? "mixed" : sources.has("code") ? "code" : "object" };
}

/** Pure: everything the shell shows about the session's mode and health. */
export function shellStatus(state: Input): ShellStatus {
  const { source, phase, connection, data } = state;
  const live = source.kind === "live";
  const blueprintOnly = data.session?.mode === "blueprint";
  const hasBlueprints = data.agentOrder.length + data.graphOrder.length > 0;
  const hasRuns = data.traceOrder.length > 0;
  const lost = live && connection === "reconnecting";

  let chip: ModeChip | null = null;
  if (phase === "ready" || (phase === "loading" && live)) {
    if (live && phase === "loading") chip = "connecting";
    else if (blueprintOnly && source.kind !== "none") chip = "blueprint";
    else if (live) chip = lost ? null : "live";
    else if (source.kind === "recording") chip = "recording";
    else if (source.kind === "embedded") chip = "export";
  }

  const ready = phase === "ready";
  return {
    chip,
    banner: lost ? (blueprintOnly ? "server-stopped" : "reconnecting") : null,
    strip: ready && live && !blueprintOnly && hasBlueprints && !hasRuns ? "waiting-for-run" : null,
    sidebar:
      phase === "loading" && live
        ? "skeleton"
        : phase !== "failed" && (hasBlueprints || hasRuns)
          ? "lists"
          : null,
    runsEmpty:
      source.kind === "recording" ? "recording" : source.kind === "embedded" ? "export" : null,
    about: blueprintOnly ? aboutOf(data) : null,
    contentOff: !blueprintOnly && data.session?.include_content === false,
    notices: data.session?.notices ?? [],
  };
}

/**
 * The status for the store's current state. It reads only the parts of the data that change
 * when a blueprint or a run is added, so a stream of spans and deltas does not re-render the
 * shell.
 */
export function useShellStatus(): ShellStatus {
  const source = useViewer((state) => state.source);
  const phase = useViewer((state) => state.phase);
  const connection = useViewer((state) => state.connection);
  const session = useViewer((state) => state.data.session);
  const agents = useViewer((state) => state.data.agents);
  const agentOrder = useViewer((state) => state.data.agentOrder);
  const graphs = useViewer((state) => state.data.graphs);
  const graphOrder = useViewer((state) => state.data.graphOrder);
  const traceOrder = useViewer((state) => state.data.traceOrder);
  return useMemo(
    () =>
      shellStatus({
        source,
        phase,
        connection,
        data: { session, agents, agentOrder, graphs, graphOrder, traceOrder },
      }),
    [source, phase, connection, session, agents, agentOrder, graphs, graphOrder, traceOrder],
  );
}
