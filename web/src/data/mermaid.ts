/**
 * Mermaid source for an agent blueprint, written here because Pydantic AI has no renderer for
 * agents (a graph blueprint carries the text `graph.render()` produced). It draws what the
 * agent canvas draws: each agent with its model, its tools grouped by toolset, its output, and
 * the delegations between agents.
 *
 *   flowchart LR
 *     agent1["triage<br/>model: openai:gpt-5.2"]
 *     subgraph agent1_tools1["Agent tools"]
 *       agent1_tool1["get_order"]
 *     end
 *     agent1_output[/"output: Resolution"/]
 *     agent1 --> agent1_tool1
 *     agent1 --> agent1_output
 *
 * Node ids are made here from positions, never from names, so no name can break the syntax.
 * Every label is quoted and its special characters are written as Mermaid entity codes.
 */
import { modelLabel } from "./model";
import type { DataState } from "./reduce";
import { type CanvasModel, canvasModel, type RunSubject } from "./selectors";

const MAX_LABEL = 80;

/** Text as a quoted Mermaid label. */
export function mermaidLabel(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const cut = flat.length > MAX_LABEL ? `${flat.slice(0, MAX_LABEL - 3)}...` : flat;
  // "#" first: it starts every entity code written after it.
  const escaped = cut
    .replace(/#/g, "#35;")
    .replace(/&/g, "#38;")
    .replace(/"/g, "#quot;")
    .replace(/</g, "#lt;")
    .replace(/>/g, "#gt;")
    // A label that begins and ends with a backtick would be read as Markdown.
    .replace(/`/g, "#96;");
  return `"${escaped}"`;
}

/** Two quoted lines in one label: the second goes under the first. */
const twoLines = (first: string, second: string): string =>
  `${mermaidLabel(first).slice(0, -1)}<br/>${mermaidLabel(second).slice(1)}`;

/** The flowchart for everything on an agent canvas. Run activity in the model is ignored. */
export function agentMermaid(model: CanvasModel): string {
  const lines = ["flowchart LR"];
  const agentIds = new Map(model.agents.map((agent, i) => [agent.blueprint.id, `agent${i + 1}`]));
  const toolIds = new Map<string, string>();
  const toolKey = (agentId: string, name: string): string => `${agentId}\u0000${name}`;

  for (const agent of model.agents) {
    const { blueprint } = agent;
    const id = agentIds.get(blueprint.id) ?? "";
    lines.push(
      `  ${id}[${
        blueprint.model
          ? twoLines(blueprint.label, `model: ${modelLabel(blueprint.model)}`)
          : mermaidLabel(blueprint.label)
      }]`,
    );

    const flows: string[] = [];
    let toolNumber = 0;
    for (const [i, group] of agent.toolsets.entries()) {
      if (group.tools.length === 0 && !group.toolset.opaque) continue;
      const groupId = `${id}_tools${i + 1}`;
      lines.push(`  subgraph ${groupId}[${mermaidLabel(group.toolset.label)}]`);
      if (group.tools.length === 0) {
        // The toolset lists its tools only while a run is going.
        lines.push(`    ${groupId}_any(["decided at run time"])`);
        flows.push(`${groupId}_any`);
      }
      for (const { tool } of group.tools) {
        toolNumber += 1;
        const toolId = `${id}_tool${toolNumber}`;
        toolIds.set(toolKey(blueprint.id, tool.name), toolId);
        lines.push(`    ${toolId}[${mermaidLabel(tool.name)}]`);
        flows.push(toolId);
      }
      lines.push("  end");
    }

    const type = blueprint.output.type_name ?? (blueprint.output.mode === "text" ? "str" : null);
    lines.push(`  ${id}_output[/${mermaidLabel(`output: ${type ?? "unknown type"}`)}/]`);
    for (const target of flows) lines.push(`  ${id} --> ${target}`);
    lines.push(`  ${id} --> ${id}_output`);
  }

  for (const delegation of model.delegations) {
    const from = toolIds.get(toolKey(delegation.fromAgentId, delegation.toolName));
    const to = agentIds.get(delegation.toAgentId);
    if (!from || !to) continue;
    // A solid line for a delegation that a run has shown ("pending" is one the replay has not
    // reached yet), a dashed one for a delegation that is only written in the code.
    const seen = delegation.evidence === "observed" || delegation.evidence === "pending";
    lines.push(
      seen ? `  ${from} -- "seen in a run" --> ${to}` : `  ${from} -. "found in code" .-> ${to}`,
    );
  }
  return `${lines.join("\n")}\n`;
}

/** Mermaid source for what a canvas shows: an agent or a graph. Null when there is none. */
export function subjectMermaid(
  data: DataState,
  subject: Pick<RunSubject, "kind" | "id">,
): string | null {
  if (subject.kind === "graph") return data.graphs[subject.id]?.mermaid ?? null;
  const model = canvasModel(data, subject.id, null);
  return model ? agentMermaid(model) : null;
}
