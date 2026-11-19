import { afterEach, describe, expect, test, vi } from "vitest";
import { planBoot, readBootSlot, recordingName, startBoot } from "./boot";
import { useViewer } from "./data/store";
import { agentRun, lifecycle, log } from "./test/factory";

/** What an untouched boot slot parses to. Built here so the token never appears in the bundle. */
const UNCONFIGURED = `__PVIZ_${"BOOT"}__`;

function slot(text: string): Document {
  const doc = document.implementation.createHTMLDocument("");
  doc.body.innerHTML = '<script id="pviz-boot" type="application/json"></script>';
  const element = doc.getElementById("pviz-boot");
  if (element) element.textContent = text;
  return doc;
}

afterEach(() => vi.unstubAllGlobals());

describe("readBootSlot", () => {
  test("an untouched slot is a string, a filled one is an object", () => {
    expect(readBootSlot(slot(JSON.stringify(UNCONFIGURED)))).toBe(UNCONFIGURED);
    expect(readBootSlot(slot('{"mode":"embedded","events":[]}'))).toEqual({
      mode: "embedded",
      events: [],
    });
  });

  test("a missing slot is unconfigured; a corrupt one is reported", () => {
    expect(planBoot(readBootSlot(document.implementation.createHTMLDocument("")), "")).toEqual({
      kind: "static",
    });
    expect(planBoot(readBootSlot(slot("{oops")), "")).toEqual({
      kind: "invalid",
      reason: "the boot data is not valid JSON",
    });
  });
});

describe("planBoot", () => {
  test("live boot data wins over the query string", () => {
    const boot = {
      mode: "live",
      events_url: "api/v1/events",
      export_html_url: "api/v1/export.html",
      export_jsonl_url: "api/v1/export.jsonl",
      rerun_url: "api/v1/rerun",
    };
    expect(planBoot(boot, "?src=x.jsonl")).toEqual({
      kind: "live",
      eventsUrl: "api/v1/events",
      exportHtmlUrl: "api/v1/export.html",
      exportJsonlUrl: "api/v1/export.jsonl",
      rerunUrl: "api/v1/rerun",
    });
  });

  test("embedded boot data carries its events", () => {
    const events = log();
    expect(planBoot({ mode: "embedded", events }, "")).toEqual({ kind: "embedded", events });
  });

  test("static mode resolves ?live, then ?src, then the index or drop zone", () => {
    expect(planBoot(UNCONFIGURED, "?live&src=a.jsonl")).toMatchObject({
      kind: "live",
      eventsUrl: "api/v1/events",
      rerunUrl: null,
    });
    expect(planBoot(UNCONFIGURED, "?src=/fixtures/sample.pviz.jsonl")).toEqual({
      kind: "src",
      src: "/fixtures/sample.pviz.jsonl",
    });
    expect(planBoot(UNCONFIGURED, "")).toEqual({ kind: "static" });
  });

  test("unknown boot shapes are invalid, with a reason", () => {
    expect(planBoot({ mode: "telepathy" }, "")).toEqual({
      kind: "invalid",
      reason: 'unknown boot mode "telepathy"',
    });
    expect(planBoot(42, "")).toMatchObject({ kind: "invalid" });
  });
});

test("recordingName is the last path segment, decoded", () => {
  expect(recordingName("/fixtures/sample.pviz.jsonl?x=1")).toBe("sample.pviz.jsonl");
  expect(recordingName("recordings/my%20run.pviz.jsonl")).toBe("my run.pviz.jsonl");
});

describe("startBoot", () => {
  test("embedded events load synchronously as an export", () => {
    const events = log(...lifecycle(agentRun({ id: "root", start: 5, end: 9 })));
    startBoot({ kind: "embedded", events });
    const state = useViewer.getState();
    expect(state).toMatchObject({ phase: "ready", source: { kind: "embedded" }, connection: null });
    expect(state.data.traceOrder).toHaveLength(1);
    expect(state.playback).toEqual({ mode: "paused", t: 9, speed: 1 });
  });

  test("a broken embedded export fails with a reason", () => {
    startBoot({ kind: "embedded", events: [{ seq: 0 }] });
    expect(useViewer.getState()).toMatchObject({
      phase: "failed",
      failure: { source: "this export", line: 1, reason: 'missing string field "type"' },
    });
  });

  test("?src fetches the recording and names it", async () => {
    const body = log()
      .map((e) => JSON.stringify(e))
      .join("\n");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: URL) =>
        String(url).endsWith("index.json") ? new Response("", { status: 404 }) : new Response(body),
      ),
    );
    startBoot({ kind: "src", src: "/fixtures/sample.pviz.jsonl" });
    expect(useViewer.getState().phase).toBe("loading");
    await vi.waitFor(() => expect(useViewer.getState().phase).toBe("ready"));
    expect(useViewer.getState().source).toEqual({ kind: "recording", name: "sample.pviz.jsonl" });
  });

  test("a failed ?src ends in the failed state, not a blank page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 404 })),
    );
    startBoot({ kind: "src", src: "missing.pviz.jsonl" });
    await vi.waitFor(() => expect(useViewer.getState().phase).toBe("failed"));
    expect(useViewer.getState().failure).toEqual({
      source: "missing.pviz.jsonl",
      line: null,
      reason: "the server answered 404",
    });
  });

  test("static mode finds the gallery, or settles on the empty state", async () => {
    const entry = { id: "a", title: "A", description: "First", src: "a.pviz.jsonl" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ recordings: [entry] })),
    );
    startBoot({ kind: "static" });
    await vi.waitFor(() => expect(useViewer.getState().phase).toBe("ready"));
    expect(useViewer.getState()).toMatchObject({ gallery: [entry], source: { kind: "none" } });
  });

  test("live mode opens an event stream, batches events and tracks the connection", async () => {
    const instances: FakeEventSource[] = [];
    class FakeEventSource {
      static readonly CLOSED = 2;
      readyState = 0;
      onopen: (() => void) | null = null;
      onmessage: ((message: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;
      constructor(readonly url: string) {
        instances.push(this);
      }
      close() {
        this.closed = true;
      }
      addEventListener() {}
    }
    vi.stubGlobal("EventSource", FakeEventSource);

    const stop = startBoot({
      kind: "live",
      eventsUrl: "api/v1/events",
      exportHtmlUrl: "api/v1/export.html",
      exportJsonlUrl: null,
      rerunUrl: "api/v1/rerun",
    });
    const es = instances[0];
    expect(es?.url).toBe("api/v1/events");
    expect(useViewer.getState()).toMatchObject({
      phase: "loading",
      connection: "connecting",
      source: { kind: "live", rerunUrl: "api/v1/rerun" },
      playback: { mode: "live" },
    });

    es?.onopen?.();
    expect(useViewer.getState()).toMatchObject({ phase: "ready", connection: "open" });

    for (const envelope of log(...lifecycle(agentRun({ id: "root", start: 5, end: 9 })))) {
      es?.onmessage?.({ data: JSON.stringify(envelope) });
    }
    es?.onmessage?.({ data: "not json" });
    expect(useViewer.getState().data.eventCount).toBe(0);
    await vi.waitFor(() => expect(useViewer.getState().data.eventCount).toBe(3));

    es?.onerror?.();
    expect(useViewer.getState()).toMatchObject({ phase: "ready", connection: "reconnecting" });
    expect(useViewer.getState().data.eventCount).toBe(3);

    stop();
    expect(es?.closed).toBe(true);
  });
});
