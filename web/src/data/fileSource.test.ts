import { afterEach, describe, expect, test, vi } from "vitest";
import { log } from "../test/factory";
import {
  checkEmbedded,
  fetchRecording,
  fetchRecordingIndex,
  parseJsonl,
  RecordingError,
  readRecordingFile,
  recordingUrl,
  sameOriginUrl,
} from "./fileSource";

const lines = (...values: unknown[]): string => values.map((v) => JSON.stringify(v)).join("\n");
const [session] = log();

function failure(run: () => unknown): RecordingError {
  try {
    run();
  } catch (error) {
    if (error instanceof RecordingError) return error;
    throw error;
  }
  throw new Error("expected a RecordingError");
}

afterEach(() => vi.unstubAllGlobals());

describe("parseJsonl", () => {
  test("reads one envelope per line and tolerates blank lines and CRLF", () => {
    const text = `${JSON.stringify(session)}\r\n\r\n{"seq":1,"ts":2,"type":"future.event"}\n`;
    const parsed = parseJsonl(text, "a.pviz.jsonl");
    expect(parsed).toHaveLength(2);
    expect(parsed[1]).toMatchObject({ seq: 1, type: "future.event" });
  });

  test("reports the line of invalid JSON", () => {
    const error = failure(() => parseJsonl(`${JSON.stringify(session)}\n{"seq": 1,`, "bad.jsonl"));
    expect(error).toMatchObject({ source: "bad.jsonl", line: 2, message: "not valid JSON" });
  });

  test("recognises an HTML page served in place of a recording", () => {
    const error = failure(() => parseJsonl("<!doctype html><html></html>", "x"));
    expect(error.message).toContain("looks like HTML");
    expect(error.line).toBe(1);
  });

  test("rejects lines that are not envelopes", () => {
    expect(failure(() => parseJsonl(lines(session, [1, 2]), "x")).message).toBe(
      "expected a JSON object",
    );
    expect(failure(() => parseJsonl(lines(session, { seq: 1, ts: 1 }), "x"))).toMatchObject({
      line: 2,
      message: 'missing string field "type"',
    });
  });

  test("requires session.started first, at a wire version it can read", () => {
    const span = { seq: 0, ts: 1, type: "span.started", span: {} };
    expect(failure(() => parseJsonl(lines(span), "x")).message).toBe(
      'the first event must be "session.started", found "span.started"',
    );
    const newer = { ...session, pviz: 2 };
    expect(failure(() => parseJsonl(lines(newer), "x")).message).toBe(
      "unsupported wire version 2 (this viewer reads pviz 1)",
    );
  });

  test("an empty file is an error without a line", () => {
    expect(failure(() => parseJsonl("\n\n", "empty.jsonl"))).toMatchObject({
      line: null,
      message: "the file is empty",
    });
  });
});

describe("checkEmbedded", () => {
  test("accepts an envelope array and rejects anything else", () => {
    expect(checkEmbedded([session], "this export")).toHaveLength(1);
    expect(failure(() => checkEmbedded("nope", "this export")).message).toBe(
      "embedded events are not a list",
    );
    expect(failure(() => checkEmbedded([], "this export")).message).toBe(
      "the export holds no events",
    );
    expect(failure(() => checkEmbedded([session, 3], "this export")).line).toBe(2);
  });
});

describe("fetching", () => {
  const base = "http://localhost:5173/demo/index.html";

  test("only same-origin recording URLs are followed", async () => {
    expect(sameOriginUrl("recordings/a.pviz.jsonl", base)?.href).toBe(
      "http://localhost:5173/demo/recordings/a.pviz.jsonl",
    );
    expect(sameOriginUrl("https://evil.example/x.jsonl", base)).toBeNull();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchRecording("//evil.example/x.jsonl", base)).rejects.toMatchObject({
      message: "recordings can only be loaded from this page's origin",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("fetchRecording parses the body and names HTTP failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(session))),
    );
    await expect(fetchRecording("a.pviz.jsonl", base)).resolves.toHaveLength(1);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 404 })),
    );
    await expect(fetchRecording("a.pviz.jsonl", base)).rejects.toMatchObject({
      source: "a.pviz.jsonl",
      message: "the server answered 404",
    });

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    await expect(fetchRecording("a.pviz.jsonl", base)).rejects.toBeInstanceOf(RecordingError);
  });

  test("readRecordingFile uses the file name as the source", async () => {
    const good = new File([JSON.stringify(session)], "run.pviz.jsonl");
    await expect(readRecordingFile(good)).resolves.toHaveLength(1);
    const bad = new File(["PNG..."], "photo.png");
    await expect(readRecordingFile(bad)).rejects.toMatchObject({ source: "photo.png", line: 1 });
  });

  test("the recording index is optional: every failure is null", async () => {
    const entry = { id: "a", title: "A", description: "First", src: "a.pviz.jsonl" };
    const fetchMock = vi.fn(async (_url: URL) => Response.json({ recordings: [entry, { id: 3 }] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchRecordingIndex(base)).resolves.toEqual({ recordings: [entry] });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://localhost:5173/demo/recordings/index.json",
    );

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>", { status: 200 })),
    );
    await expect(fetchRecordingIndex(base)).resolves.toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 404 })),
    );
    await expect(fetchRecordingIndex(base)).resolves.toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ recordings: [] })),
    );
    await expect(fetchRecordingIndex(base)).resolves.toBeNull();

    const untouched = vi.fn();
    vi.stubGlobal("fetch", untouched);
    await expect(fetchRecordingIndex("file:///C:/exports/run.html")).resolves.toBeNull();
    expect(untouched).not.toHaveBeenCalled();
  });

  test("index entries resolve relative to the recordings directory", () => {
    const entry = { id: "a", title: "A", description: "", src: "a.pviz.jsonl" };
    expect(recordingUrl(entry, base)).toBe("http://localhost:5173/demo/recordings/a.pviz.jsonl");
  });
});
