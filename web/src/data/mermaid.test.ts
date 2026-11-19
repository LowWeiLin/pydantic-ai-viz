// @vitest-environment node
/**
 * The Mermaid flowchart written for an agent blueprint.
 *
 * The real Mermaid parser is not a dependency here (the package is over 100 MB unpacked), so
 * the output is held to the small part of the flowchart grammar the generator uses:
 * `checkFlowchart` fails on any line outside it, on an id that is used before it is declared
 * or declared twice, and on a label that could end its own quotes.
 */
import { describe, expect, test } from "vitest";
import { agentBlueprint, fixtureState, graphBlueprint, stateOf } from "../test/factory";
import type { AgentBlueprint, Event, Tool, Toolset } from "../wire";
import { agentMermaid, mermaidLabel, subjectMermaid } from "./mermaid";
import type { DataState } from "./reduce";
import { canvasModel } from "./selectors";

const ID = "[A-Za-z][A-Za-z0-9_]*";
/** A quoted label: no raw quote inside, and "#" only as the start of an entity code. */
const LABEL = '"((?:[^"#]|#[A-Za-z0-9]+;)*)"';
const NODE = new RegExp(`^ {2}(?: {2})?(${ID})(\\[|\\(\\[|\\[/)${LABEL}(\\]|\\]\\)|/\\])$`);
const SUBGRAPH = new RegExp(`^ {2}subgraph (${ID})\\[${LABEL}\\]$`);
const EDGE = new RegExp(`^ {2}(${ID}) (-->|-- ${LABEL} -->|-\\. ${LABEL} \\.->) (${ID})$`);
const CLOSERS: Record<string, string> = { "[": "]", "([": "])", "[/": "/]" };
const RESERVED = new Set(["end", "subgraph", "graph", "flowchart", "direction", "class", "style"]);

type Flowchart = {
  nodes: Map<string, string>;
  groups: Map<string, { label: string; members: string[] }>;
  edges: { from: string; to: string; label: string | null; dashed: boolean }[];
};

/** Reads Mermaid entity codes back, so a label can be compared with what went in. */
const decode = (label: string): string =>
  label
    .replace(/<br\/>/g, "\n")
    .replace(/#quot;/g, '"')
    .replace(/#lt;/g, "<")
    .replace(/#gt;/g, ">")
    .replace(/#(\d+);/g, (_, code: string) => String.fromCharCode(Number(code)));

function checkFlowchart(source: string): Flowchart {
  expect(source.endsWith("\n")).toBe(true);
  const [head, ...lines] = source.slice(0, -1).split("\n");
  expect(head).toBe("flowchart LR");
  const chart: Flowchart = { nodes: new Map(), groups: new Map(), edges: [] };
  const declare = (id: string): void => {
    expect(RESERVED.has(id), `${id} is a Mermaid keyword`).toBe(false);
    expect(chart.nodes.has(id) || chart.groups.has(id), `${id} is declared twice`).toBe(false);
  };
  let open: string | null = null;
  for (const line of lines) {
    const node = NODE.exec(line);
    const group = SUBGRAPH.exec(line);
    const edge = EDGE.exec(line);
    if (node) {
      const [, id = "", opener = "", label = "", closer] = node;
      expect(closer, line).toBe(CLOSERS[opener]);
      // The only markup a label may hold is the line break the generator itself puts there.
      expect(label.replace(/<br\/>/g, ""), line).not.toMatch(/[<>`]/);
      expect(line.startsWith("    "), `${line}: indented exactly when inside a subgraph`).toBe(
        open !== null,
      );
      declare(id);
      chart.nodes.set(id, decode(label));
      if (open) chart.groups.get(open)?.members.push(id);
    } else if (group) {
      expect(open, "subgraphs are not nested").toBeNull();
      const [, id = "", label = ""] = group;
      declare(id);
      chart.groups.set(id, { label: decode(label), members: [] });
      open = id;
    } else if (line === "  end") {
      expect(open, "an end without a subgraph").not.toBeNull();
      open = null;
    } else if (edge) {
      expect(open, "edges are written outside subgraphs").toBeNull();
      const [, from = "", arrow = "", solid, dashed, to = ""] = edge;
      for (const end of [from, to]) {
        expect(chart.nodes.has(end), `${line}: ${end} is not a declared node`).toBe(true);
      }
      chart.edges.push({
        from,
        to,
        label: solid ?? dashed ?? null,
        dashed: arrow.startsWith("-."),
      });
    } else {
      throw new Error(`not a line the generator may write: ${JSON.stringify(line)}`);
    }
  }
  expect(open, "a subgraph was left open").toBeNull();
  return chart;
}

function mermaidOf(data: DataState, agentId: string): string {
  const model = canvasModel(data, agentId, null);
  if (!model) throw new Error(`no agent ${agentId}`);
  return agentMermaid(model);
}

const sessionOf = (...agents: AgentBlueprint[]): DataState =>
  stateOf(...agents.map((agent): [number, Event] => [1, { type: "blueprint.agent", agent }]));

const toolset = (id: string, label: string, fields: Partial<Toolset> = {}): Toolset => ({
  id,
  toolset_id: null,
  label,
  kind: "function",
  wrappers: [],
  opaque: false,
  expression: null,
  location: null,
  ...fields,
});

const tool = (name: string, set = "ts0"): Tool => ({
  name,
  toolset: set,
  description: null,
  parameters_schema: { type: "object", properties: {} },
  return_schema: null,
  kind: "function",
  takes_ctx: false,
  is_async: true,
  max_retries: null,
  function: null,
  origin: "code",
  location: null,
  library_version: null,
});

const newsroom = fixtureState("blueprint/newsroom.pviz.jsonl");

describe("agentMermaid", () => {
  test("an agent with its model, tools by toolset, output and delegate, exactly", () => {
    expect(mermaidOf(newsroom, "a2")).toBe(
      [
        "flowchart LR",
        // The model name is read from the environment: the variable, not the default.
        '  agent1["reporter<br/>model: env NEWSROOM_MODEL"]',
        '  subgraph agent1_tools1["Agent tools"]',
        '    agent1_tool1["ask_stringer"]',
        "  end",
        "  subgraph agent1_tools2[\"FunctionToolset 'wires'\"]",
        '    agent1_tool2["search_wires"]',
        '    agent1_tool3["fetch_article"]',
        "  end",
        '  subgraph agent1_tools3["MCPServerStdio"]',
        '    agent1_tools3_any(["decided at run time"])',
        "  end",
        '  agent1_output[/"output: str"/]',
        "  agent1 --> agent1_tool1",
        "  agent1 --> agent1_tool2",
        "  agent1 --> agent1_tool3",
        "  agent1 --> agent1_tools3_any",
        "  agent1 --> agent1_output",
        '  agent2["stringer<br/>model: claude-sonnet-4-5"]',
        '  subgraph agent2_tools1["Agent tools"]',
        '    agent2_tool1["duckduckgo_search"]',
        "  end",
        '  agent2_output[/"output: str"/]',
        "  agent2 --> agent2_tool1",
        "  agent2 --> agent2_output",
        '  agent1_tool1 -. "found in code" .-> agent2',
        "",
      ].join("\n"),
    );
  });

  test("it draws what the canvas draws: every reachable agent, tool and delegation", () => {
    const model = canvasModel(newsroom, "a1", null);
    if (!model) throw new Error("expected the editor");
    const chart = checkFlowchart(agentMermaid(model));
    const labels = [...chart.nodes.values()];
    for (const { blueprint } of model.agents) {
      expect(labels.filter((label) => label.startsWith(`${blueprint.label}\n`))).toHaveLength(1);
      for (const { name } of blueprint.tools) expect(labels).toContain(name);
    }
    expect(labels).toContain("output: Story");
    expect(labels).toContain("output: Verdict[Claim]");
    expect(chart.edges.filter((edge) => edge.label !== null)).toHaveLength(
      model.delegations.length,
    );
    // editor -> reporter -> stringer, and editor -> fact_checker: all written in the code.
    expect(chart.edges.filter((edge) => edge.dashed).map((edge) => edge.label)).toEqual([
      "found in code",
      "found in code",
      "found in code",
    ]);
  });

  test("a delegation that a run has shown is a solid line", () => {
    const chart = checkFlowchart(mermaidOf(fixtureState("sample.pviz.jsonl"), "a1"));
    const [delegation] = chart.edges.filter((edge) => edge.label !== null);
    expect(delegation).toMatchObject({ label: "seen in a run", dashed: false });
    expect(chart.nodes.get(delegation?.from ?? "")).toBe("escalate_refund");
    expect(chart.nodes.get(delegation?.to ?? "")).toBe("refunds\nmodel: scripted-support");
  });

  test("no model, no tools, an unknown output type: still a complete chart", () => {
    const bare = agentBlueprint({
      id: "a1",
      label: "bare",
      model: null,
      toolsets: [toolset("ts0", "Agent tools")],
      output: {
        mode: "unknown",
        allows_text: null,
        type_name: null,
        json_schema: null,
        tool_names: [],
        location: null,
      },
    });
    expect(mermaidOf(sessionOf(bare), "a1")).toBe(
      [
        "flowchart LR",
        '  agent1["bare"]',
        '  agent1_output[/"output: unknown type"/]',
        "  agent1 --> agent1_output",
        "",
      ].join("\n"),
    );
  });

  test("names cannot break the syntax, whatever they contain", () => {
    const names = [
      'say "hi"',
      "end",
      "subgraph x[y]",
      "a --> b",
      "`code`",
      "<script>alert(1)</script>",
      "50% & #1 | {x} (y)",
      "two\nlines\tand a tab",
      "x".repeat(300),
    ];
    const hostile = agentBlueprint({
      id: "a1",
      label: names[0] ?? "",
      model: {
        id: null,
        name: 'build("m", [1, 2])',
        provider: null,
        offline: false,
        expression: 'build("m", [1, 2])',
        model_class: null,
        env: null,
      },
      toolsets: [toolset("ts0", 'Toolset "quoted" [1]'), toolset("end", "end")],
      tools: [...names.map((name) => tool(name)), tool("end", "end")],
      output: {
        mode: "tool",
        allows_text: false,
        type_name: 'dict[str, "A | B"]',
        json_schema: null,
        tool_names: [],
        location: null,
      },
    });
    const chart = checkFlowchart(mermaidOf(sessionOf(hostile), "a1"));
    const labels = [...chart.nodes.values()];
    expect(labels).toContain('say "hi"\nmodel: build("m", [1, 2])');
    expect(labels).toContain("<script>alert(1)</script>");
    expect(labels).toContain("50% & #1 | {x} (y)");
    expect(labels).toContain("`code`");
    expect(labels).toContain('output: dict[str, "A | B"]');
    // White space is flattened and a very long name is cut, with a mark that it was.
    expect(labels).toContain("two lines and a tab");
    expect(labels).toContain(`${"x".repeat(77)}...`);
    expect([...chart.groups.values()].map((group) => group.label)).toEqual([
      'Toolset "quoted" [1]',
      "end",
    ]);
  });

  test.each(["sample.pviz.jsonl", "failed.pviz.jsonl", "redacted.pviz.jsonl"])(
    "every agent of %s gives a well-formed chart",
    (name) => {
      const data = fixtureState(name);
      expect(data.agentOrder.length).toBeGreaterThan(0);
      for (const id of data.agentOrder) checkFlowchart(mermaidOf(data, id));
    },
  );

  test("the check is not vacuous: it rejects what Mermaid would", () => {
    const good = mermaidOf(newsroom, "a3");
    checkFlowchart(good);
    expect(() => checkFlowchart(good.replace('"stringer', '"str"inger'))).toThrow();
    expect(() => checkFlowchart(good.replace("agent1_output\n", "agent9\n"))).toThrow();
    expect(() => checkFlowchart(`${good}  end\n`)).toThrow();
    expect(() => checkFlowchart(good.replace("flowchart LR", "graph"))).toThrow();
  });
});

describe("mermaidLabel", () => {
  test("quotes the text and writes its special characters as entity codes", () => {
    expect(mermaidLabel("plain")).toBe('"plain"');
    expect(mermaidLabel('a "b" <c> #d & `e`')).toBe(
      '"a #quot;b#quot; #lt;c#gt; #35;d #38; #96;e#96;"',
    );
  });
});

describe("subjectMermaid", () => {
  test("an agent gets the generated chart; a graph the text its blueprint carries", () => {
    expect(subjectMermaid(newsroom, { kind: "agent", id: "a3" })).toBe(mermaidOf(newsroom, "a3"));
    expect(subjectMermaid(newsroom, { kind: "graph", id: "g1" })).toContain("stateDiagram-v2");
  });

  test("nothing to draw gives null", () => {
    const data = stateOf([1, { type: "blueprint.graph", graph: graphBlueprint("g1", ["a"]) }]);
    expect(subjectMermaid(data, { kind: "graph", id: "g1" })).toBeNull();
    expect(subjectMermaid(data, { kind: "graph", id: "nope" })).toBeNull();
    expect(subjectMermaid(data, { kind: "agent", id: "nope" })).toBeNull();
  });
});
