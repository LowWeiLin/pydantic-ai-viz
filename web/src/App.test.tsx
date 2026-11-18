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
import { fixture, fixtureText, log } from "./test/factory";
import { TooltipProvider } from "./ui/overlays";

const embedded = (name: string): BootPlan => ({ kind: "embedded", events: fixture(name) });

/** The recording index is looked up in static mode; answer "none" unless a test says otherwise. */
function stubFetch(handler: (url: string) => Response = () => new Response("", { status: 404 })) {
  const mock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

function Broken(): never {
  throw new TypeError("span.data is undefined");
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
    const sources: {
      onopen?: () => void;
      onerror?: () => void;
      onmessage?: (m: { data: string }) => void;
    }[] = [];
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
    render(
      <App
        plan={{
          kind: "live",
          eventsUrl: "api/v1/events",
          exportHtmlUrl: "api/v1/export.html",
          exportJsonlUrl: "api/v1/export.jsonl",
          rerunUrl: "api/v1/rerun",
        }}
      />,
    );
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
  test("an agent blueprint lists instructions, tools with schemas, delegation and output", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    expect(screen.getByRole("heading", { level: 2, name: "triage" })).toBeInTheDocument();
    expect(screen.getByText("offline model")).toBeInTheDocument();
    expect(screen.getByText(/Computed at run time by/)).toHaveTextContent("customer_context");
    expect(screen.getByText("FunctionToolset 'kb'")).toBeInTheDocument();

    expect(screen.queryByRole("table", { name: "Parameters of get_order" })).not.toBeVisible();
    await user.click(screen.getByText("get_order"));
    expect(screen.getByRole("table", { name: "Parameters of get_order" })).toBeVisible();

    const delegate = screen.getByRole("link", { name: "refunds" });
    expect(delegate).toHaveAttribute("href", "#/agents/a2");
    expect(screen.getByText("seen in a run")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Output of triage" })).toBeInTheDocument();
    expect(screen.getByText("PendingMessageDrainCapability")).not.toBeVisible();
  });

  test("an observed blueprint says so and shows its warning", () => {
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded("failed.pviz.jsonl")} />);
    expect(screen.getByText("Observed from runs")).toBeInTheDocument();
    expect(screen.getByText(/Register the agent with serve\(\)/)).toBeInTheDocument();
    expect(screen.getByText("1 warning")).toBeInTheDocument();
  });

  test("a graph blueprint lists nodes and edges and copies Mermaid", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    window.location.hash = "#/graphs/g1";
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    expect(screen.getByRole("heading", { level: 2, name: "pipeline" })).toBeInTheDocument();
    expect(screen.getByText("approved?")).toBeInTheDocument();
    expect(screen.getByText("needs work")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("stateDiagram-v2"));
  });

  test("a run replays from the start; skipping to the end shows every span", async () => {
    const user = userEvent.setup();
    const sample = fixture("sample.pviz.jsonl");
    render(<App plan={{ kind: "embedded", events: sample }} />);
    const triage = runs(useViewer.getState().data)[1];
    act(() => {
      window.location.hash = `#/runs/${triage?.traceId}`;
    });
    const tree = await screen.findByRole("tree", { name: "Spans of this run" });
    await waitFor(() => expect(useViewer.getState().playback.mode).toBe("playing"));
    expect(within(tree).getAllByRole("treeitem").length).toBeLessThan(14);

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
  });

  test("selecting a span puts it in the URL and opens its details; scrubbing back hides it", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const triage = runs(useViewer.getState().data)[1];
    const traceId = triage?.traceId ?? "";
    act(() => {
      window.location.hash = `#/runs/${traceId}`;
    });
    await skipToEnd(user);
    expect(screen.getByRole("heading", { name: "Select a span" })).toBeInTheDocument();

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
    expect(await screen.findByRole("heading", { name: "Not started yet" })).toBeInTheDocument();
    await skipToEnd(user);
    const details = screen.getByRole("complementary", { name: "Span details" });
    expect(within(details).getByRole("heading", { name: "issue_refund" })).toBeInTheDocument();
    expect(within(details).getByText("RF-88213")).toBeInTheDocument();
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

  test("the inspector resizes from the keyboard and collapses", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Workspace main={<p>main</p>} inspector={<p>details</p>} />
      </TooltipProvider>,
    );
    const separator = screen.getByRole("separator", { name: "Resize inspector" });
    expect(separator).toHaveAttribute("aria-valuenow", "380");
    separator.focus();
    await user.keyboard("{ArrowLeft}");
    expect(separator).toHaveAttribute("aria-valuenow", "396");
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
