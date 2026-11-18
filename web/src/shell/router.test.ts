import { describe, expect, test } from "vitest";
import { initialData } from "../data/reduce";
import {
  agentBlueprint,
  agentRun,
  graphBlueprint,
  lifecycle,
  stateOf,
  TRACE,
} from "../test/factory";
import { formatRoute, homeRoute, navigate, parseHash, type Route } from "./router";

describe("parseHash and formatRoute", () => {
  const cases: [string, Route][] = [
    ["#/", { name: "home" }],
    ["#/gallery", { name: "gallery" }],
    ["#/agents/a1", { name: "agent", id: "a1" }],
    ["#/graphs/g1", { name: "graph", id: "g1" }],
    [`#/runs/${TRACE}`, { name: "run", traceId: TRACE, spanId: null }],
    [
      `#/runs/${TRACE}?span=b7ad6b7169203331`,
      { name: "run", traceId: TRACE, spanId: "b7ad6b7169203331" },
    ],
  ];

  test.each(cases)("%s round-trips", (hash, route) => {
    expect(parseHash(hash)).toEqual(route);
    expect(formatRoute(route)).toBe(hash);
  });

  test("empty and sloppy hashes are home or the same route", () => {
    expect(parseHash("")).toEqual({ name: "home" });
    expect(parseHash("#")).toEqual({ name: "home" });
    expect(parseHash("#/agents/a1/")).toEqual({ name: "agent", id: "a1" });
    expect(parseHash("#/agents/my%20agent")).toEqual({ name: "agent", id: "my agent" });
  });

  test("anything else is unknown, with its path kept for the message", () => {
    expect(parseHash("#/settings")).toEqual({ name: "unknown", path: "/settings" });
    expect(parseHash("#/agents")).toEqual({ name: "unknown", path: "/agents" });
    expect(parseHash("#/runs/a/b")).toEqual({ name: "unknown", path: "/runs/a/b" });
  });
});

describe("navigate", () => {
  test("sets the hash, and replace keeps history length", () => {
    navigate({ name: "agent", id: "a1" });
    expect(window.location.hash).toBe("#/agents/a1");
    const length = window.history.length;
    let announced = 0;
    const listener = () => {
      announced += 1;
    };
    window.addEventListener("hashchange", listener);
    navigate({ name: "run", traceId: TRACE, spanId: "abc" }, { replace: true });
    window.removeEventListener("hashchange", listener);
    expect(window.location.hash).toBe(`#/runs/${TRACE}?span=abc`);
    expect(window.history.length).toBe(length);
    expect(announced).toBe(1);
  });
});

describe("homeRoute", () => {
  const none = { kind: "none" } as const;
  const entry = { id: "a", title: "A", description: "", src: "a.pviz.jsonl" };

  test("the gallery, when there is an index and nothing is loaded", () => {
    expect(homeRoute({ data: initialData, gallery: [entry], source: none })).toEqual({
      name: "gallery",
    });
  });

  test("the latest run, once there is one", () => {
    const other = "b".repeat(32);
    const data = stateOf(
      [0, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1" }) }],
      ...lifecycle(
        agentRun({ id: "r1", start: 1, end: 5 }),
        agentRun({ id: "r2", trace: other, start: 7 }),
      ),
    );
    const source = { kind: "recording", name: "x" } as const;
    expect(homeRoute({ data, gallery: [entry], source })).toEqual({
      name: "run",
      traceId: other,
      spanId: null,
    });
  });

  test("else the first blueprint, agents before graphs", () => {
    const graphOnly = stateOf([0, { type: "blueprint.graph", graph: graphBlueprint("g1", ["a"]) }]);
    expect(homeRoute({ data: graphOnly, gallery: null, source: none })).toEqual({
      name: "graph",
      id: "g1",
    });
    const both = stateOf(
      [0, { type: "blueprint.graph", graph: graphBlueprint("g1", ["a"]) }],
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1" }) }],
    );
    expect(homeRoute({ data: both, gallery: null, source: none })).toEqual({
      name: "agent",
      id: "a1",
    });
  });

  test("else nothing: the empty state stays", () => {
    expect(homeRoute({ data: initialData, gallery: null, source: none })).toBeNull();
  });
});
