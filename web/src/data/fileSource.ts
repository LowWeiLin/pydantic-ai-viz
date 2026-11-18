/**
 * Recordings: `.pviz.jsonl` text from a fetch, a dropped file or an embedded array.
 * Parsing is strict about shape (so a wrong file fails with a line number) and lenient about
 * content (unknown event types and fields pass through; the reducer ignores them).
 */
import type { Envelope, RecordingEntry, RecordingIndex } from "../wire";
import { PVIZ_VERSION } from "../wire";

/** Why a recording could not be loaded: shown verbatim in the "failed to load" state. */
export class RecordingError extends Error {
  /** File name or URL as the user knows it. */
  readonly source: string;
  /** 1-based line of the problem, when there is one. */
  readonly line: number | null;

  constructor(source: string, reason: string, line: number | null = null) {
    super(reason);
    this.name = "RecordingError";
    this.source = source;
    this.line = line;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkEnvelope(value: unknown): string | null {
  if (!isObject(value)) return "expected a JSON object";
  if (typeof value.type !== "string") return 'missing string field "type"';
  if (typeof value.seq !== "number") return 'missing number field "seq"';
  if (typeof value.ts !== "number") return 'missing number field "ts"';
  return null;
}

function checkFirst(value: Envelope): string | null {
  if (value.type !== "session.started") {
    return `the first event must be "session.started", found "${value.type}"`;
  }
  if (value.pviz !== PVIZ_VERSION) {
    return `unsupported wire version ${JSON.stringify(value.pviz)} (this viewer reads pviz ${PVIZ_VERSION})`;
  }
  return null;
}

/** Parses `.pviz.jsonl` text. Blank lines are allowed; anything else must be an envelope. */
export function parseJsonl(text: string, source: string): Envelope[] {
  const envelopes: Envelope[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = (lines[i] ?? "").trim();
    if (line === "") continue;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      const hint = line.startsWith("<") ? " (this looks like HTML, not a recording)" : "";
      throw new RecordingError(source, `not valid JSON${hint}`, i + 1);
    }
    const problem =
      checkEnvelope(value) ?? (envelopes.length === 0 ? checkFirst(value as Envelope) : null);
    if (problem) throw new RecordingError(source, problem, i + 1);
    envelopes.push(value as Envelope);
  }
  if (envelopes.length === 0) throw new RecordingError(source, "the file is empty");
  return envelopes;
}

/** Validates an embedded event array (boot mode "embedded"). */
export function checkEmbedded(events: unknown, source: string): Envelope[] {
  if (!Array.isArray(events)) throw new RecordingError(source, "embedded events are not a list");
  events.forEach((value, i) => {
    const problem = checkEnvelope(value) ?? (i === 0 ? checkFirst(value as Envelope) : null);
    if (problem) throw new RecordingError(source, problem, i + 1);
  });
  if (events.length === 0) throw new RecordingError(source, "the export holds no events");
  return events as Envelope[];
}

/** A recording URL is only followed when it stays on the page's own origin. */
export function sameOriginUrl(src: string, base: string): URL | null {
  try {
    const url = new URL(src, base);
    return url.origin === new URL(base).origin ? url : null;
  } catch {
    return null;
  }
}

export async function fetchRecording(
  src: string,
  base: string = window.location.href,
): Promise<Envelope[]> {
  const url = sameOriginUrl(src, base);
  if (!url) throw new RecordingError(src, "recordings can only be loaded from this page's origin");
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store" });
  } catch {
    throw new RecordingError(src, "the request failed (is the file reachable?)");
  }
  if (!response.ok) throw new RecordingError(src, `the server answered ${response.status}`);
  return parseJsonl(await response.text(), src);
}

export async function readRecordingFile(file: File): Promise<Envelope[]> {
  let text: string;
  try {
    text = await file.text();
  } catch {
    throw new RecordingError(file.name, "the file could not be read");
  }
  return parseJsonl(text, file.name);
}

function isEntry(value: unknown): value is RecordingEntry {
  return (
    isObject(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.description === "string" &&
    typeof value.src === "string"
  );
}

/**
 * Looks for `recordings/index.json` next to the page. Absence is the normal case (a plain
 * export or dev build), so every failure resolves to null instead of throwing.
 */
export async function fetchRecordingIndex(
  base: string = window.location.href,
): Promise<RecordingIndex | null> {
  if (!/^https?:/.test(base)) return null; // file:// pages cannot fetch their neighbours.
  try {
    const response = await fetch(new URL("recordings/index.json", base), { cache: "no-store" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (!isObject(body) || !Array.isArray(body.recordings)) return null;
    const recordings = body.recordings.filter(isEntry);
    return recordings.length > 0 ? { recordings } : null;
  } catch {
    return null;
  }
}

/** Resolves an index entry's `src` (relative to the index file) to a URL the page can fetch. */
export function recordingUrl(entry: RecordingEntry, base: string = window.location.href): string {
  return new URL(entry.src, new URL("recordings/", base)).href;
}
