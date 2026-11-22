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

type FakeSource = {
  onopen?: () => void;
  onerror?: () => void;
  named: Map<string, (m: { data: string }) => void>;
};

/** Replaces EventSource with one the test drives; returns the instances created. */
function stubEventSource(): FakeSource[] {
  const sources: FakeSource[] = [];
  class FakeEventSource {
    static readonly CLOSED = 2;
    readyState = 1;
    onopen?: () => void;
    onerror?: () => void;
    onmessage?: (m: { data: string }) => void;
    named = new Map<string, (m: { data: string }) => void>();
    constructor() {
      sources.push(this);
    }
    close() {}
    addEventListener(name: string, listener: (m: { data: string }) => void) {
      this.named.set(name, listener);
    }
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
    expect(await screen.findByRole("heading", { name: "Nothing to show yet" })).toBeInTheDocument();
    // "Live" is the one place that says the page is connected; an empty session has no lists.
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText(/Connected/)).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Blueprints and runs" })).toBeNull();
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();

    // Blueprints arrive: the blueprint shows, with a "waiting" strip above it. The empty run
    // list does not say the same thing a second time.
    const blueprintOnly = fixture("sample.pviz.jsonl").slice(0, 3);
    act(() => useViewer.getState().ingest(blueprintOnly));
    await waitFor(() => expect(window.location.hash).toBe("#/agents/a1"));
    expect(screen.getByText("Waiting for a run")).toBeInTheDocument();
    const runList = screen.getByRole("navigation", { name: /Runs/ });
    expect(runList).toHaveTextContent(/^Runs0$/);

    // The connection drops: the banner says so, and nothing claims to be live meanwhile.
    act(() => source?.onerror?.());
    expect(screen.getByText("Reconnecting...")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "triage" })).toBeInTheDocument();
    // What asks the server waits for it to answer again.
    expect(screen.getByRole("button", { name: "Run again" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
    act(() => source?.onopen?.());
    expect(screen.queryByText("Reconnecting...")).toBeNull();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Export" })).toBeEnabled();
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

  test("a run that was over before the viewer drew it is followed, one in the backlog is not", async () => {
    // A fast run can start and end between two frames: the viewer never sees it running.
    const sources = stubEventSource();
    render(<App plan={livePlan} />);
    const source = sources.at(-1);
    act(() => source?.onopen?.());
    act(() => source?.named.get("backlog")?.({ data: '{"last_seq":3}' }));

    const second = "f".repeat(32);
    const old = agentRun({ id: "r1", start: 10, end: 50 });
    const fast = agentRun({ id: "r2", trace: second, start: 100, end: 120 });
    const events = log(
      [1, { type: "blueprint.agent", agent: agentBlueprint({ id: "a1", name: "triage" }) }],
      started(old),
      ended(old),
      started(fast),
      ended(fast),
    );
    // The backlog (up to seq 3) holds a finished run: history, so the viewer stays home.
    window.location.hash = "#/agents/a1";
    act(() => useViewer.getState().ingest(events.slice(0, 4)));
    await waitFor(() => expect(screen.getAllByRole("link", { name: /triage/ }).length).toBe(2));
    expect(window.location.hash).toBe("#/agents/a1");

    // Watching that run, the next one arrives whole in one batch, after the backlog: news.
    window.location.hash = `#/runs/${TRACE}`;
    await waitFor(() => expect(screen.getByRole("button", { name: "Live" })).toBeInTheDocument());
    act(() => useViewer.getState().ingest(events.slice(4)));
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${second}`));
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
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("link", { name: "Gallery" })).toHaveAttribute("aria-current", "page");
  });

  test("a gallery entry of kind blueprint is opened, not played", async () => {
    stubFetch(() =>
      Response.json({
        recordings: [
          { id: "r", title: "A run", description: "", src: "r.pviz.jsonl" },
          { id: "s", title: "The source", description: "", src: "s.pviz.jsonl", kind: "blueprint" },
        ],
      }),
    );
    render(<App plan={{ kind: "static" }} />);
    await waitFor(() => expect(window.location.hash).toBe("#/gallery"));
    expect(screen.getByRole("link", { name: "Play A run" })).toBeInTheDocument();
    const open = screen.getByRole("link", { name: "Open The source" });
    expect(open.getAttribute("href")).toMatch(/\?src=recordings%2Fs\.pviz\.jsonl#\/$/);
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

    // A blueprint is short: everything about the agent is in one scroll, under headings.
    expect(within(details).queryByRole("tablist")).toBeNull();
    const panel = within(details).getByRole("region", { name: "Agent blueprint" });
    expect(
      within(panel)
        .getAllByRole("heading", { level: 4 })
        .map((heading) => heading.textContent),
    ).toEqual([
      "Model",
      "Instructions3",
      "Toolsets",
      "OutputResolution",
      "Delegates to1",
      "Settings",
      "Capabilities",
      "Mermaid",
    ]);
    expect(within(panel).getByText(/A test or function model/)).toBeInTheDocument();
    expect(within(panel).getByText("FunctionToolset 'kb'")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "refunds" })).toHaveAttribute(
      "href",
      "#/agents/a2",
    );
    expect(within(panel).getByText("seen in a run")).toBeInTheDocument();
    expect(within(panel).getByText("PendingMessageDrainCapability")).not.toBeVisible();
    expect(within(panel).getByText(/Computed at run time by/)).toHaveTextContent(
      "customer_context",
    );
    expect(within(panel).getByRole("table", { name: "Output of triage" })).toBeVisible();

    // Every part of the blueprint is a button on the canvas, the sub-agent included.
    for (const name of ["Agent triage", "Agent refunds", "Tool search_kb", "Tool issue_refund"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Tool get_order" }));
    expect(within(details).getByRole("heading", { name: "get_order" })).toBeInTheDocument();
    expect(within(details).queryByRole("tablist")).toBeNull();
    const tool = within(details).getByRole("region", { name: "Tool" });
    expect(
      within(tool)
        .getAllByRole("heading", { level: 4 })
        .map((heading) => heading.textContent),
    ).toEqual(["Parameters", "Returns", "Definition"]);
    expect(within(tool).getByRole("table", { name: "Parameters of get_order" })).toBeVisible();
  });

  test("picking the output node brings the output section of the agent into view", async () => {
    const user = userEvent.setup();
    // jsdom has no layout: say where things are. The panel is 100 px down the page and the
    // output section 300 px, so the panel has to scroll by 200 to bring it to its top.
    const layout = vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: Element) {
        const section = this.getAttribute("data-section");
        const top = section === "output" ? 300 : this.tagName === "SECTION" ? 100 : 0;
        return new DOMRect(0, top, 0, 0);
      });
    try {
      window.location.hash = "#/agents/a1";
      render(<App plan={embedded("sample.pviz.jsonl")} />);
      const details = screen.getByRole("complementary", { name: "Blueprint details" });
      const panel = within(details).getByRole("region", { name: "Agent blueprint" });
      const output = screen.getByRole("button", { name: /^Output of triage: Resolution/ });
      await user.click(output);
      expect(panel.scrollTop).toBe(200);
      // The panel is still the agent's: the output is one of its sections.
      expect(within(details).getByRole("heading", { name: "triage" })).toBeInTheDocument();
      // Asking again scrolls again, in case the reader has moved away since.
      panel.scrollTop = 0;
      await user.click(output);
      expect(panel.scrollTop).toBe(200);
      // Picking the agent itself leaves the panel where the reader has it.
      panel.scrollTop = 40;
      await user.click(screen.getByRole("button", { name: "Agent triage" }));
      expect(panel.scrollTop).toBe(40);
      // Nothing but the panel is scrolled: the shell's fixed layout must not move.
      expect(document.documentElement.scrollTop).toBe(0);
    } finally {
      layout.mockRestore();
    }
  });

  test("an observed blueprint says so and shows its warning", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded("failed.pviz.jsonl")} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    // The count is above the canvas; the list is the first thing in the panel, said once.
    expect(screen.getByText("1 warning")).toBeInTheDocument();
    const panel = within(details).getByRole("region", { name: "Agent blueprint" });
    expect(within(panel).getAllByRole("heading", { level: 4 })[0]).toHaveTextContent("Warnings1");
    expect(within(details).getByText(/Register the agent with/)).toBeInTheDocument();

    // How the blueprint is known is one chip, and the chip opens the key to all three ways.
    await user.click(within(details).getByRole("button", { name: /^Observed in runs/ }));
    const legend = screen.getByRole("dialog", { name: "How a blueprint is known" });
    expect(
      within(legend)
        .getAllByRole("term")
        .map((term) => term.textContent),
    ).toEqual(["From source", "From a live object", "Observed in runs (this one)"]);
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
    // Its panel says which attempt it was once, as the status beside its name.
    await user.click(rows[2] as HTMLElement);
    const details = screen.getByRole("complementary", { name: "Span details" });
    expect(within(details).getAllByText(/1 of 2/)).toHaveLength(1);
    expect(within(details).getByText("Retry 1 of 2").closest("header")).not.toBeNull();
    expect(within(details).getByText("model_retry")).toBeInTheDocument();
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
    // Arguments and result are short: both are in the one "Details" tab, under headings.
    expect(
      within(details)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Details", "Raw"]);
    expect(
      within(within(details).getByRole("tabpanel"))
        .getAllByRole("heading", { level: 4 })
        .map((heading) => heading.textContent),
    ).toEqual(["Arguments", "Result"]);
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
    // The arguments and the result, each where its value would be.
    expect(screen.getAllByText(/Content capture is off/)).toHaveLength(2);
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

describe("a blueprint-only session (pydantic-ai-viz view)", () => {
  const NEWSROOM = "blueprint/newsroom.pviz.jsonl";
  const viewPlan: BootPlan = { ...livePlan, rerunUrl: null };
  /** The top bar. The screen and the inspector have headers of their own. */
  const topBar = (): HTMLElement => {
    const bar = screen.getByRole("link", { name: "pydantic-ai-viz home" }).closest("header");
    if (!bar) throw new Error("expected the top bar");
    return bar;
  };
  /** The words a session with runs uses. None of them is true of a blueprint-only view. */
  const LIVE_WORDS = [
    /\bLive\b/,
    /Connect(ed|ing)/,
    /Waiting for a run/,
    /No runs/,
    /Reconnecting/,
  ];

  test("served: it says what it is, why it has no runs, and never claims to be live", async () => {
    const user = userEvent.setup();
    const sources = stubEventSource();
    render(<App plan={viewPlan} />);
    act(() => sources.at(-1)?.onopen?.());
    act(() => useViewer.getState().ingest(fixture(NEWSROOM)));
    await waitFor(() => expect(window.location.hash).toBe("#/agents/a1"));

    const bar = topBar();
    expect(within(bar).getByRole("heading", { level: 1, name: "newsroom" })).toBeInTheDocument();
    expect(within(bar).getByText("Blueprint")).toBeInTheDocument();
    for (const words of LIVE_WORDS) expect(screen.queryByText(words)).toBeNull();
    expect(screen.queryByRole("button", { name: "Run again" })).toBeNull();

    // Where the runs would be, the sidebar explains the view instead.
    const sidebar = screen.getByRole("complementary", { name: "Blueprints" });
    expect(within(sidebar).queryByRole("navigation", { name: /Runs/ })).toBeNull();
    const about = within(sidebar).getByRole("region", { name: "About this view" });
    expect(about).toHaveTextContent("Read from source code");
    expect(about).toHaveTextContent(
      "The code was parsed and never run, so there are no runs here.",
    );
    expect(about).toHaveTextContent("To see a run, call pydantic_ai_viz.serve(agent)");

    // What static analysis had to skip is one click away, in every mode.
    await user.click(within(bar).getByRole("button", { name: "1 notice" }));
    expect(screen.getByRole("dialog", { name: "Session notices" })).toHaveTextContent(
      "newsroom/legacy.py was skipped",
    );
    await user.keyboard("{Escape}");

    // An export of blueprints holds no captured content, and the menu does not warn of any.
    await user.click(screen.getByRole("button", { name: "Export" }));
    const menu = screen.getByRole("dialog", { name: "Export" });
    expect(within(menu).getByRole("link", { name: "Blueprints (.pviz.jsonl)" })).toHaveAttribute(
      "href",
      "api/v1/export.jsonl",
    );
    expect(menu).toHaveTextContent("There is no run data in them.");
    expect(menu).not.toHaveTextContent(/prompts/);
    await user.keyboard("{Escape}");

    // Ctrl+C on `view`: nothing is lost, so nothing is "reconnecting".
    act(() => sources.at(-1)?.onerror?.());
    expect(screen.getByText("The server behind this page has stopped.")).toBeInTheDocument();
    for (const words of LIVE_WORDS) expect(screen.queryByText(words)).toBeNull();
    expect(within(bar).getByText("Blueprint")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agent editor, 2 warnings" })).toBeInTheDocument();
  });

  test("exported: the same view from a file, with nothing to export", () => {
    window.location.hash = "#/graphs/g1";
    render(<App plan={embedded(NEWSROOM)} />);
    const bar = topBar();
    expect(within(bar).getByText("Blueprint")).toBeInTheDocument();
    // It is an export, but "Blueprint" is the one thing the chip has to say.
    expect(within(bar).queryByText("Export")).toBeNull();
    for (const words of LIVE_WORDS) expect(screen.queryByText(words)).toBeNull();
    expect(screen.getByRole("region", { name: "About this view" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "publishing" })).toBeInTheDocument();
  });

  test("imported objects (view --import) are not said to be read from source", () => {
    const events = fixture("sample.pviz.jsonl")
      .slice(0, 3)
      .map((event) => (event.type === "session.started" ? { ...event, mode: "blueprint" } : event));
    render(<App plan={{ kind: "embedded", events }} />);
    const about = screen.getByRole("region", { name: "About this view" });
    expect(about).toHaveTextContent("Read from live objects");
    expect(about).toHaveTextContent("The code was imported");
    expect(about).not.toHaveTextContent(/parsed/);
  });

  test("a session with runs keeps its run list, and says so when it is empty", () => {
    const events = fixture("sample.pviz.jsonl").slice(0, 3);
    render(<App plan={{ kind: "embedded", events }} />);
    expect(screen.queryByRole("region", { name: "About this view" })).toBeNull();
    expect(screen.getByRole("navigation", { name: /Runs/ })).toHaveTextContent(
      "No runs in this export.",
    );
    expect(screen.queryByText("Waiting for a run")).toBeNull();
  });

  test("an agent card shows how many warnings its blueprint has", () => {
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded(NEWSROOM)} />);
    const card = screen.getByRole("button", { name: "Agent editor, 2 warnings" });
    const badge = card.parentElement?.querySelector('[title="2 warnings"]');
    expect(badge).toHaveTextContent(/^2$/);
    expect(badge?.querySelector("svg")).not.toBeNull();
  });

  test("the sidebar groups blueprints by the file they are defined in", () => {
    render(<App plan={embedded(NEWSROOM)} />);
    const list = screen.getByRole("navigation", { name: /Blueprints/ });
    expect(
      within(list)
        .getAllByRole("heading", { level: 3 })
        .map((heading) => heading.getAttribute("title")),
    ).toEqual([
      "newsroom/desk.py",
      "newsroom/reporters.py",
      "newsroom/checks.py",
      "newsroom/pipeline.py",
    ]);
    // In file order, each with the number of its warnings: marked as such, never a bare number.
    const names = ["editor", "reporter", "stringer", "fact_checker", "publishing"];
    expect(
      within(list)
        .getAllByRole("link")
        .map((link) => link.querySelector("[title]")?.textContent),
    ).toEqual(names);
    for (const name of [
      "editor, 2 warnings",
      "reporter, 3 warnings",
      "stringer",
      "fact_checker, 1 warning",
      "publishing",
    ]) {
      expect(within(list).getByRole("link", { name })).toBeInTheDocument();
    }
    expect(within(list).getByTitle("3 warnings")).toHaveTextContent(/^3$/);
    expect(within(list).getByTitle("3 warnings").querySelector("svg")).not.toBeNull();
    // Five blueprints can be read at a glance: no filter.
    expect(within(list).queryByRole("searchbox")).toBeNull();
  });

  test("a long list can be filtered by name or file", async () => {
    const user = userEvent.setup();
    const teams = ["billing", "support", "search", "growth"];
    const blueprints = Array.from({ length: 36 }, (_, i): [number, Event] => {
      const team = teams[i % teams.length] ?? "";
      return [
        1,
        {
          type: "blueprint.agent",
          agent: agentBlueprint({
            id: `a${i + 1}`,
            name: `${team}_agent_${i + 1}`,
            source: "code",
            location: { file: `app/${team}/agents_${i % 8}.py`, line: 10 + i },
          }),
        },
      ];
    });
    const events = log([0, { ...sessionStarted(), mode: "blueprint" }], ...blueprints);
    render(<App plan={{ kind: "embedded", events }} />);
    const list = screen.getByRole("navigation", { name: /Blueprints/ });
    expect(within(list).getAllByRole("link")).toHaveLength(36);
    expect(within(list).getAllByRole("heading", { level: 3 })).toHaveLength(8);
    expect(list).toHaveTextContent(/^Blueprints36/);

    const filter = within(list).getByRole("searchbox", { name: "Filter blueprints" });
    await user.type(filter, "Billing");
    expect(list).toHaveTextContent(/^Blueprints9 of 36/);
    expect(within(list).getAllByRole("link")).toHaveLength(9);
    expect(within(list).getAllByRole("heading", { level: 3 })).toHaveLength(2);

    // A file name narrows it further; a link still leads to its blueprint.
    await user.clear(filter);
    await user.type(filter, "agents_4.py");
    const links = within(list).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "billing_agent_5",
      "billing_agent_13",
      "billing_agent_21",
      "billing_agent_29",
    ]);
    await user.click(links[1] as HTMLElement);
    await waitFor(() => expect(window.location.hash).toBe("#/agents/a13"));
    expect(screen.getByRole("heading", { level: 2, name: "billing_agent_13" })).toBeInTheDocument();
    expect(filter).toHaveValue("agents_4.py");

    await user.clear(filter);
    await user.type(filter, "nothing-like-this");
    expect(within(list).queryAllByRole("link")).toHaveLength(0);
    expect(within(list).getByRole("status")).toHaveTextContent(
      'No blueprint matches "nothing-like-this".',
    );
    // Escape empties the box, and typing in it never triggers a shortcut (t toggles the theme).
    await user.keyboard("{Escape}");
    expect(filter).toHaveValue("");
    expect(within(list).getAllByRole("link")).toHaveLength(36);
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });
});
