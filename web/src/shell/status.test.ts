// @vitest-environment node
/**
 * The table at the top of `status.ts`, one row per test: for every state of a session, what
 * the shell shows and where. If a row here changes, the table changes with it.
 */
import { describe, expect, test } from "vitest";
import type { Connection } from "../data/liveSource";
import { initialData } from "../data/reduce";
import type { Phase, Source } from "../data/store";
import {
  agentBlueprint,
  agentRun,
  graphBlueprint,
  lifecycle,
  sessionStarted,
  stateOf,
} from "../test/factory";
import type { Event, SessionStarted } from "../wire";
import { type ShellStatus, shellStatus } from "./status";

const LIVE: Source = {
  kind: "live",
  exportHtmlUrl: "e.html",
  exportJsonlUrl: "e.jsonl",
  rerunUrl: null,
};
const RECORDING: Source = { kind: "recording", name: "sample.pviz.jsonl" };
const EMBEDDED: Source = { kind: "embedded" };

const session = (fields: Partial<SessionStarted> = {}): [number, Event] => [
  0,
  { ...sessionStarted(), ...fields },
];
const blueprint = (
  id: string,
  fields: Parameters<typeof agentBlueprint>[0] = { id },
): [number, Event] => [1, { type: "blueprint.agent", agent: agentBlueprint({ ...fields, id }) }];
const oneRun = lifecycle(agentRun({ id: "root", start: 10, end: 20 }));

function status(
  source: Source,
  events: [number, Event][],
  phase: Phase = "ready",
  connection: Connection | null = source.kind === "live" ? "open" : null,
): ShellStatus {
  // No events at all is a page that has received nothing, not even the session.
  const data = events.length === 0 ? initialData : stateOf(...events);
  return shellStatus({ source, phase, connection, data });
}

/** The parts of the status that are words on screen. */
const shown = ({ chip, banner, strip, sidebar, runsEmpty }: ShellStatus) => ({
  chip,
  banner,
  strip,
  sidebar,
  runsEmpty,
});

describe("what the shell says, by state", () => {
  test("nothing loaded: no chip, no sidebar", () => {
    const empty = status({ kind: "none" }, []);
    expect(shown(empty)).toEqual({
      chip: null,
      banner: null,
      strip: null,
      sidebar: null,
      runsEmpty: null,
    });
    expect(empty.about).toBeNull();
  });

  test("a recording that is still being fetched says nothing yet", () => {
    expect(shown(status(RECORDING, [session()], "loading")).chip).toBeNull();
  });

  test("live, first connection: 'Connecting' and the skeleton", () => {
    expect(shown(status(LIVE, [], "loading", "connecting"))).toEqual({
      chip: "connecting",
      banner: null,
      strip: null,
      sidebar: "skeleton",
      runsEmpty: null,
    });
  });

  test("live with nothing in it: 'Live', and no empty lists beside the welcome screen", () => {
    expect(shown(status(LIVE, [session()]))).toEqual({
      chip: "live",
      banner: null,
      strip: null,
      sidebar: null,
      runsEmpty: null,
    });
  });

  test("live with a blueprint and no run: the strip waits, the run list stays silent", () => {
    expect(shown(status(LIVE, [session(), blueprint("a1")]))).toEqual({
      chip: "live",
      banner: null,
      strip: "waiting-for-run",
      sidebar: "lists",
      runsEmpty: null,
    });
  });

  test("live with runs: 'Live' and nothing else", () => {
    expect(shown(status(LIVE, [session(), blueprint("a1"), ...oneRun]))).toEqual({
      chip: "live",
      banner: null,
      strip: null,
      sidebar: "lists",
      runsEmpty: null,
    });
  });

  test("live, connection lost: the banner says so and nothing claims to be live", () => {
    const lost = status(LIVE, [session(), blueprint("a1"), ...oneRun], "ready", "reconnecting");
    expect(shown(lost)).toMatchObject({ chip: null, banner: "reconnecting", sidebar: "lists" });
    // What was waiting is still waiting: the strip does not flicker with the connection.
    const waiting = status(LIVE, [session(), blueprint("a1")], "ready", "reconnecting");
    expect(shown(waiting)).toMatchObject({ banner: "reconnecting", strip: "waiting-for-run" });
  });

  describe("blueprint-only", () => {
    const fromCode = blueprint("a1", {
      id: "a1",
      source: "code",
      location: { file: "pkg/desk.py", line: 3 },
    });
    const view = [session({ mode: "blueprint", notices: ["skipped legacy.py"] }), fromCode];

    test("served by `view`: 'Blueprint', never live, connected or waiting", () => {
      const served = status(LIVE, view);
      expect(shown(served)).toEqual({
        chip: "blueprint",
        banner: null,
        strip: null,
        sidebar: "lists",
        runsEmpty: null,
      });
      expect(served.about).toEqual({ origin: "code" });
      expect(served.notices).toEqual(["skipped legacy.py"]);
    });

    test("the server behind it stops: a quiet banner, and it is still a blueprint", () => {
      expect(shown(status(LIVE, view, "ready", "reconnecting"))).toMatchObject({
        chip: "blueprint",
        banner: "server-stopped",
        strip: null,
      });
    });

    test("in a file (`view --export`): the same, with no connection to speak of", () => {
      for (const source of [EMBEDDED, RECORDING]) {
        const file = status(source, view);
        expect(shown(file)).toMatchObject({ chip: "blueprint", banner: null, strip: null });
        expect(file.about).toEqual({ origin: "code" });
      }
    });

    test("how the blueprints were read: source code, live objects, or both", () => {
      const imported = blueprint("a2", { id: "a2", source: "object" });
      const graph: [number, Event] = [
        2,
        { type: "blueprint.graph", graph: { ...graphBlueprint("g1", ["a", "b"]), source: "code" } },
      ];
      const mode = session({ mode: "blueprint" });
      expect(status(EMBEDDED, [mode, fromCode, graph]).about).toEqual({ origin: "code" });
      expect(status(EMBEDDED, [mode, imported]).about).toEqual({ origin: "object" });
      expect(status(EMBEDDED, [mode, fromCode, imported]).about).toEqual({ origin: "mixed" });
      // A graph counts like an agent.
      expect(status(EMBEDDED, [mode, imported, graph]).about).toEqual({ origin: "mixed" });
    });

    test("content capture is not its subject, whatever the flag says", () => {
      const off = session({ mode: "blueprint", include_content: false });
      expect(status(EMBEDDED, [off, fromCode]).contentOff).toBe(false);
    });
  });

  test("a recording: 'Recording'; an empty run list says there are none in it", () => {
    expect(shown(status(RECORDING, [session(), blueprint("a1")]))).toEqual({
      chip: "recording",
      banner: null,
      strip: null,
      sidebar: "lists",
      runsEmpty: "recording",
    });
  });

  test("an export: 'Export'; an empty run list says there are none in it", () => {
    expect(shown(status(EMBEDDED, [session(), blueprint("a1"), ...oneRun]))).toEqual({
      chip: "export",
      banner: null,
      strip: null,
      sidebar: "lists",
      runsEmpty: "export",
    });
  });

  test("a failed load shows no mode and no lists", () => {
    expect(shown(status(RECORDING, [session(), blueprint("a1")], "failed"))).toMatchObject({
      chip: null,
      sidebar: null,
    });
  });

  test("content off is said for a capturing session, and notices default to none", () => {
    const captured = status(EMBEDDED, [session({ include_content: false }), blueprint("a1")]);
    expect(captured.contentOff).toBe(true);
    expect(captured.about).toBeNull();
    expect(captured.notices).toEqual([]);
  });

  test("a recording made before sessions had a mode is a capturing session", () => {
    const { mode: _mode, notices: _notices, ...old } = sessionStarted();
    const legacy = status(EMBEDDED, [[0, old as SessionStarted], blueprint("a1")]);
    expect(shown(legacy)).toMatchObject({ chip: "export" });
    expect(legacy.about).toBeNull();
    expect(legacy.notices).toEqual([]);
  });
});
