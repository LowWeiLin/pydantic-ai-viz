/**
 * Blueprint screens over a session read from source code: what static analysis found is shown
 * as found, what it could not know is shown as the code that decides it, and the picture can be
 * taken away as Mermaid.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { App } from "../App";
import type { BootPlan } from "../boot";
import { runs } from "../data/selectors";
import { useViewer } from "../data/store";
import { firstLine, parameterCount, trimBlankLines } from "../inspector/BlueprintInspector";
import { SourceRef } from "../inspector/source";
import {
  agentBlueprint,
  agentRun,
  capability,
  fixture,
  hook,
  lifecycle,
  log,
  sessionStarted,
  sid,
  skill,
  TRACE,
  toolCall,
} from "../test/factory";
import type { AgentBlueprint, Toolset } from "../wire";

const NEWSROOM = "blueprint/newsroom.pviz.jsonl";
const embedded = (name: string): BootPlan => ({ kind: "embedded", events: fixture(name) });

/** Opens a run by its hash and waits for its screen (the route change is asynchronous). */
async function openRun(traceId: string | undefined, name: string): Promise<void> {
  act(() => {
    window.location.hash = `#/runs/${traceId}`;
  });
  await screen.findByRole("heading", { level: 2, name });
}

describe("a blueprint read from source", () => {
  test("what the code decides at run time is shown as code, and said to be", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });

    // Where it is defined, ready to paste into an editor's go-to-file box.
    expect(within(details).getByRole("button", { name: /^From source/ })).toBeInTheDocument();
    await user.click(
      within(details).getByRole("button", { name: "Copy newsroom/reporters.py:31" }),
    );
    expect(writeText).toHaveBeenCalledWith("newsroom/reporters.py:31");

    const section = (title: string): HTMLElement => {
      const heading = within(details)
        .getAllByRole("heading", { level: 4 })
        .find((h) => h.textContent?.startsWith(title));
      if (!heading?.parentElement) throw new Error(`no section ${title}`);
      return heading.parentElement;
    };
    // The model name is read from the environment: the variable is shown, and the default the
    // source gives is labelled as a default, never passed off as the model.
    const model = section("Model");
    expect(within(model).getByText("Model name")).toBeInTheDocument();
    expect(within(model).getByText("NEWSROOM_MODEL").tagName).toBe("CODE");
    expect(within(model).getByText("env")).toBeInTheDocument();
    expect(within(model).getByText("Default")).toBeInTheDocument();
    expect(within(model).getByText("gpt-5.2-mini").tagName).toBe("CODE");
    expect(within(model).getByText(/Only if NEWSROOM_MODEL is not set/)).toBeInTheDocument();
    expect(within(model).getByText("OpenAIChatModel").tagName).toBe("CODE");
    expect(within(model).getByText("Built by")).toBeInTheDocument();
    expect(within(model).getByText("get_model()").tagName).toBe("CODE");
    expect(within(model).queryByText("Name")).toBeNull();
    // On the canvas the chip names the variable, not the default.
    const chip = screen.getByText("env NEWSROOM_MODEL");
    expect(chip.closest("[title]")).toHaveAttribute(
      "title",
      "OpenAIChatModel, model name from environment variable NEWSROOM_MODEL at run time; " +
        "default gpt-5.2-mini if NEWSROOM_MODEL is not set",
    );
    expect(screen.queryByText("gpt-5.2-mini", { selector: ".react-flow *" })).toBeNull();
    expect(screen.getAllByText("at run time")).toHaveLength(1);

    // Warnings are a quiet list, with the code in them set as code.
    const warnings = section("Warnings");
    expect(within(warnings).getAllByRole("listitem")).toHaveLength(3);
    expect(within(warnings).getByText("NEWSROOM_MODEL").tagName).toBe("CODE");
    expect(warnings.querySelector(".tone-retry, .tone-error")).toBeNull();

    expect(within(section("Instructions")).getByText('PROMPTS["reporter"]').tagName).toBe("CODE");
    const toolsets = section("Toolsets");
    expect(within(toolsets).getByText('MCPServerStdio("uvx", args=["mcp-archive"])')).toBeVisible();
    expect(within(toolsets).getByText("PrefixedToolset")).toBeInTheDocument();
    expect(within(toolsets).getByTitle("newsroom/reporters.py:22")).toBeInTheDocument();
    // Static analysis fills in no default, and the panel does not list rows of dashes.
    expect(section("Settings")).toHaveTextContent("The code sets none");
    expect(within(details).queryByText("Capabilities")).toBeNull();
  });

  test("templates, computed settings, model classes and types that could not be followed", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded(NEWSROOM)} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });

    // A template keeps its whole text; the holes are marked, with a short note and no warning.
    expect(within(details).getByText("{today:%A}").tagName).toBe("CODE");
    expect(within(details).getByText("{STYLE.name}").tagName).toBe("CODE");
    expect(within(details).getByText("Template")).toBeInTheDocument();
    expect(within(details).getByText(/A template: the marked parts/)).toBeInTheDocument();
    // A setting can be a literal, an expression, or absent.
    expect(within(details).getByText("MAX_RETRIES", { selector: "dd code" })).toBeInTheDocument();
    expect(within(details).getByText("DESK.temperature", { selector: "dd code" })).toBeVisible();
    expect(within(details).getByText("max_tokens")).toBeInTheDocument();
    expect(within(details).queryByText("End strategy")).toBeNull();
    expect(within(details).getByTitle("newsroom/desk.py:12")).toBeInTheDocument();
    // A delegate in another file says where it is.
    expect(within(details).getByText("reporters.py:31")).toBeInTheDocument();

    // A model class built with a literal name is known by the name, and shows how it is built.
    act(() => {
      window.location.hash = "#/agents/a3";
    });
    await screen.findByRole("heading", { level: 2, name: "stringer" });
    const stringer = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(stringer).getByText("claude-sonnet-4-5", { selector: "dd code" })).toBeVisible();
    expect(within(stringer).getByText("Built by")).toBeInTheDocument();
    expect(within(stringer).queryByText("Decided at run time")).toBeNull();
    expect(screen.queryByText("at run time")).toBeNull();
    // A tool Pydantic AI defines, read from the source of the installed Pydantic AI.
    await user.click(screen.getByRole("button", { name: "Tool duckduckgo_search" }));
    expect(within(stringer).getByText(/installed Pydantic AI 2\.70\.0/)).toBeVisible();
    expect(within(stringer).getByText(/The project may use another version/)).toBeVisible();
    expect(
      within(stringer).getByRole("table", { name: "Parameters of duckduckgo_search" }),
    ).toHaveTextContent("query");

    // A type the analysis could not follow, and a tool defined outside the analysed files.
    act(() => {
      window.location.hash = "#/agents/a4";
    });
    await screen.findByRole("heading", { level: 2, name: "fact_checker" });
    const checker = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(checker).getByText(/Output of fact_checker/)).toHaveTextContent(
      "Verdict[Claim]. The type is shown as the source names it",
    );
    await user.click(screen.getByRole("button", { name: "Tool archive_search" }));
    expect(within(checker).getByText(/defined outside the files that were read/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Tool lookup_source" }));
    expect(
      within(checker).getByRole("table", { name: "Parameters of lookup_source" }),
    ).toHaveTextContent("Claim[Source]");
    expect(within(checker).getByTitle("newsroom/checks.py:27")).toBeInTheDocument();
  });
});

describe("a toolset, and the parts a panel names", () => {
  const details = (): HTMLElement =>
    screen.getByRole("complementary", { name: /Blueprint details|Span details/ });
  const title = (): string | null =>
    within(details()).getByRole("heading", { level: 3 }).textContent;

  test("a toolset lists its tools, and each one is picked as on the canvas", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    await user.click(
      screen.getByRole("button", { name: "Toolset FunctionToolset 'wires', 2 tools" }),
    );
    expect(title()).toBe("FunctionToolset 'wires'");
    // The box is outlined on the canvas, as a picked tool is.
    expect(document.querySelector(".react-flow__node-toolset .pviz-selected")).not.toBeNull();

    const tools = within(details()).getByRole("list", { name: "Tools of FunctionToolset 'wires'" });
    expect(
      within(tools)
        .getAllByRole("button")
        .map((row) => row.textContent),
    ).toEqual([
      "search_wires3 parametersSearch the wire services.",
      "fetch_article1 parameterFetch the full text of a wire article.",
    ]);
    expect(within(details()).getByText("Function toolset")).toBeInTheDocument();
    expect(within(details()).getByTitle("newsroom/reporters.py:12")).toBeInTheDocument();

    // A tool picked from the list: its panel, its node outlined, focus on its title.
    await user.click(within(tools).getByRole("button", { name: /^search_wires/ }));
    expect(title()).toBe("search_wires");
    await vi.waitFor(() =>
      expect(document.activeElement).toBe(
        within(details()).getByRole("heading", { level: 3, name: "search_wires" }),
      ),
    );
    expect(screen.getByRole("button", { name: "Tool search_wires" }).parentElement).toHaveClass(
      "pviz-selected",
    );
    expect(document.querySelector(".react-flow__node-toolset .pviz-selected")).toBeNull();

    // The tool's panel leads back up: to its toolset, and to its agent.
    const header = within(details()).getByRole("heading", { level: 3 }).parentElement;
    expect(header?.querySelector("p:last-of-type")).toHaveTextContent(
      /^in FunctionToolset 'wires' of reporter$/,
    );
    await user.click(within(details()).getByRole("button", { name: "FunctionToolset 'wires'" }));
    expect(title()).toBe("FunctionToolset 'wires'");
    await user.click(within(details()).getByRole("button", { name: "reporter" }));
    expect(title()).toBe("reporter");

    // The agent's own panel names every tool under its toolset, each one a way in.
    const ownTools = within(details()).getByRole("list", { name: "Tools of Agent tools" });
    await user.click(within(ownTools).getByRole("button", { name: "ask_stringer" }));
    expect(title()).toBe("ask_stringer");
    await user.click(within(details()).getByRole("button", { name: "reporter" }));
    await user.click(within(details()).getByRole("button", { name: "MCPServerStdio" }));
    expect(title()).toBe("MCPServerStdio");
  });

  test("a picked toolset, and a tool picked from its list, are links that open on them", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a2";
    const first = render(<App plan={embedded(NEWSROOM)} />);
    const length = window.history.length;
    await user.click(
      screen.getByRole("button", { name: "Toolset FunctionToolset 'wires', 2 tools" }),
    );
    expect(window.location.hash).toMatch(/^#\/agents\/a2\?part=toolset%3Ats\d+$/);
    const toolsetLink = window.location.hash;
    const tools = within(details()).getByRole("list", { name: "Tools of FunctionToolset 'wires'" });
    await user.click(within(tools).getByRole("button", { name: /^fetch_article/ }));
    expect(window.location.hash).toBe("#/agents/a2?part=tool%3Afetch_article");
    // A pick replaces the link: Back leaves the screen, not the last part picked.
    expect(window.history.length).toBe(length);
    // Back to the agent itself: its link has no part.
    await user.click(within(details()).getByRole("button", { name: "reporter" }));
    expect(window.location.hash).toBe("#/agents/a2");
    first.unmount();

    // Opened from the links: the tool's panel and its node outlined, then the toolset's.
    window.location.hash = "#/agents/a2?part=tool%3Afetch_article";
    const second = render(<App plan={embedded(NEWSROOM)} />);
    expect(title()).toBe("fetch_article");
    expect(screen.getByRole("button", { name: "Tool fetch_article" }).parentElement).toHaveClass(
      "pviz-selected",
    );
    act(() => {
      window.location.hash = toolsetLink;
    });
    await vi.waitFor(() => expect(title()).toBe("FunctionToolset 'wires'"));
    expect(document.querySelector(".react-flow__node-toolset .pviz-selected")).not.toBeNull();
    second.unmount();

    // A link to a tool this agent does not have shows the agent.
    window.location.hash = "#/agents/a2?part=tool%3Aghost";
    render(<App plan={embedded(NEWSROOM)} />);
    expect(title()).toBe("reporter");
  });

  test("a toolset's id is shown, except the placeholder that stands for the agent's own tools", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    const definition = (): HTMLElement =>
      within(details()).getByRole("heading", { level: 4, name: "Definition" }).closest("section") ??
      details();
    await user.click(
      screen.getByRole("button", { name: "Toolset FunctionToolset 'wires', 2 tools" }),
    );
    expect(within(definition()).getByText("wires")).toBeInTheDocument();
    // The reporter's own tools; the stringer it delegates to has its own as well.
    const [own] = screen.getAllByRole("button", { name: /^Toolset Agent tools/ });
    if (!own) throw new Error("no toolset");
    await user.click(own);
    expect(within(definition()).getByText("The agent's own tools")).toBeInTheDocument();
    expect(within(definition()).queryByText("Id")).toBeNull();
    expect(within(details()).queryByText("<agent>")).toBeNull();
  });

  test("a picked graph node is a link that opens on it", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/graphs/g1";
    const first = render(<App plan={embedded(NEWSROOM)} />);
    await user.click(screen.getByRole("button", { name: "Step Review" }));
    expect(window.location.hash).toMatch(/^#\/graphs\/g1\?node=/);
    const link = window.location.hash;
    // The graph's own name in the node's panel picks the graph again.
    await user.click(within(details()).getByRole("button", { name: "publishing" }));
    expect(window.location.hash).toBe("#/graphs/g1");
    first.unmount();

    window.location.hash = link;
    const second = render(<App plan={embedded(NEWSROOM)} />);
    expect(title()).toBe("Review");
    expect(screen.getByRole("button", { name: "Step Review" }).parentElement).toHaveClass(
      "pviz-selected",
    );
    second.unmount();
    window.location.hash = "#/graphs/g1?node=ghost";
    render(<App plan={embedded(NEWSROOM)} />);
    expect(screen.queryByRole("button", { name: "Step Review" })?.parentElement).not.toHaveClass(
      "pviz-selected",
    );
    expect(within(details()).getByRole("heading", { level: 3 })).not.toHaveTextContent("Review");
  });

  test("a toolset that lists its tools only when the code runs says so, from its placeholder too", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    // Said once, in the words used for every value the code decides: the box's header has no
    // count, and the agent's panel says the same of it.
    expect(screen.getByRole("button", { name: "Toolset MCPServerStdio" })).toBeInTheDocument();
    expect(screen.getByText("decided at run time", { selector: "div" })).toBeInTheDocument();
    expect(screen.queryByText(/dynamic|resolved/)).toBeNull();
    const toolsets = details().querySelector('[data-section="toolsets"]') as HTMLElement;
    expect(within(toolsets).getByText("decided at run time")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "Toolset MCPServerStdio: its tools are decided at run time",
      }),
    );
    expect(title()).toBe("MCPServerStdio");
    expect(within(details()).getByText(/lists its tools only when the code runs/)).toBeVisible();
    expect(within(details()).getByText("MCP server")).toBeInTheDocument();
    expect(within(details()).getByText("PrefixedToolset")).toBeInTheDocument();
    expect(within(details()).getByText('MCPServerStdio("uvx", args=["mcp-archive"])').tagName).toBe(
      "CODE",
    );
    expect(within(details()).getByText("Decided at run time")).toBeInTheDocument();
  });

  test("a delegating tool is a way to its tool, and the delegate a link to its agent", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded(NEWSROOM)} />);
    const delegates = details().querySelector('[data-section="delegates"]') as HTMLElement;
    await user.click(within(delegates).getByRole("button", { name: "check_facts" }));
    expect(title()).toBe("check_facts");
    expect(within(details()).getByRole("link", { name: "fact_checker" })).toHaveAttribute(
      "href",
      "#/agents/a4",
    );
  });

  test("a graph's nodes, and the nodes an edge joins, are picked from the panel", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/graphs/g1";
    render(<App plan={embedded(NEWSROOM)} />);
    const nodes = details().querySelector('[data-section="nodes"]') as HTMLElement;
    await user.click(within(nodes).getByRole("button", { name: "Review" }));
    expect(title()).toBe("Review");
    expect(screen.getByRole("button", { name: "Step Review" }).parentElement).toHaveClass(
      "pviz-selected",
    );
    const leadsTo = details().querySelector('[data-section="to"]') as HTMLElement;
    await user.click(within(leadsTo).getByRole("button", { name: "decision" }));
    expect(title()).toBe("decision");
    await user.click(within(details()).getByRole("button", { name: "publishing" }));
    expect(title()).toBe("publishing");
  });

  test("on a run, a tool picked from its toolset's panel selects its latest call", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const triage = runs(useViewer.getState().data).find((run) => run.name === "triage");
    await openRun(triage?.traceId, "triage");
    await user.click(await screen.findByRole("button", { name: "Skip to end" }));
    await user.click(screen.getByRole("button", { name: "Toolset Agent tools, 3 tools" }));
    expect(title()).toBe("Agent tools");
    await user.click(within(details()).getByRole("button", { name: /^get_order/ }));
    await vi.waitFor(() => expect(window.location.hash).toMatch(/\?span=/));
    // The span of the last get_order call, as a click on the tool on the canvas selects it.
    const spanId = new URLSearchParams(window.location.hash.split("?")[1]).get("span");
    const span = spanId ? useViewer.getState().data.spans[spanId] : undefined;
    expect(span?.kind === "tool_call" && span.data.tool_name).toBe("get_order");
    expect(title()).toBe("get_order");
  });
});

describe("the warning count above the canvas", () => {
  test("opens the blueprint's own panel at its warnings, from a tool or a closed panel", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    const main = screen.getByRole("main");
    const chip = within(main).getByRole("button", {
      name: "3 warnings: show them in the panel",
    });

    // From a tool's panel, back to the agent's, where the warnings are listed.
    await user.click(screen.getByRole("button", { name: "Tool search_wires" }));
    expect(screen.getByRole("heading", { level: 3, name: "search_wires" })).toBeInTheDocument();
    await user.click(chip);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByRole("heading", { level: 3, name: "reporter" })).toBeVisible();
    expect(details.querySelector('[data-section="warnings"]')).not.toBeNull();

    // A closed panel opens for it.
    await user.click(screen.getByRole("button", { name: "Hide blueprint details" }));
    expect(screen.queryByRole("complementary", { name: "Blueprint details" })).toBeNull();
    await user.click(chip);
    expect(screen.getByRole("complementary", { name: "Blueprint details" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide blueprint details" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  test("is not there for a blueprint without warnings", () => {
    window.location.hash = "#/agents/a3";
    render(<App plan={embedded(NEWSROOM)} />);
    expect(within(screen.getByRole("main")).queryByRole("button", { name: /warning/ })).toBeNull();
  });
});

describe("Copy Mermaid", () => {
  test("Copy Mermaid works for an agent, as it does for a graph", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    window.location.hash = "#/agents/a2";
    render(<App plan={embedded(NEWSROOM)} />);
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    const [copied] = writeText.mock.calls.at(-1) ?? [];
    expect(copied).toMatch(/^flowchart LR\n/);
    expect(copied).toContain('agent1["reporter<br/>model: env NEWSROOM_MODEL"]');
    expect(copied).toContain('agent1_tool1 -. "found in code" .-> agent2');
    // The same text can be read in the panel before it is copied.
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    await user.click(within(details).getByText("Show the source"));
    expect(details.querySelector("pre")?.textContent).toBe(copied);

    act(() => {
      window.location.hash = "#/graphs/g1";
    });
    await screen.findByRole("heading", { level: 2, name: "publishing" });
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    expect(writeText).toHaveBeenLastCalledWith(expect.stringContaining("stateDiagram-v2"));
  });

  test("a run view offers the Mermaid of the blueprint it is drawn on", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<App plan={embedded("sample.pviz.jsonl")} />);
    const [pipeline, triage] = runs(useViewer.getState().data);
    await openRun(triage?.traceId, "triage");
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    expect(writeText).toHaveBeenLastCalledWith(
      expect.stringMatching(/^flowchart LR\n {2}agent1\["triage/),
    );
    await openRun(pipeline?.traceId, "pipeline");
    await user.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    expect(writeText).toHaveBeenLastCalledWith(expect.stringContaining("stateDiagram-v2"));
  });
});

describe("SourceRef", () => {
  test("a long path gives way from its folder, then its name; the line is never cut", () => {
    const location = { file: "pydantic_ai_examples/medical_agent_delegation.py", line: 161 };
    const { container } = render(<SourceRef location={location} />);
    const code = screen.getByTitle("pydantic_ai_examples/medical_agent_delegation.py:161");
    const [folder, tail] = [...(code.firstElementChild?.children ?? [])] as HTMLElement[];
    const [name, line] = [...(tail?.children ?? [])] as HTMLElement[];
    expect([folder, name, line].map((part) => part?.textContent)).toEqual([
      "pydantic_ai_examples/",
      "medical_agent_delegation.py",
      ":161",
    ]);
    // The folder takes what room the name and line leave it, down to two characters (the
    // ellipsis and its last slash). They do not shrink as long as they fit: only a name too long
    // on its own is cut, never the line.
    expect(folder).toHaveClass("min-w-[2ch]", "truncate");
    // Cut from its start, so the folders nearest the file stay; its slashes stay in place.
    expect(folder).toHaveAttribute("dir", "rtl");
    expect(folder?.firstElementChild?.tagName).toBe("BDI");
    expect(folder?.firstElementChild).toHaveAttribute("dir", "ltr");
    expect(tail).toHaveClass("shrink-0", "max-w-[calc(100%-2ch)]", "min-w-0");
    expect(name).toHaveClass("min-w-0", "truncate");
    expect(line).toHaveClass("shrink-0");
    expect(code).toHaveTextContent(/^pydantic_ai_examples\/medical_agent_delegation\.py:161$/);
    expect(container).toHaveTextContent("Defined at");
    expect(
      screen.getByRole("button", {
        name: "Copy pydantic_ai_examples/medical_agent_delegation.py:161",
      }),
    ).toBeInTheDocument();
  });

  test("a file at the top of the analysed tree has no folder part", () => {
    render(<SourceRef location={{ file: "01_tools.py", line: 22 }} prefix="" />);
    const code = screen.getByTitle("01_tools.py:22");
    const parts = [...(code.firstElementChild?.children ?? [])];
    expect(parts).toHaveLength(1);
    // With no folder to leave a character for, the name may take the whole width.
    expect(parts[0]).toHaveClass("max-w-full");
    expect([...(parts[0]?.children ?? [])].map((part) => part.textContent)).toEqual([
      "01_tools.py",
      ":22",
    ]);
    expect(screen.queryByText("Defined at")).toBeNull();
  });
});

describe("a tool in a toolset's list", () => {
  test("counts the parameters its schema names, and says when that is not known", () => {
    expect(parameterCount({ type: "object", properties: { a: {}, b: {} } })).toBe(2);
    expect(parameterCount({ type: "object", properties: {}, additionalProperties: false })).toBe(0);
    expect(parameterCount({ type: "object" })).toBe(0);
    // Defined outside the files that were read, or a type the analysis could not follow.
    expect(parameterCount(null)).toBeNull();
    expect(parameterCount({ "x-python-type": "Params" })).toBeNull();
    expect(parameterCount("string")).toBeNull();
  });

  test("is described by the first line of its docstring that has text", () => {
    expect(firstLine("\n  Search the wires.\n\n  Args:\n    q: what to find\n")).toBe(
      "Search the wires.",
    );
    expect(firstLine("One line")).toBe("One line");
    expect(firstLine("  \n ")).toBeNull();
    expect(firstLine(null)).toBeNull();
  });
});

describe("an instruction written across lines", () => {
  test("is shown without the blank lines the source wraps it in", () => {
    expect(trimBlankLines("\nYou are a triage clinician.\nBe brief.\n")).toBe(
      "You are a triage clinician.\nBe brief.",
    );
    expect(trimBlankLines("\n  \n    Indented first line.\n\n  ")).toBe("    Indented first line.");
    // A blank line inside the text is part of it, and so are spaces at the end of a line.
    expect(trimBlankLines("One.\n\nTwo.  ")).toBe("One.\n\nTwo.  ");
    expect(trimBlankLines("")).toBe("");
  });

  test("the panel shows the text from its first line", () => {
    const agent = agentBlueprint({
      id: "a1",
      source: "code",
      instructions: [
        {
          kind: "instructions",
          dynamic: false,
          text: "\nYou are a triage clinician.\n",
          function: null,
          expression: null,
          location: null,
        },
      ],
    });
    const events = log(
      [0, { ...sessionStarted(), mode: "blueprint" }],
      [1, { type: "blueprint.agent", agent }],
    );
    window.location.hash = "#/agents/a1";
    render(<App plan={{ kind: "embedded", events }} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByText(/You are a triage clinician\./).textContent).toBe(
      "You are a triage clinician.",
    );
  });
});

describe("a model the code works out", () => {
  const render1 = (model: AgentBlueprint["model"]): HTMLElement => {
    const agent = agentBlueprint({ id: "a1", source: "code", model });
    const events = log(
      [0, { ...sessionStarted(), mode: "blueprint" }],
      [1, { type: "blueprint.agent", agent }],
    );
    window.location.hash = "#/agents/a1";
    render(<App plan={{ kind: "embedded", events }} />);
    return screen.getByRole("complementary", { name: "Blueprint details" });
  };

  test("a computed model is the expression, decided at run time", () => {
    const details = render1({
      id: null,
      name: "get_model()",
      provider: null,
      offline: false,
      expression: "get_model()",
      model_class: null,
      env: null,
    });
    expect(within(details).getByText("Set by")).toBeInTheDocument();
    expect(within(details).getByText("get_model()", { selector: "dd code" })).toBeVisible();
    expect(within(details).getByText("Decided at run time")).toBeInTheDocument();
    expect(within(details).queryByText("Name")).toBeNull();
  });

  test("a model read from the environment without a default says the variable must be set", () => {
    const details = render1({
      id: null,
      name: 'os.environ["MODEL"]',
      provider: null,
      offline: false,
      expression: 'os.environ["MODEL"]',
      model_class: null,
      env: { variable: "MODEL", default: null },
    });
    expect(within(details).getByText("Model", { selector: "dt" })).toBeInTheDocument();
    expect(within(details).getByText("MODEL", { selector: "dd code" })).toBeVisible();
    expect(within(details).getByText("None in the source: MODEL has to be set.")).toBeVisible();
    expect(within(details).getByText("Set by")).toBeInTheDocument();
    expect(within(details).queryByText("Class")).toBeNull();
    expect(screen.getByText("env MODEL").closest("[title]")).toHaveAttribute(
      "title",
      "From environment variable MODEL at run time; no default",
    );
  });
});

describe("the capabilities of an agent", () => {
  const toolset: Toolset = {
    id: "ts0",
    toolset_id: null,
    label: "FunctionToolset 'kb'",
    kind: "function",
    wrappers: [],
    opaque: false,
    expression: null,
    location: null,
  };

  test("are grouped as the Pydantic AI docs group them, with what each one was given", async () => {
    const user = userEvent.setup();
    const agent = agentBlueprint({
      id: "a1",
      toolsets: [toolset],
      capabilities: [
        capability({ name: "AuditTrail" }),
        capability({
          name: "Thinking",
          category: "reasoning",
          package: "pydantic_ai",
          details: { effort: "high" },
        }),
        capability({
          name: "ModelFallback",
          category: "loop",
          package: "pydantic_ai",
          details: { models: ["openai:gpt-5", "test"] },
        }),
        capability({
          name: "Memory",
          category: "knowledge",
          package: "pydantic_ai_harness",
          toolsets: ["ts0"],
          deferred: true,
        }),
        capability({
          name: "MCP",
          category: "tools",
          expressions: { url: "os.environ['URL']" },
          native_tools: ["mcp_server"],
        }),
        capability({ name: "Future", category: "quantum" as never }),
        capability({ name: "ToolSearch", builtin: true, category: "context" }),
      ],
    });
    const events = log(
      [0, { ...sessionStarted(), mode: "blueprint" }],
      [1, { type: "blueprint.agent", agent }],
    );
    window.location.hash = "#/agents/a1";
    render(<App plan={{ kind: "embedded", events }} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    const panel = within(details).getByRole("region", { name: "Agent blueprint" });
    expect(
      within(panel).getByRole("heading", { level: 4, name: /^Capabilities/ }),
    ).toHaveTextContent("Capabilities6");

    const groups = within(panel).getAllByRole("heading", { level: 5 });
    expect(groups.map((h) => h.textContent)).toEqual([
      "Tools & native abilities 1",
      "Reasoning, planning & delegation 1",
      "Knowledge & memory 1",
      "Loop customization 1",
      "Custom 2",
    ]);
    const custom = within(panel).getByRole("region", { name: "Custom" });
    expect(within(custom).getByText("AuditTrail")).toBeInTheDocument();
    expect(within(custom).getByText("Future")).toBeInTheDocument();

    expect(within(panel).getAllByText("Core")).toHaveLength(2);
    expect(within(panel).getByText("Harness")).toBeInTheDocument();
    expect(within(panel).getByText("on demand")).toBeInTheDocument();
    expect(within(panel).getByText("high")).toBeInTheDocument();
    expect(within(panel).getByText("openai:gpt-5")).toBeInTheDocument();
    expect(within(panel).getByText("test")).toBeInTheDocument();
    expect(within(panel).getByText("os.environ['URL']").tagName).toBe("CODE");
    expect(within(panel).getByText("mcp_server")).toBeInTheDocument();
    expect(within(panel).getByText("ToolSearch")).not.toBeVisible();

    const link = within(panel).getAllByRole("button", { name: "FunctionToolset 'kb'" });
    await user.click(link[link.length - 1] as HTMLElement);
    const toolsetPanel = await screen.findByRole("region", { name: "Toolset" });
    expect(within(toolsetPanel).getByText("Given by")).toBeInTheDocument();
    expect(within(toolsetPanel).getByText("Memory")).toBeInTheDocument();
  });

  test("list a built-in one the agent configures under its group, and fold the plain ones", () => {
    const agent = agentBlueprint({
      id: "a1",
      capabilities: [
        capability({
          name: "ToolSearch",
          builtin: true,
          category: "context",
          details: { max_results: 3 },
          native_tools: ["tool_search"],
        }),
        capability({
          name: "PendingMessageDrainCapability",
          builtin: true,
          category: "runtime",
          native_tools: ["tool_search"],
        }),
      ],
    });
    const events = log(
      [0, { ...sessionStarted(), mode: "blueprint" }],
      [1, { type: "blueprint.agent", agent }],
    );
    window.location.hash = "#/agents/a1";
    render(<App plan={{ kind: "embedded", events }} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    const panel = within(details).getByRole("region", { name: "Agent blueprint" });
    const context = within(panel).getByRole("region", { name: "Context management" });
    expect(within(context).getByText("ToolSearch")).toBeVisible();
    expect(within(context).getByText("3")).toBeVisible();
    expect(within(panel).getByText("PendingMessageDrainCapability")).not.toBeVisible();
  });

  describe("on the canvas", () => {
    const withTools: Toolset = { ...toolset, label: "MemoryToolset 'memory'" };
    const agent = agentBlueprint({
      id: "a1",
      toolsets: [withTools],
      tools: [
        {
          name: "write_memory",
          toolset: "ts0",
          description: null,
          parameters_schema: {},
          return_schema: null,
          kind: "function",
          takes_ctx: null,
          is_async: null,
          max_retries: null,
          function: null,
          origin: "object",
          location: null,
          library_version: null,
        },
      ],
      capabilities: [
        capability({ name: "ToolSearch", builtin: true, category: "context" }),
        capability({ name: "Thinking", category: "reasoning", details: { effort: "high" } }),
        capability({ name: "AuditTrail" }),
        capability({
          name: "Memory",
          category: "knowledge",
          package: "pydantic_ai_harness",
          toolsets: ["ts0"],
          deferred: true,
        }),
      ],
    });
    const open = (hash: string) => {
      window.location.hash = hash;
      return render(
        <App
          plan={{
            kind: "embedded",
            events: log(
              [0, { ...sessionStarted(), mode: "blueprint" }],
              [1, { type: "blueprint.agent", agent }],
            ),
          }}
        />,
      );
    };
    const details = (): HTMLElement =>
      screen.getByRole("complementary", { name: "Blueprint details" });
    const title = (): string | null =>
      within(details()).getByRole("heading", { level: 3 }).textContent;

    test("a capability picked on the canvas opens its panel, and its link opens on it", async () => {
      const user = userEvent.setup();
      const first = open("#/agents/a1");
      // The built-in one is not drawn.
      expect(screen.queryByRole("button", { name: /^Capability ToolSearch/ })).toBeNull();
      await user.click(
        screen.getByRole("button", {
          name: "Capability Memory, Knowledge & memory, gives toolset MemoryToolset 'memory', on demand",
        }),
      );
      expect(window.location.hash).toBe("#/agents/a1?part=capability%3A3");
      expect(title()).toBe("Memory");
      expect(within(details()).getByText("Capability")).toBeInTheDocument();
      expect(within(details()).getByText("Harness")).toBeInTheDocument();
      expect(within(details()).getByText("on demand")).toBeInTheDocument();
      expect(within(details()).getByText("Knowledge & memory")).toBeInTheDocument();
      expect(within(details()).getByText("Loaded")).toBeInTheDocument();
      // Its toolset leads on, and the toolset leads back.
      await user.click(within(details()).getByRole("button", { name: "MemoryToolset 'memory'" }));
      expect(title()).toBe("MemoryToolset 'memory'");
      await user.click(within(details()).getByRole("button", { name: "Memory" }));
      expect(title()).toBe("Memory");
      // Back up to the agent, and down again from its list of capabilities.
      await user.click(within(details()).getByRole("button", { name: "a1" }));
      expect(window.location.hash).toBe("#/agents/a1");
      await user.click(within(details()).getByRole("button", { name: "Thinking" }));
      expect(window.location.hash).toBe("#/agents/a1?part=capability%3A1");
      expect(within(details()).getByText("high")).toBeInTheDocument();
      first.unmount();

      const second = open("#/agents/a1?part=capability%3A2");
      expect(title()).toBe("AuditTrail");
      expect(
        screen.getByRole("button", { name: "Capability AuditTrail, Custom" }).parentElement,
      ).toHaveClass("pviz-selected");
      expect(within(details()).getByText(/gives the agent no toolset/)).toBeInTheDocument();
      second.unmount();

      // A built-in one has a panel too, though the canvas does not draw it.
      const third = open("#/agents/a1?part=capability%3A0");
      expect(title()).toBe("ToolSearch");
      expect(within(details()).getByText("Added by Pydantic AI to every agent.")).toBeVisible();
      third.unmount();

      // An index the agent does not have shows the agent.
      open("#/agents/a1?part=capability%3A9");
      expect(title()).toBe("a1");
    });

    test("the canvas explains the dashed edge from a capability to its toolset", () => {
      open("#/agents/a1");
      expect(screen.getByText("capability gives toolset")).toBeInTheDocument();
      expect(screen.queryByText("delegation found in code")).toBeNull();
    });

    test("a sub-agent's capability picked on the canvas opens on that agent, not this one's of its index", async () => {
      const user = userEvent.setup();
      const tool = (name: string) =>
        ({ ...agent.tools[0], name }) as AgentBlueprint["tools"][number];
      const hub = agentBlueprint({
        id: "hub",
        toolsets: [toolset],
        tools: [tool("ask_worker")],
        delegates: [{ tool_name: "ask_worker", agent_id: "worker", evidence: "object" }],
        capabilities: [
          capability({ name: "Thinking", category: "reasoning" }),
          capability({ name: "HubOnly", category: "safety" }),
        ],
      });
      const worker = agentBlueprint({
        id: "worker",
        toolsets: [toolset],
        tools: [tool("work")],
        capabilities: [
          capability({ name: "WorkerA", category: "reasoning" }),
          capability({ name: "WorkerB", category: "safety" }),
        ],
      });
      window.location.hash = "#/agents/hub";
      render(
        <App
          plan={{
            kind: "embedded",
            events: log(
              [0, { ...sessionStarted(), mode: "blueprint" }],
              [1, { type: "blueprint.agent", agent: hub }],
              [2, { type: "blueprint.agent", agent: worker }],
            ),
          }}
        />,
      );
      await user.click(screen.getByRole("button", { name: /^Capability WorkerB/ }));
      expect(window.location.hash).toBe("#/agents/worker?part=capability%3A1");
      await screen.findByRole("heading", { level: 3, name: "WorkerB" });
      expect(screen.getByRole("button", { name: /^Capability WorkerB/ }).parentElement).toHaveClass(
        "pviz-selected",
      );
    });
  });
});

describe("the hooks of a Hooks capability", () => {
  const agent = agentBlueprint({
    id: "a1",
    capabilities: [
      capability({
        name: "Hooks",
        category: "loop",
        package: "pydantic_ai",
        hooks: [
          hook({
            point: "before_model_request",
            function: "app.log_request",
            location: { file: "app.py", line: 9 },
          }),
          hook({
            point: "before_tool_execute",
            function: "app.guard",
            tools: ["delete_file"],
            timeout: 5,
            location: { file: "app.py", line: 14 },
          }),
        ],
      }),
    ],
  });

  test("a hook picked on the canvas opens its capability's panel at its hooks", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(
      <App
        plan={{
          kind: "embedded",
          events: log(
            [0, { ...sessionStarted(), mode: "blueprint" }],
            [1, { type: "blueprint.agent", agent }],
          ),
        }}
      />,
    );
    expect(screen.getByRole("button", { name: /^Capability Hooks, .*2 hooks$/ })).toBeVisible();
    await user.click(
      screen.getByRole("button", {
        name: "Hook before_tool_execute of Hooks: guard, for delete_file, times out after 5 s",
      }),
    );
    expect(window.location.hash).toBe("#/agents/a1?part=hook%3A0%3A1");
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByRole("heading", { level: 3 }).textContent).toBe("Hooks");
    const hooks = within(details).getByRole("list", { name: "Hooks of Hooks" });
    const items = within(hooks).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringMatching(/^before_model_request→app\.log_requestapp\.py:9/),
      expect.stringMatching(
        /^before_tool_execute→app\.guardOnly for delete_file · Times out after 5 sapp\.py:14/,
      ),
    ]);
  });
});

describe("the output validators of an agent", () => {
  test("the output section names them in registration order", () => {
    const base = agentBlueprint({ id: "a1" });
    const agent = {
      ...base,
      output: { ...base.output, validators: ["app.not_empty", "app.short"] },
    };
    window.location.hash = "#/agents/a1";
    render(
      <App
        plan={{
          kind: "embedded",
          events: log(
            [0, { ...sessionStarted(), mode: "blueprint" }],
            [1, { type: "blueprint.agent", agent }],
          ),
        }}
      />,
    );
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    expect(within(details).getByText("Validated by")).toBeInTheDocument();
    expect(within(details).getByText("app.not_empty, app.short")).toBeInTheDocument();
  });
});

describe("skills, and what Pydantic AI adds by default", () => {
  const houseStyle = skill({
    name: "house-style",
    description: "How the newsroom formats a short briefing.\nIn three bullets.",
    when_to_use: "Before writing the final briefing for a reader.",
    path: "_skills/house-style/SKILL.md",
    body: "\n# House style for briefings\n\nWrite every briefing in this shape.\n",
  });
  const secret = skill({ name: "secret", model_invocable: false, body: "" });
  const agent = agentBlueprint({
    id: "a1",
    capabilities: [
      capability({ name: "ToolSearch", builtin: true, category: "context" }),
      capability({
        name: "Skills",
        category: "knowledge",
        package: "pydantic_ai_harness",
        skills: [houseStyle, secret],
      }),
    ],
    settings: {
      ...agentBlueprint({ id: "x" }).settings,
      end_strategy: "early",
      tool_retries: 3,
      defaults: ["end_strategy"],
    },
  });
  const open = (hash: string) => {
    window.location.hash = hash;
    return render(
      <App
        plan={{
          kind: "embedded",
          events: log(
            [0, { ...sessionStarted(), mode: "blueprint" }],
            [1, { type: "blueprint.agent", agent }],
          ),
        }}
      />,
    );
  };
  const details = (): HTMLElement =>
    screen.getByRole("complementary", { name: "Blueprint details" });
  const title = (): string | null =>
    within(details()).getByRole("heading", { level: 3 }).textContent;

  test("a skill picked on the canvas shows when to use it and the instructions the model gets", async () => {
    const user = userEvent.setup();
    open("#/agents/a1");
    await user.click(
      screen.getByRole("button", { name: "Skill house-style of Skills, loaded on demand" }),
    );
    expect(window.location.hash).toBe("#/agents/a1?part=skill%3A1%3Ahouse-style");
    expect(title()).toBe("house-style");
    const panel = within(details()).getByRole("region", { name: "Skill" });
    expect(within(details()).getByText("Skill")).toBeInTheDocument();
    expect(within(details()).getByText("on demand")).toBeInTheDocument();
    expect(within(panel).getByText(houseStyle.when_to_use ?? "")).toBeInTheDocument();
    // The body without the blank lines around it, and a way to copy it.
    expect(within(panel).getByText(/^# House style for briefings/).textContent).toBe(
      "# House style for briefings\n\nWrite every briefing in this shape.",
    );
    expect(within(panel).getByRole("button", { name: "Copy the instructions" })).toBeVisible();
    expect(within(panel).getByText("_skills/house-style/")).toBeInTheDocument();
    expect(within(panel).getByText("load_capability")).toBeInTheDocument();
    // Up to the capability that offers it, which lists its skills.
    await user.click(within(details()).getByRole("button", { name: "Skills" }));
    expect(title()).toBe("Skills");
    const skills = within(details()).getByRole("list", { name: "Skills of Skills" });
    expect(
      within(skills)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "house-styleHow the newsroom formats a short briefing.",
      `secrethidden${secret.description}`,
    ]);
    await user.click(within(skills).getByRole("button", { name: /^secret/ }));
    expect(title()).toBe("secret");
    expect(within(details()).getByText("hidden from the model")).toBeInTheDocument();
    expect(within(details()).queryByText("When to use")).toBeNull();
    expect(within(details()).getByText(/nothing after the frontmatter/)).toBeInTheDocument();
    // The agent's list of capabilities names its skills too.
    await user.click(within(details()).getByRole("button", { name: "a1" }));
    await user.click(within(details()).getByRole("button", { name: "house-style" }));
    expect(title()).toBe("house-style");
  });

  test("a skill a link names that the agent does not have shows the agent", () => {
    open("#/agents/a1?part=skill%3A1%3Aghost");
    expect(title()).toBe("a1");
  });

  test("the defaults toggle draws them, says so, and is remembered", async () => {
    const user = userEvent.setup();
    const first = open("#/agents/a1");
    const toggle = screen.getByRole("button", { name: "Pydantic AI defaults +1" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAttribute("title", "Show the 1 part Pydantic AI adds by default");
    expect(toggle).not.toHaveClass("text-accent!");
    expect(screen.queryByRole("button", { name: /^Capability ToolSearch/ })).toBeNull();
    // Hidden, a default setting is folded away under its own heading.
    const panel = within(details()).getByRole("region", { name: "Agent blueprint" });
    expect(within(panel).getByText("Pydantic AI defaults")).toBeVisible();
    expect(within(panel).getByText("End strategy")).not.toBeVisible();
    expect(within(panel).getByText("Tool retries")).toBeVisible();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAttribute("title", "Hide the 1 part Pydantic AI adds by default");
    // Pressed looks it, apart from a hover: in the accent colour.
    expect(toggle).toHaveClass("text-accent!");
    expect(within(toggle).getByText("+1")).toHaveClass("tone-accent");
    expect(screen.getByText("Showing 1 part from Pydantic AI")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Capability ToolSearch, .*a Pydantic AI default$/ }),
    ).toBeInTheDocument();
    expect(within(panel).getByText("End strategy")).toBeVisible();
    expect(within(panel).getByText("default")).toBeVisible();
    first.unmount();

    open("#/agents/a1");
    expect(screen.getByRole("button", { name: "Pydantic AI defaults +1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("the toggle still works when storage refuses to keep the choice", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    try {
      open("#/agents/a1");
      const toggle = screen.getByRole("button", { name: "Pydantic AI defaults +1" });
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "true");
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "false");
    } finally {
      vi.restoreAllMocks();
    }
  });

  test("in a run, a load_capability call says which skill it loaded, and outlines it", async () => {
    const user = userEvent.setup();
    const named = { ...agent, name: "briefing", label: "briefing" };
    const root = agentRun({ id: "root", start: 0, end: 50, agent: "briefing" });
    const loadSpan = toolCall({
      id: "load",
      parent: "root",
      start: 5,
      end: 7,
      tool: "load_capability",
      agent: "briefing",
    });
    const load = { ...loadSpan, data: { ...loadSpan.data, arguments: { id: "house-style" } } };
    window.location.hash = `#/runs/${TRACE}`;
    render(
      <App
        plan={{
          kind: "embedded",
          events: log([1, { type: "blueprint.agent", agent: named }], ...lifecycle(root, load)),
        }}
      />,
    );
    await screen.findByRole("heading", { level: 2, name: "briefing" });
    await user.click(await screen.findByRole("button", { name: "Skip to end" }));
    const row = within(screen.getByRole("tree"))
      .getAllByRole("treeitem")
      .find((item) => item.getAttribute("data-span") === sid("load"));
    if (!row) throw new Error("expected the load_capability row");
    await user.click(row);
    const span = screen.getByRole("complementary", { name: "Span details" });
    expect(within(span).getByText(/^Loads skill/).textContent).toBe(
      "Loads skill house-style of Skills",
    );
    expect(
      screen.getByRole("button", { name: /^Skill house-style of Skills/ }).parentElement,
    ).toHaveClass("pviz-selected");
  });

  test("is not offered for an agent Pydantic AI added nothing to", () => {
    const plain = agentBlueprint({ id: "a1" });
    window.location.hash = "#/agents/a1";
    render(
      <App
        plan={{
          kind: "embedded",
          events: log(
            [0, { ...sessionStarted(), mode: "blueprint" }],
            [1, { type: "blueprint.agent", agent: plain }],
          ),
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /^Pydantic AI defaults/ })).toBeNull();
  });
});

describe("tool calls left for the caller", () => {
  const APPROVALS = "approvals.pviz.jsonl";
  const spanDetails = (): HTMLElement =>
    screen.getByRole("complementary", { name: "Span details" });

  test("a tool that needs approval, and one that runs outside, say so on the canvas and in their panel", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/agents/a1";
    render(<App plan={embedded(APPROVALS)} />);
    const details = screen.getByRole("complementary", { name: "Blueprint details" });
    await user.click(
      await screen.findByRole("button", { name: "Tool delete_file, needs approval" }),
    );
    expect(within(details).getByText("asked before every call")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Tool page_oncall, runs outside the agent" }),
    );
    expect(within(details).getByText("the caller supplies the result")).toBeInTheDocument();
  });

  test("a run that ended waiting lists its calls, and the run that resumes it says what became of them", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded(APPROVALS)} />);
    const [second, first] = runs(useViewer.getState().data);
    await openRun(first?.traceId, "ops");
    await user.click(await screen.findByRole("button", { name: "Skip to end" }));
    expect(screen.getAllByText("waiting for approval and results: 3 calls").length).toBeGreaterThan(
      0,
    );
    await user.click(within(spanDetails()).getByRole("tab", { name: "Output" }));
    const panel = within(spanDetails());
    expect(
      panel.getByText("approved: it ran in the run that resumed this one"),
    ).toBeInTheDocument();
    expect(panel.getByText("denied: the model was told it may not run")).toBeInTheDocument();
    expect(panel.getByText("result supplied by the caller")).toBeInTheDocument();

    await openRun(second?.traceId, "ops");
    const back = screen.getByRole("link", {
      name: /^resumes ops \d\d:\d\d:\d\d: 1 approved, 1 denied, 1 result supplied$/,
    });
    expect(back.getAttribute("href")).toBe(`#/runs/${first?.traceId}`);
  });

  test("the events of a run are marked on its spans, listed in their panel and counted on the capability", async () => {
    const user = userEvent.setup();
    render(<App plan={embedded(APPROVALS)} />);
    const [, first] = runs(useViewer.getState().data);
    await openRun(first?.traceId, "ops");
    await user.click(await screen.findByRole("button", { name: "Skip to end" }));
    const tree = within(screen.getByRole("tree"));
    expect(tree.getByRole("img", { name: "Event audit.tool_checked from audit" })).toHaveAttribute(
      "title",
      "audit.tool_checked from audit",
    );
    expect(tree.getByRole("img", { name: "Event restart_queued" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Capability AuditLog, .*, 2 events$/ }),
    ).toBeInTheDocument();

    const row = tree
      .getAllByRole("treeitem")
      .find((item) => item.getAttribute("data-span") === first?.rootSpanId);
    if (!row) throw new Error("expected the run's row");
    await user.click(row);
    await user.click(within(spanDetails()).getByRole("tab", { name: "Events" }));
    const panel = within(spanDetails());
    expect(panel.getByText("audit.request_logged")).toBeInTheDocument();
    expect(panel.getByText("restart_queued")).toBeInTheDocument();
    expect(panel.getByText("Custom event")).toBeInTheDocument();
    expect(panel.getAllByText("restart_service").length).toBeGreaterThan(0);
  });
});
