// @vitest-environment node
import { describe, expect, test } from "vitest";
import type { RunSummary } from "../data/selectors";
import { followTarget, nothingSeen, type Seen, seenOf } from "./follow";
import type { Route } from "./router";

const run = (traceId: string, status: RunSummary["status"] = "running"): RunSummary => ({
  traceId,
  rootSpanId: "root",
  name: "triage",
  kind: "agent_run",
  status,
  startMs: 0,
  endMs: status === "running" ? null : 10,
  durationMs: 10,
  spanCount: 1,
  usage: { input_tokens: 0, output_tokens: 0 },
});

const onRun = (traceId: string, spanId: string | null = null): Route => ({
  name: "run",
  traceId,
  spanId,
});
const onAgent: Route = { name: "agent", id: "a1" };
const watching = { live: true, pinned: true };
const afterOne: Seen = { newest: "t1", count: 1 };

describe("followTarget", () => {
  test("waiting on a blueprint, the session's first run is opened", () => {
    expect(followTarget(nothingSeen, [run("t1")], { ...watching, route: onAgent })).toBe("t1");
    expect(followTarget(nothingSeen, [run("t1")], { ...watching, route: { name: "home" } })).toBe(
      "t1",
    );
    expect(
      followTarget(nothingSeen, [run("t1")], { ...watching, route: { name: "graph", id: "g1" } }),
    ).toBe("t1");
  });

  test("reading a blueprint after runs have happened is not interrupted", () => {
    const list = [run("t2"), run("t1", "ok")];
    expect(followTarget(afterOne, list, { ...watching, route: onAgent })).toBeNull();
  });

  test("watching a run, the next run takes over; with a span selected it does not", () => {
    const list = [run("t2"), run("t1", "ok")];
    expect(followTarget(afterOne, list, { ...watching, route: onRun("t1") })).toBe("t2");
    expect(followTarget(afterOne, list, { ...watching, route: onRun("t1", "s1") })).toBeNull();
    // Already there (the `#/` redirect got to it first).
    expect(followTarget(afterOne, list, { ...watching, route: onRun("t2") })).toBeNull();
  });

  test("a paused or scrubbed view stays where it is", () => {
    const list = [run("t2"), run("t1", "ok")];
    expect(
      followTarget(afterOne, list, { live: true, pinned: false, route: onRun("t1") }),
    ).toBeNull();
  });

  test("recordings never follow", () => {
    expect(
      followTarget(nothingSeen, [run("t1")], { live: false, pinned: true, route: onAgent }),
    ).toBeNull();
  });

  test("history is not news: finished runs in a backlog do not move a deep link", () => {
    const backlog = [run("t2", "ok"), run("t1", "error")];
    expect(followTarget(nothingSeen, backlog, { ...watching, route: onAgent })).toBeNull();
    expect(followTarget(nothingSeen, backlog, { ...watching, route: onRun("t1") })).toBeNull();
  });

  test("the same newest run is followed once, not on every event", () => {
    const list = [run("t2"), run("t1", "ok")];
    expect(followTarget(seenOf(list), list, { ...watching, route: onRun("t1") })).toBeNull();
  });

  test("the gallery, unknown pages and an empty session stay put", () => {
    expect(
      followTarget(nothingSeen, [run("t1")], { ...watching, route: { name: "gallery" } }),
    ).toBeNull();
    expect(
      followTarget(nothingSeen, [run("t1")], {
        ...watching,
        route: { name: "unknown", path: "/x" },
      }),
    ).toBeNull();
    expect(followTarget(nothingSeen, [], { ...watching, route: onAgent })).toBeNull();
    expect(seenOf([])).toEqual(nothingSeen);
  });
});
