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
import { agentBlueprint, fixture, log, sessionStarted } from "../test/factory";

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
    // The model is an expression: it is not passed off as a model name.
    const model = section("Model");
    expect(within(model).getByText("Set by")).toBeInTheDocument();
    expect(within(model).getByText("get_model()").tagName).toBe("CODE");
    expect(within(model).getByText("Decided at run time")).toBeInTheDocument();
    expect(within(model).queryByText("Name")).toBeNull();
    expect(screen.getAllByText("at run time")).toHaveLength(1);

    // Warnings are a quiet list, with the code in them set as code.
    const warnings = section("Warnings");
    expect(within(warnings).getAllByRole("listitem")).toHaveLength(3);
    expect(within(warnings).getByText("get_model()").tagName).toBe("CODE");
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

    // An f-string keeps its text; the holes are marked.
    expect(within(details).getByText("{today:%A}").tagName).toBe("CODE");
    expect(within(details).getByText("Template")).toBeInTheDocument();
    expect(within(details).getByText(/An f-string/)).toBeInTheDocument();
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
    expect(copied).toContain('agent1["reporter<br/>model: get_model()"]');
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
    // The folder takes what room the name and line leave it, down to one character (a lone
    // ellipsis). They do not shrink as long as they fit: only a name too long on its own is
    // cut, never the line.
    expect(folder).toHaveClass("min-w-[1ch]", "truncate");
    // Cut from its start, so the folders nearest the file stay; its slashes stay in place.
    expect(folder).toHaveAttribute("dir", "rtl");
    expect(folder?.firstElementChild?.tagName).toBe("BDI");
    expect(folder?.firstElementChild).toHaveAttribute("dir", "ltr");
    expect(tail).toHaveClass("shrink-0", "max-w-[calc(100%-1ch)]", "min-w-0");
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
