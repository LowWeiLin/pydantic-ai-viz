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
import { SourceRef } from "../inspector/source";
import { fixture } from "../test/factory";

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
  test("a long path gives way from its folder; the file and line are cut last", () => {
    const location = { file: "pydantic_ai_examples/medical_agent_delegation.py", line: 161 };
    const { container } = render(<SourceRef location={location} />);
    const code = screen.getByTitle("pydantic_ai_examples/medical_agent_delegation.py:161");
    const [folder, tail] = [...code.children] as HTMLElement[];
    expect(folder).toHaveTextContent(/^pydantic_ai_examples\/$/);
    expect(folder).toHaveClass("min-w-0", "truncate");
    // The tail does not shrink while the folder still can; it is cut only when it alone is
    // wider than the panel.
    expect(tail).toHaveTextContent(/^medical_agent_delegation\.py:161$/);
    expect(tail).toHaveClass("shrink-0", "max-w-full", "truncate");
    expect(container).toHaveTextContent("Defined at");
    expect(
      screen.getByRole("button", {
        name: "Copy pydantic_ai_examples/medical_agent_delegation.py:161",
      }),
    ).toBeInTheDocument();
  });

  test("a file at the top of the analysed tree is one part", () => {
    render(<SourceRef location={{ file: "01_tools.py", line: 22 }} prefix="" />);
    const code = screen.getByTitle("01_tools.py:22");
    expect(code.children).toHaveLength(1);
    expect(code).toHaveTextContent(/^01_tools\.py:22$/);
    expect(screen.queryByText("Defined at")).toBeNull();
  });
});
