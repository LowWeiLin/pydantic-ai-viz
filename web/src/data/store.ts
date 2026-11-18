/**
 * The viewer's single store. `data` is the reduced event log; everything else is the small
 * amount of state that is not derivable from it: where the events come from, whether that
 * source is healthy, and the playback clock.
 *
 * Routing and selection live in the URL hash (see `shell/router.ts`), not here.
 */
import { create } from "zustand";
import type { Envelope, RecordingEntry } from "../wire";
import type { RecordingError } from "./fileSource";
import type { Connection } from "./liveSource";
import { livePlayback, type Playback } from "./playback";
import { type DataState, initialData, reduceAll } from "./reduce";

export type Source =
  /** Static mode with nothing loaded yet: the gallery or the drop zone. */
  | { kind: "none" }
  | {
      kind: "live";
      exportHtmlUrl: string | null;
      exportJsonlUrl: string | null;
      /** Set in demo mode only: the "Run again" endpoint. */
      rerunUrl: string | null;
    }
  /** A single-file HTML export with its events inside. */
  | { kind: "embedded" }
  /** A `.pviz.jsonl` that was fetched or dropped. */
  | { kind: "recording"; name: string };

export type Phase = "loading" | "ready" | "failed";

export type Failure = { source: string; line: number | null; reason: string };

export type ViewerState = {
  data: DataState;
  source: Source;
  phase: Phase;
  /** Set when `phase` is "failed". */
  failure: Failure | null;
  /** Live mode only. */
  connection: Connection | null;
  /** Entries of `recordings/index.json`, when the page has one. */
  gallery: readonly RecordingEntry[] | null;
  playback: Playback;

  /** Appends live events. */
  ingest: (envelopes: readonly Envelope[]) => void;
  /** Switches to live mode and waits for the first connection. */
  openLive: (source: Extract<Source, { kind: "live" }>) => void;
  /** Replaces everything with a recording or an embedded export. */
  openRecording: (
    source: Extract<Source, { kind: "embedded" | "recording" }>,
    envelopes: readonly Envelope[],
  ) => void;
  /** Static mode with nothing to show yet. */
  openEmpty: () => void;
  setLoading: () => void;
  fail: (error: RecordingError) => void;
  setConnection: (connection: Connection) => void;
  setGallery: (entries: readonly RecordingEntry[] | null) => void;
  /** Applies one of the pure transitions from `playback.ts`. */
  updatePlayback: (transition: (playback: Playback) => Playback) => void;
};

type Values = Omit<
  ViewerState,
  | "ingest"
  | "openLive"
  | "openRecording"
  | "openEmpty"
  | "setLoading"
  | "fail"
  | "setConnection"
  | "setGallery"
  | "updatePlayback"
>;

const initialValues: Values = {
  data: initialData,
  source: { kind: "none" },
  phase: "loading",
  failure: null,
  connection: null,
  gallery: null,
  playback: livePlayback,
};

export const useViewer = create<ViewerState>()((set) => ({
  ...initialValues,

  ingest: (envelopes) =>
    set((state) => {
      const data = reduceAll(state.data, envelopes);
      return data === state.data ? state : { data };
    }),

  openLive: (source) =>
    set({
      source,
      data: initialData,
      phase: "loading",
      failure: null,
      connection: "connecting",
      playback: livePlayback,
    }),

  openRecording: (source, envelopes) =>
    set(() => {
      const data = reduceAll(initialData, envelopes);
      return {
        source,
        data,
        phase: "ready",
        failure: null,
        connection: null,
        // Held at the end until a run view starts its replay.
        playback: { mode: "paused", t: data.lastTs, speed: 1 },
      };
    }),

  openEmpty: () => set({ source: { kind: "none" }, phase: "ready", failure: null }),

  setLoading: () => set({ phase: "loading", failure: null }),

  fail: (error) =>
    set({
      phase: "failed",
      failure: { source: error.source, line: error.line, reason: error.message },
    }),

  setConnection: (connection) =>
    set((state) => ({
      connection,
      // The first successful connection ends the skeleton; later drops keep what is on screen.
      phase: connection === "open" && state.phase === "loading" ? "ready" : state.phase,
    })),

  setGallery: (entries) => set({ gallery: entries }),

  updatePlayback: (transition) =>
    set((state) => {
      const playback = transition(state.playback);
      return playback === state.playback ? state : { playback };
    }),
}));

/** Puts the store back to its boot state. For tests. */
export function resetViewer(): void {
  useViewer.setState(initialValues);
}
