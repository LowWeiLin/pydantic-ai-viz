/**
 * Boot: decides where the viewer's events come from.
 *
 * Python fills the `pviz-boot` slot in `viewer.html` with JSON. An untouched slot parses to a
 * plain string, which means "static mode": the hosted demo, `pnpm dev`, or a bare viewer file.
 * Static mode resolves in this order: `?live`, `?src=<url>`, `recordings/index.json`, drop zone.
 */
import {
  checkEmbedded,
  fetchRecording,
  fetchRecordingIndex,
  RecordingError,
} from "./data/fileSource";
import { startLiveSource } from "./data/liveSource";
import { useViewer } from "./data/store";

export type BootPlan =
  | {
      kind: "live";
      eventsUrl: string;
      exportHtmlUrl: string | null;
      exportJsonlUrl: string | null;
      rerunUrl: string | null;
    }
  | { kind: "embedded"; events: unknown }
  | { kind: "src"; src: string }
  | { kind: "static" }
  | { kind: "invalid"; reason: string };

const BOOT_ELEMENT_ID = "pviz-boot";

/** Reads and parses the boot slot. A missing or unparsable slot counts as unconfigured. */
export function readBootSlot(doc: Document = document): unknown {
  const text = doc.getElementById(BOOT_ELEMENT_ID)?.textContent;
  if (!text) return "";
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { mode: "unreadable" };
  }
}

const stringOrNull = (value: unknown): string | null => (typeof value === "string" ? value : null);

/** Pure: turns the boot value and the page's query string into a plan. */
export function planBoot(boot: unknown, search: string): BootPlan {
  if (typeof boot !== "string") {
    if (typeof boot !== "object" || boot === null) {
      return { kind: "invalid", reason: "the boot data is not an object" };
    }
    const config = boot as Record<string, unknown>;
    if (config.mode === "live") {
      return {
        kind: "live",
        eventsUrl: stringOrNull(config.events_url) ?? "api/v1/events",
        exportHtmlUrl: stringOrNull(config.export_html_url),
        exportJsonlUrl: stringOrNull(config.export_jsonl_url),
        rerunUrl: stringOrNull(config.rerun_url),
      };
    }
    if (config.mode === "embedded") return { kind: "embedded", events: config.events };
    return {
      kind: "invalid",
      reason:
        config.mode === "unreadable"
          ? "the boot data is not valid JSON"
          : `unknown boot mode ${JSON.stringify(config.mode)}`,
    };
  }

  const params = new URLSearchParams(search);
  if (params.has("live")) {
    return {
      kind: "live",
      eventsUrl: "api/v1/events",
      exportHtmlUrl: "api/v1/export.html",
      exportJsonlUrl: "api/v1/export.jsonl",
      rerunUrl: null,
    };
  }
  const src = params.get("src");
  if (src) return { kind: "src", src };
  return { kind: "static" };
}

/** Short display name for a recording URL: its last path segment. */
export function recordingName(src: string): string {
  const path = src.split(/[?#]/)[0] ?? src;
  const last = path.split("/").filter(Boolean).at(-1) ?? src;
  try {
    return decodeURIComponent(last);
  } catch {
    return last;
  }
}

const asRecordingError = (error: unknown, source: string): RecordingError =>
  error instanceof RecordingError ? error : new RecordingError(source, String(error));

/** Fetches a recording into the store, reporting failure through the store as well. */
export async function loadRecordingFromUrl(src: string): Promise<void> {
  const store = useViewer.getState();
  store.setLoading();
  try {
    const envelopes = await fetchRecording(src);
    useViewer.getState().openRecording({ kind: "recording", name: recordingName(src) }, envelopes);
  } catch (error) {
    useViewer.getState().fail(asRecordingError(error, src));
  }
}

/** The gallery is optional and never blocks: look for it in the background. */
function discoverGallery(): Promise<boolean> {
  return fetchRecordingIndex().then((index) => {
    useViewer.getState().setGallery(index ? index.recordings : null);
    return index !== null;
  });
}

/** Carries out a plan against the store. Returns a function that stops any live connection. */
export function startBoot(plan: BootPlan): () => void {
  const store = useViewer.getState();
  switch (plan.kind) {
    case "live": {
      store.openLive({
        kind: "live",
        exportHtmlUrl: plan.exportHtmlUrl,
        exportJsonlUrl: plan.exportJsonlUrl,
        rerunUrl: plan.rerunUrl,
      });
      return startLiveSource(plan.eventsUrl, {
        onEvents: (envelopes) => useViewer.getState().ingest(envelopes),
        onConnection: (connection) => useViewer.getState().setConnection(connection),
      });
    }
    case "embedded": {
      try {
        store.openRecording({ kind: "embedded" }, checkEmbedded(plan.events, "this export"));
      } catch (error) {
        store.fail(asRecordingError(error, "this export"));
      }
      break;
    }
    case "src": {
      void loadRecordingFromUrl(plan.src);
      void discoverGallery();
      break;
    }
    case "static": {
      store.setLoading();
      void discoverGallery().then(() => useViewer.getState().openEmpty());
      break;
    }
    case "invalid": {
      store.fail(new RecordingError("this page", plan.reason));
      break;
    }
  }
  return () => {};
}
