import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { App } from "./App";
import type { BootPlan } from "./boot";
import { seek } from "./data/playback";
import { runBounds, runs } from "./data/selectors";
import { useViewer } from "./data/store";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { recordingHref } from "./shell/Gallery";
import { Workspace } from "./shell/Workspace";
import {
  agentBlueprint,
  agentRun,
  ended,
  fixture,
  fixtureText,
  log,
  modelRequest,
  sessionStarted,
  started,
  TRACE,
} from "./test/factory";
import { TooltipProvider } from "./ui/overlays";
import type { Event } from "./wire";

const embedded = (name: string): BootPlan => ({ kind: "embedded", events: fixture(name) });

/** The recording index is looked up in static mode; answer "none" unless a test says otherwise. */
function stubFetch(handler: (url: string) => Response = () => new Response("", { status: 404 })) {
  const mock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

type FakeSource = { onopen?: () => void; onerror?: () => void };

/** Replaces EventSource with one the test drives; returns the instances created. */
function stubEventSource(): FakeSource[] {
  const sources: FakeSource[] = [];
  class FakeEventSource {
    static readonly CLOSED = 2;
    readyState = 1;
    onopen?: () => void;
    onerror?: () => void;
    onmessage?: (m: { data: string }) => void;
    constructor() {
      sources.push(this);
    }
    close() {}
  }
  vi.stubGlobal("EventSource", FakeEventSource);
  return sources;
}

const livePlan: BootPlan = {
  kind: "live",
  eventsUrl: "api/v1/events",
  exportHtmlUrl: "api/v1/export.html",
  exportJsonlUrl: "api/v1/export.jsonl",
  rerunUrl: "api/v1/rerun",
};

function Broken(): never {
  throw new TypeError("span.data is undefined");
}

/** Opens a run by its hash and waits for its screen (the route change is asynchronous). */
async function openRun(traceId: string | undefined, name: string): Promise<void> {
  act(() => {
    window.location.hash = `#/runs/${traceId}`;
  });
  await screen.findByRole("heading", { level: 2, name });
}

const skipToEnd = async (user: ReturnType<typeof userEvent.setup>) =>
  user.click(await screen.findByRole("button", { name: "Skip to end" }));

beforeEach(() => {
  stubFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("boot modes and states", () => {
  test("static mode with nothing to load shows how to start", async () => {
    render(<App plan={{ kind: "static" }} />);
    expect(screen.getByRole("status", { name: "Connecting" })).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "See what your agents are, and what they do" }),
    ).toBeInTheDocument();
    expect(screen.getByText("uvx pydantic-ai-viz demo")).toBeInTheDocument();
    expect(screen.getByText(/pviz\.serve\(agent\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Recording file")).toBeInTheDocument();
    expect(screen.getByText(/Not affiliated with or endorsed by Pydantic/)).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Blueprints and runs" })).toBeNull();
  });

  test("an embedded export opens on its latest run, with blueprints and runs listed", async () => {
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const [latest] = runs(useViewer.getState().data);
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${latest?.traceId}`));

    expect(screen.getByRole("heading", { level: 1, name: "Sample session" })).toBeInTheDocument();
    expect(screen.getByText("Export")).toBeInTheDocument();
    const sidebar = screen.getByRole("complementary", { name: "Blueprints and runs" });
    const blueprints = within(sidebar).getByRole("navigation", { name: /Blueprints/ });
    expect(
      within(blueprints)
        .getAllByRole("link")
        .map((a) => a.getAttribute("href")),
    ).toEqual(["#/agents/a1", "#/agents/a2", "#/agents/a3", "#/agents/a4", "#/graphs/g1"]);
    const runList = within(sidebar).getByRole("navigation", { name: /Runs/ });
    const links = within(runList).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(links[0]).toHaveTextContent("pipeline");
    expect(links[1]).toHaveTextContent(/triage.*6\.32 s.*3,362 tok/);
  });

  test("a recording that fails to load says which file, which line and why", async () => {
    stubFetch((url) =>
      url.endsWith("broken.pviz.jsonl")
        ? new Response(`${JSON.stringify(log()[0])}\n{"seq": 1, oops`)
        : new Response("", { status: 404 }),
    );
    render(<App plan={{ kind: "src", src: "recordings/broken.pviz.jsonl" }} />);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading")).toHaveTextContent(
      "This recording could not be loaded",
    );
    expect(within(alert).getByText("recordings/broken.pviz.jsonl")).toBeInTheDocument();
    expect(within(alert).getByText("2")).toBeInTheDocument();
    expect(within(alert).getByText("not valid JSON")).toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "choose another file" })).toBeInTheDocument();
  });

  test("choosing another file recovers from a failed load", async () => {
    const user = userEvent.setup();
    render(<App plan={{ kind: "embedded", events: "garbage" }} />);
    await screen.findByRole("alert");
    const file = new File([fixtureText("redacted.pviz.jsonl")], "redacted.pviz.jsonl");
    await user.upload(screen.getByLabelText("Recording file"), file);
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/runs\//));
    expect(screen.getByText("Recording")).toBeInTheDocument();
    expect(screen.getByText("Content off")).toBeInTheDocument();
  });

  test("live mode: connecting skeleton, then waiting for a run, then reconnecting", async () => {
    const sources = stubEventSource();
    render(<App plan={livePlan} />);
    expect(screen.getByRole("status", { name: "Connecting" })).toBeInTheDocument();
    expect(screen.getByText("Connecting")).toBeInTheDocument();

    const source = sources.at(-1);
    act(() => source?.onopen?.());
    expect(
      await screen.findByRole("heading", { name: "Connected. Nothing to show yet." }),
    ).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();

    // Blueprints arrive: the blueprint shows, with a "waiting" strip above it.
    const blueprintOnly = fixture("sample.pviz.jsonl").slice(0, 3);
    act(() => useViewer.getState().ingest(blueprintOnly));
    await waitFor(() => expect(window.location.hash).toBe("#/agents/a1"));
    expect(screen.getByText("Waiting for a run")).toBeInTheDocument();

    act(() => source?.onerror?.());
    expect(screen.getByText("Reconnecting...")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "triage" })).toBeInTheDocument();
  });

  test("a live session opens a run as it starts, shows it live, and follows the next one", async () => {
    const user = userEvent.setup();
    const sources = stubEventSource();
    render(<App plan={livePlan} />);
    act(() => sources.at(-1)?.onopen?.());

    const second = "f".repeat(32);
    const third = "e".repeat(32);
    const run = agentRun({ id: "r1", start: 10, end: 50 });
    const request = modelRequest({ id: "m1", parent: "r1", start: 12, end: 40 });
    const events = log(
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      started(run),
      started(request),
      ended(request),
      ended(run),
      started(agentRun({ id: "r2", trace: second, start: 100 })),
      started(agentRun({ id: "r3", trace: third, start: 200 })),
    );
    const feed = (from: number, to: number) =>
      act(() => useViewer.getState().ingest(events.slice(from, to)));

    feed(0, 2);
    await waitFor(() => expect(window.location.hash).toBe("#/agents/a1"));
    expect(screen.getByText("Waiting for a run")).toBeInTheDocument();

    // The first run starts: the viewer goes to it, pinned to now.
    feed(2, 4);
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${TRACE}`));
    expect(
      await screen.findByRole("button", { name: "Agent triage, 1 call, running" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Model m of triage, 1 call, running" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Live" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("treeitem")).toHaveLength(2);

    feed(4, 6);
    expect(screen.getByRole("button", { name: "Agent triage, 1 call, OK" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Output of triage.*, ready$/ })).toBeInTheDocument();

    // The next run takes over while nothing is selected and the view is pinned.
    feed(6, 7);
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${second}`));

    // Pausing to look around stops the following.
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(useViewer.getState().playback.mode).toBe("paused");
    feed(7, 8);
    expect(screen.getAllByRole("link", { name: /triage/ }).length).toBeGreaterThanOrEqual(3);
    expect(window.location.hash).toBe(`#/runs/${second}`);

    await user.click(screen.getByRole("button", { name: "Live" }));
    expect(useViewer.getState().playback.mode).toBe("live");
    expect(window.location.hash).toBe(`#/runs/${second}`);
  });

  test("a new session behind the same address starts the viewer over", async () => {
    // Edit, Ctrl+C, run again on the same port: the open tab reconnects into a new session.
    const user = userEvent.setup();
    const sources = stubEventSource();
    render(<App plan={livePlan} />);
    act(() => sources.at(-1)?.onopen?.());

    const blueprint: [number, Event] = [
      1,
      { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) },
    ];
    const old = agentRun({ id: "r1", start: 10, end: 50 });
    act(() => useViewer.getState().ingest(log(blueprint, started(old), ended(old))));
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${TRACE}`));
    // The user was looking around in the old session: paused, with a span selected.
    await user.click(await screen.findByRole("button", { name: "Step back" }));
    await user.click(screen.getByRole("treeitem", { name: /triage/ }));
    expect(window.location.hash).toContain("?span=");
    expect(useViewer.getState().playback.mode).toBe("paused");

    // The new session's run has already ended by the time the tab reconnects.
    const fresh = "b".repeat(32);
    const rerun = agentRun({ id: "r9", trace: fresh, start: 1010, end: 1050 });
    act(() =>
      useViewer
        .getState()
        .ingest(
          log([1000, sessionStarted("c".repeat(32))], blueprint, started(rerun), ended(rerun)),
        ),
    );
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${fresh}`));
    expect(screen.queryByText("Run not found")).toBeNull();
    expect(useViewer.getState().playback.mode).toBe("live");
    expect(await screen.findByRole("button", { name: "Live" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("runs of the same agent can be told apart by when they started", async () => {
    // Running one agent again and again is what a session mostly is: same name, similar length.
    const hour = 3_600_000;
    const earlier = agentRun({ id: "r1", start: 9 * hour, end: 9 * hour + 2000 });
    const later = agentRun({
      id: "r2",
      trace: "f".repeat(32),
      start: 9 * hour + 95_000,
      end: 9 * hour + 97_000,
    });
    render(
      <App
        plan={{
          kind: "embedded",
          events: log(started(earlier), ended(earlier), started(later), ended(later)),
        }}
      />,
    );
    const list = await screen.findByRole("navigation", { name: /Runs/ });
    const [newest, oldest] = within(list).getAllByRole("link");
    const startOf = (link: HTMLElement | undefined) => link?.querySelector("time");
    expect(startOf(newest)).toHaveAttribute("datetime", "1970-01-01T09:01:35.000Z");
    expect(startOf(oldest)).toHaveAttribute("datetime", "1970-01-01T09:00:00.000Z");
    // Shown as a clock time in the viewer's own time zone: only the seconds are the same
    // everywhere (zones differ by hours, and some by 30 or 45 minutes).
    expect(startOf(newest)?.textContent).toMatch(/^\d\d:\d\d:35$/);
    expect(startOf(oldest)?.textContent).toMatch(/^\d\d:\d\d:00$/);
    expect(newest).toHaveAttribute("title", expect.stringContaining("f".repeat(32)));
    expect(oldest).toHaveAttribute("title", expect.stringContaining(TRACE));
  });

  test("the gallery lists recordings with shareable links", async () => {
    const index = JSON.parse(fixtureText("index.json")) as unknown;
    stubFetch(() => Response.json(index));
    render(<App plan={{ kind: "static" }} />);
    await waitFor(() => expect(window.location.hash).toBe("#/gallery"));
    expect(screen.getByRole("heading", { name: "Recordings" })).toBeInTheDocument();
    const play = screen.getByRole("link", { name: "Play A run that fails" });
    expect(play.getAttribute("href")).toMatch(/\?src=recordings%2Ffailed\.pviz\.jsonl#\/$/);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Gallery" })).toHaveAttribute("aria-current", "page");
  });

  test("recordingHref keeps the page path and makes src relative to it", () => {
    const entry = { id: "a", title: "A", description: "", src: "a.pviz.jsonl" };
    expect(recordingHref(entry, "https://example.test/demo/index.html?src=old#/runs/x")).toBe(
      "/demo/index.html?src=recordings%2Fa.pviz.jsonl#/",
    );
  });
});

describe("screens", () => {
  test("an agent blueprint is a canvas of clickable parts with an inspector beside it", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    expect(screen.getByRole("heading", { level: 2, name: "triage" })).toBeInTheDocument();
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByRole("heading", { name: "triage" })).toBeInTheDocument();
    expect(within(details).getByText("offline model")).toBeInTheDocument();
    expect(within(details).getByText("FunctionToolset 'kb'")).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "refunds" })).toHaveAttribute(
      "href",
      "#/agents/a2",
    );
    expect(within(details).getByText("seen in a run")).toBeInTheDocument();
    expect(within(details).getByText("PendingMessageDrainCapability")).not.toBeVisible();

    await user.click(within(details).getByRole("tab", { name: "Instructions" }));
    expect(within(details).getByText(/Computed at run time by/)).toHaveTextContent(
      "customer_context",
    );

    // Every part of the blueprint is a button on the canvas, the sub-agent included.
    for (const name of ["Agent triage", "Agent refunds", "Tool search_kb", "Tool issue_refund"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Tool get_order" }));
    expect(within(details).getByRole("heading", { name: "get_order" })).toBeInTheDocument();
    await user.click(within(details).getByRole("tab", { name: "Parameters" }));
    expect(within(details).getByRole("table", { name: "Parameters of get_order" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: /^Output of triage: Resolution/ }));
    expect(within(details).getByRole("table", { name: "Output of triage" })).toBeVisible();
  });

  test("an observed blueprint says so and shows its warning", () => {
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded("failed.pviz.jsonl")} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(screen.getAllByText("Observed from runs").length).toBeGreaterThan(0);
    expect(within(details).getAllByText(/Register the agent with/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 warning").length).toBeGreaterThan(0);
  });

  test("a graph blueprint draws nodes by kind, inspects them and copies Mermaid", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    window.location.hash = "#/graphs/g1";
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    expect(screen.getByRole("heading", { level: 2, name: "pipeline" })).toBeInTheDocument();
    for (const name of ["Start Start", "Step Write draft", "Decision decision", "End End"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Decision decision" }));
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByText("approved?")).toBeInTheDocument();
    expect(within(details).getByText("needs work")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("stateDiagram-v2"));
  });

  test("a run replays from the start; skipping to the end shows every span", async () => {
    const user = userEvent.setup();
    const sample = fixture("sample.pviz.jsonl");
    render(<App plan={{ kind: "embedded", events: sample }} />);
    const triage = runs(useViewer.getState().data)[1];
    await openRun(triage?.traceId, "triage");
    const tree = await screen.findByRole("tree", { name: "Spans of this run" });
    await waitFor(() => expect(useViewer.getState().playback.mode).toBe("playing"));
    expect(within(tree).getAllByRole("treeitem").length).toBeLessThan(14);
    // The canvas shows the run so far: nothing has reached the output yet.
    expect(
      screen.getByRole("button", { name: /^Output of triage: Resolution.*not ready yet$/ }),
    ).toBeInTheDocument();

    await skipToEnd(user);
    const rows = within(tree).getAllByRole("treeitem");
    expect(rows).toHaveLength(14);
    expect(rows[0]).toHaveAttribute("aria-level", "1");
    expect(rows[0]).toHaveTextContent(/triage.*OK/);
    expect(rows[2]).toHaveTextContent(/get_order.*Retry 1 of 2/);
    expect(within(tree).getAllByRole("treeitem", { name: /refunds/ })[0]).toHaveAttribute(
      "aria-level",
      "3",
    );
    expect(screen.getByText("6 requests")).toBeInTheDocument();
    expect(screen.getByText("3,109 in / 253 out")).toBeInTheDocument();
    expect(screen.getByText("1 retry")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay" })).toBeInTheDocument();

    // The overlay on the canvas: call counts, the retry, the delegated agent and the output.
    expect(
      screen.getByRole("button", { name: "Tool get_order, retried once, 2 calls, OK" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tool search_kb, 1 call, OK" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agent refunds, 1 call, OK" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Output of triage: Resolution.*, ready$/ }),
    ).toBeInTheDocument();
  });

  test("clicking a canvas node selects its latest span; the subject switcher changes the canvas", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const triage = runs(useViewer.getState().data)[1];
    await openRun(triage?.traceId, "triage");
    await skipToEnd(user);

    await user.click(screen.getByRole("button", { name: /^Tool get_order/ }));
    const details = screen.getByRole("complementary", { name: "Span details" });
    expect(within(details).getByRole("heading", { name: "get_order" })).toBeInTheDocument();
    expect(window.location.hash).toContain("?span=");
    // The latest call is the one that succeeded, not the retry.
    expect(within(details).getByText("OK")).toBeInTheDocument();
    const selected = screen.getByRole("treeitem", { selected: true });
    expect(selected).toHaveTextContent(/get_order.*OK/);

    const subjects = screen.getByRole("group", { name: "Canvas subject" });
    expect(within(subjects).getByRole("button", { name: "triage" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(within(subjects).getByRole("button", { name: "refunds" }));
    expect(screen.queryByRole("button", { name: /^Tool get_order/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: /^Tool issue_refund, 1 call, OK/ }),
    ).toBeInTheDocument();
  });

  test("a graph run shows visit counts on the graph, loop included", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    await screen.findByRole("heading", { level: 2, name: "pipeline" });
    await skipToEnd(user);
    expect(
      screen.getByRole("button", { name: "Step Write draft, visited twice, done" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Decision decision, visited twice, done" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Step Publish, visited once, done" }),
    ).toBeInTheDocument();
  });

  test("selecting a span puts it in the URL and opens its details; scrubbing back hides it", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const triage = runs(useViewer.getState().data)[1];
    const traceId = triage?.traceId ?? "";
    await openRun(traceId, "triage");
    await skipToEnd(user);
    // With nothing selected the inspector shows the run itself.
    const run = screen.getByRole("complementary", { name: "Span details" });
    expect(within(run).getByRole("heading", { name: "triage" })).toBeInTheDocument();
    expect(within(run).getByRole("list", { name: "Conversation" })).toBeInTheDocument();
    expect(screen.queryByRole("treeitem", { selected: true })).toBeNull();

    const tree = screen.getByRole("tree");
    const retry = within(tree).getAllByRole("treeitem")[2];
    if (!retry) throw new Error("expected the retried tool call row");
    await user.click(retry);
    const spanId = retry.getAttribute("data-span");
    expect(window.location.hash).toBe(`#/runs/${traceId}?span=${spanId}`);
    expect(retry).toHaveAttribute("aria-selected", "true");

    const details = screen.getByRole("complementary", { name: "Span details" });
    expect(within(details).getByText("pydantic_ai.exceptions.ToolRetryError")).toBeInTheDocument();
    expect(within(details).getAllByText(/Order ids look like 'A-1042'/).length).toBeGreaterThan(0);
    await user.click(within(details).getByRole("tab", { name: "Raw" }));
    expect(within(details).getByText("execute_tool")).toBeInTheDocument();

    // j moves to the next span; Escape clears the selection.
    await user.keyboard("j");
    expect(window.location.hash).not.toContain(spanId ?? "none");
    expect(window.location.hash).toContain("?span=");
    await user.keyboard("{Escape}");
    expect(window.location.hash).toBe(`#/runs/${traceId}`);

    // Scrub to just after the run started: later spans are gone, the run is running again.
    const bounds = runBounds(useViewer.getState().data, traceId);
    if (!bounds) throw new Error("expected bounds");
    act(() => useViewer.getState().updatePlayback((p) => seek(p, bounds.start + 500, bounds)));
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(2);
    expect(within(tree).getAllByRole("treeitem")[0]).toHaveTextContent(/triage.*Running/);
    expect(screen.getByText("1 request")).toBeInTheDocument();
  });

  test("a deep link restores the selected span", async () => {
    const user = userEvent.setup();
    const sample = fixture("sample.pviz.jsonl");
    const span = sample.flatMap((e) =>
      e.type === "span.ended" && e.span.name === "issue_refund" ? [e.span] : [],
    )[0];
    window.location.hash = `#/runs/${span?.trace_id}?span=${span?.span_id}`;
    render(<App plan={{ kind: "embedded", events: sample }} />);
    // No replay from the start here: the linked span would only show up once the replay got to
    // it. The run opens held at its end, with the span there to read.
    const details = await screen.findByRole("complementary", { name: "Span details" });
    expect(await within(details).findByRole("heading", { name: "issue_refund" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Not started yet" })).toBeNull();
    expect(screen.getByRole("button", { name: "Replay" })).toBeInTheDocument();
    await user.click(within(details).getByRole("tab", { name: "Result" }));
    expect(within(details).getByText(/RF-88213/)).toBeInTheDocument();

    // Replaying from the start hides the span again; one click goes back to it.
    await user.click(screen.getByRole("button", { name: "Replay" }));
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    await user.click(await screen.findByRole("button", { name: "Jump to this span" }));
    expect(within(details).getByRole("heading", { name: "issue_refund" })).toBeVisible();
    expect(within(details).getByText(/RF-88213/)).toBeInTheDocument();
  });

  test("a request that is still streaming shows the text so far", async () => {
    const user = userEvent.setup();
    const events = fixture("failed.pviz.jsonl");
    render(<App plan={{ kind: "embedded", events }} />);
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/runs\//));
    await skipToEnd(user);
    await user.click(screen.getAllByRole("treeitem")[1] as HTMLElement);
    const partial = screen.getByRole("region", { name: "Response so far" });
    expect(within(partial).getByText("Thinking")).toBeInTheDocument();
    expect(within(partial).getByText("Fetching the pydantic-ai feed")).toBeInTheDocument();
    expect(
      screen.getByText("The recording ends here, before this run finished."),
    ).toBeInTheDocument();
  });

  test("a redacted session says content capture is off instead of showing blanks", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("redacted.pviz.jsonl")} />);
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/runs\//));
    await skipToEnd(user);
    await user.click(screen.getAllByRole("treeitem")[2] as HTMLElement);
    expect(screen.getByText(/Content capture is off/)).toBeInTheDocument();
    expect(screen.getByText("Content off")).toBeInTheDocument();
  });

  test("unknown routes and ids are explained, not blank", async () => {
    window.location.hash = "#/agents/zz";
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    expect(screen.getByRole("heading", { name: "Agent blueprint not found" })).toBeInTheDocument();
    act(() => {
      window.location.hash = "#/settings";
    });
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    act(() => {
      window.location.hash = "#/runs/ffff";
    });
    expect(await screen.findByRole("heading", { name: "Run not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the start" })).toHaveAttribute("href", "#/");
  });
});

describe("shell behaviour", () => {
  test("t toggles the theme and remembers it; ? opens the shortcut list", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    await user.keyboard("t");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("pviz.theme")).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");

    await user.keyboard("?");
    const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(dialog).getByText("Play or pause")).toBeInTheDocument();
    // While the dialog is open, shortcuts do not reach the page behind it.
    await user.keyboard("t");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  test("the theme still toggles when storage is unavailable", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    await user.keyboard("t");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  test("shortcuts are ignored while typing and with modifiers", async () => {
    const user = userEvent.setup();
    render(
      <>
        <App plan={embedded("sample.pviz.jsonl")} />
        <input aria-label="notes" />
      </>,
    );
    await user.type(screen.getByLabelText("notes"), "t?");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.keyDown(document.body, { key: "t", ctrlKey: true });
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  test("playback keys still work with a timeline row or a canvas node focused", async () => {
    // Clicking a row is the first thing anyone does, and it leaves the focus on that row.
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const triage = runs(useViewer.getState().data)[1];
    await openRun(triage?.traceId, "triage");
    await skipToEnd(user);
    const all = screen.getAllByRole("treeitem").length;
    const row = screen.getAllByRole("treeitem")[2] as HTMLElement;
    await user.click(row);
    expect(row).toHaveFocus();

    // Three boundaries back: the first ones are span ends, which hide no row.
    await user.keyboard("{ArrowLeft}{ArrowLeft}{ArrowLeft}");
    expect(screen.getAllByRole("treeitem").length).toBeLessThan(all);
    await user.keyboard("{ArrowRight}");
    await user.keyboard(" ");
    expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();
    await user.keyboard(" ");
    expect(await screen.findByRole("button", { name: "Play" })).toBeInTheDocument();
    // The row keeps its own keys: Enter selects, Down moves on.
    await user.keyboard("{ArrowDown}");
    expect(window.location.hash).not.toContain(row.getAttribute("data-span") ?? "none");

    // A canvas node is a real button, so Space presses it; the arrows still step.
    await skipToEnd(user);
    const node = screen.getByRole("button", { name: /^Agent triage/ });
    await user.click(node);
    const before = screen.getAllByRole("treeitem").length;
    await user.keyboard("{ArrowLeft}{ArrowLeft}{ArrowLeft}");
    expect(screen.getAllByRole("treeitem").length).toBeLessThan(before);
  });

  test("the inspector resizes from the keyboard and collapses", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Workspace main={<p>main</p>} inspector={<p>details</p>} />
      </TooltipProvider>,
    );
    const separator = screen.getByRole("separator", { name: "Resize inspector" });
    // jsdom's window is 1024 px wide: the inspector starts at its small-window width.
    expect(separator).toHaveAttribute("aria-valuenow", "300");
    separator.focus();
    await user.keyboard("{ArrowLeft}");
    expect(separator).toHaveAttribute("aria-valuenow", "316");
    await user.keyboard("{End}");
    expect(separator).toHaveAttribute("aria-valuenow", "280");
    expect(window.localStorage.getItem("pviz.inspector.width")).toBe("280");

    await user.click(screen.getByRole("button", { name: "Hide inspector" }));
    expect(screen.queryByText("details")).toBeNull();
    expect(screen.getByRole("button", { name: "Show inspector" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("a render error is contained, with details to copy and a way out", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary resetKey="#/runs/a">
        <Broken />
      </ErrorBoundary>,
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByRole("heading")).toHaveTextContent("This view failed to render");
    expect(within(alert).getByRole("button", { name: "Reload" })).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: /Copy details/ }));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("TypeError: span.data is undefined"),
    );

    rerender(
      <ErrorBoundary resetKey="#/agents/a1">
        <p>recovered</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("recovered")).toBeInTheDocument();
  });
});
