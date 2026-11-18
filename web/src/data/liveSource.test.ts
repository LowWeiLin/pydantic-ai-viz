import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { log, sessionStarted } from "../test/factory";
import type { Envelope } from "../wire";
import { type Connection, startLiveSource } from "./liveSource";

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  close(): void {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
  send(envelope: Envelope | string): void {
    this.onmessage?.({ data: typeof envelope === "string" ? envelope : JSON.stringify(envelope) });
  }
}

const latest = (): FakeEventSource => {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error("no EventSource was opened");
  return source;
};

let frames: FrameRequestCallback[] = [];
const runFrame = (): void => {
  const due = frames;
  frames = [];
  for (const callback of due) callback(0);
};

function start() {
  const batches: Envelope[][] = [];
  const states: Connection[] = [];
  const stop = startLiveSource("api/v1/events", {
    onEvents: (batch) => batches.push(batch),
    onConnection: (state) => states.push(state),
  });
  return { batches, states, stop };
}

const events = log(
  [1, sessionStarted("a".repeat(32))],
  [2, sessionStarted("b".repeat(32))],
  [3, sessionStarted("c".repeat(32))],
);
const [first, second, third] = events as [Envelope, Envelope, Envelope];

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  frames = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", () => {
    frames = [];
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("startLiveSource", () => {
  test("reports connecting, then open", () => {
    const { states } = start();
    expect(latest().url).toBe("api/v1/events");
    expect(states).toEqual(["connecting"]);
    latest().onopen?.();
    expect(states).toEqual(["connecting", "open"]);
  });

  test("a burst of events is handed over once, on the next frame, in order", () => {
    const { batches } = start();
    latest().send(first);
    latest().send(second);
    latest().send(third);
    expect(batches).toEqual([]);
    runFrame();
    expect(batches).toEqual([[first, second, third]]);
    // Nothing is delivered twice when the fallback timer comes round.
    vi.advanceTimersByTime(1000);
    expect(batches).toHaveLength(1);
  });

  test("without frames (a background tab) a batch still arrives within a quarter second", () => {
    const { batches } = start();
    latest().send(first);
    vi.advanceTimersByTime(249);
    expect(batches).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(batches).toEqual([[first]]);
    latest().send(second);
    vi.advanceTimersByTime(250);
    expect(batches).toEqual([[first], [second]]);
  });

  test("a malformed frame is skipped and the rest still arrive", () => {
    const { batches } = start();
    latest().send("{not json");
    runFrame();
    expect(batches).toEqual([]);
    latest().send("{oops");
    latest().send(first);
    runFrame();
    expect(batches).toEqual([[first]]);
  });

  test("a dropped connection is left to the browser, which resumes with Last-Event-ID", () => {
    const { states } = start();
    const source = latest();
    source.onopen?.();
    source.readyState = FakeEventSource.CONNECTING;
    source.onerror?.();
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(10_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(source.closed).toBe(false);
    source.onopen?.();
    expect(states.at(-1)).toBe("open");
  });

  test("a stream closed for good is reopened after two seconds, and keeps trying", () => {
    const { states, batches } = start();
    const source = latest();
    source.readyState = FakeEventSource.CLOSED;
    source.onerror?.();
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(1999);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(2);

    const again = latest();
    again.readyState = FakeEventSource.CLOSED;
    again.onerror?.();
    vi.advanceTimersByTime(2000);
    expect(FakeEventSource.instances).toHaveLength(3);

    latest().onopen?.();
    latest().send(first);
    runFrame();
    expect(states.at(-1)).toBe("open");
    expect(batches).toEqual([[first]]);
  });

  test("stopping closes the stream, drops what is queued and cancels a pending reopen", () => {
    const { batches, states, stop } = start();
    const source = latest();
    source.send(first);
    stop();
    expect(source.closed).toBe(true);
    runFrame();
    vi.advanceTimersByTime(5000);
    expect(batches).toEqual([]);

    const other = start();
    const failing = latest();
    failing.readyState = FakeEventSource.CLOSED;
    failing.onerror?.();
    other.stop();
    const opened = FakeEventSource.instances.length;
    vi.advanceTimersByTime(5000);
    expect(FakeEventSource.instances).toHaveLength(opened);
    // Errors after stopping are not reported.
    const before = states.length;
    source.onerror?.();
    expect(states).toHaveLength(before);
  });
});
