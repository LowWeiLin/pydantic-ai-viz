/**
 * The inspector for blueprint parts. A blueprint is short and read together, so each panel is
 * one scroll of sections under headings that stay in view; there are no tabs here.
 *
 *   agent       Warnings · Model · Instructions · Toolsets · Output · Delegates to · Settings ·
 *               Capabilities · Mermaid
 *   tool        Parameters · Returns · Definition
 *   graph       Warnings · Types · Nodes · Mermaid
 *   graph node  Node · Comes from · Leads to
 *
 * A section with nothing to say is left out, and so is a setting the blueprint does not set.
 */
import { type ReactNode, useMemo } from "react";
import { graphNodeLabel, nodeKind } from "../canvas/layout";
import { agentMermaid } from "../data/mermaid";
import { canvasModel, fileName } from "../data/selectors";
import { useViewer } from "../data/store";
import { Facts } from "../screens/parts";
import { plural } from "../shell/format";
import { formatRoute } from "../shell/router";
import { Badge, Chip } from "../ui/Badge";
import { Icon } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { SchemaTable } from "../ui/SchemaTable";
import type {
  AgentBlueprint,
  Delegate,
  GraphBlueprint,
  GraphNode,
  Instruction,
  Json,
  Tool,
  Toolset,
} from "../wire";
import { InspectorHeader, Note, type Reveal, type SectionItem, Sections, TextBlock } from "./parts";
import { Expression, InlineCode, ProvenanceChip, SourceRef, TemplateText } from "./source";

type Fact = [label: string, value: ReactNode];

/** Drops the facts that have no value: a blueprint read from source sets few of them. */
const known = (facts: (Fact | null | undefined | false)[]): Fact[] =>
  facts.filter((fact): fact is Fact => Boolean(fact));

/**
 * What the blueprint could not tell. They are limits of what was read, not failures, so they
 * are set quietly and never in the colours of an error or a retry.
 */
function warningsSection(warnings: readonly string[]): SectionItem | null {
  if (warnings.length === 0) return null;
  return {
    id: "warnings",
    title: "Warnings",
    aside: String(warnings.length),
    content: (
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {warnings.map((warning) => (
          <li key={warning} className="flex items-start gap-2 text-muted text-ui">
            <Icon name="warning" size={14} className="mt-0.5 text-faint" />
            <span className="min-w-0 break-words">
              <InlineCode text={warning} />
            </span>
          </li>
        ))}
      </ul>
    ),
  };
}

/** The diagram as text, folded away: "Copy Mermaid" above the canvas is how it is taken. */
function MermaidSource({ source }: { source: string }) {
  return (
    <details>
      <summary className="font-medium text-muted text-ui">Show the source</summary>
      <pre className="m-0 mt-1.5 w-full overflow-x-auto rounded-control border border-line bg-bg p-2.5 text-data text-fg">
        {source}
      </pre>
    </details>
  );
}

function RawSchema({ schema, label }: { schema: Json; label: string }) {
  return (
    <details>
      <summary className="font-medium text-muted text-ui">Raw JSON Schema</summary>
      <JsonTree value={schema} label={label} openDepth={2} className="mt-1.5" />
    </details>
  );
}

const present = (items: (SectionItem | null | false)[]): SectionItem[] =>
  items.filter((item): item is SectionItem => Boolean(item));

// ---------- agent ----------

function InstructionItem({ item }: { item: Instruction }) {
  // An f-string keeps its text, with holes: it is dynamic and still readable.
  const template = item.dynamic && item.text !== null;
  return (
    <li className="flex flex-col gap-1.5 border-line border-b pb-3 last:border-b-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip>{item.kind === "system_prompt" ? "System prompt" : "Instructions"}</Chip>
        {item.dynamic && <Chip tone="accent">{template ? "Template" : "Dynamic"}</Chip>}
        {/* min-w-0: on a line of its own, a long path is cut to the panel, not pushed past it. */}
        <span className="ml-auto flex min-w-0 max-w-full">
          <SourceRef location={item.location} prefix="" />
        </span>
      </div>
      {item.text !== null ? (
        template ? (
          <>
            <TemplateText text={item.text} />
            <p className="m-0 text-faint text-meta">
              An f-string: the parts in braces are filled in when the code runs.
            </p>
          </>
        ) : (
          <TextBlock text={item.text} />
        )
      ) : item.expression !== null ? (
        <Expression code={item.expression} />
      ) : (
        <p className="m-0 text-muted text-ui">
          Computed at run time by{" "}
          <code className="break-all text-fg">{item.function ?? "a function"}</code>. The resolved
          text appears on each run.
        </p>
      )}
    </li>
  );
}

function toolsetMeta(toolset: Toolset, count: number): string {
  if (toolset.opaque && count === 0) return "resolved at run time";
  return plural(count, "tool");
}

function modelSection(agent: AgentBlueprint): SectionItem {
  const { model } = agent;
  if (!model) {
    return {
      id: "model",
      title: "Model",
      content: <Note>No model is set on the agent; one is passed at run time.</Note>,
    };
  }
  // A model class built with a literal name is known by that name; anything else that is
  // computed is known only as the expression.
  const named = model.expression === null || model.name !== model.expression;
  return {
    id: "model",
    title: "Model",
    content: (
      <Facts
        items={known([
          named && ["Name", <code key="name">{model.name}</code>],
          model.expression !== null && [
            named ? "Built by" : "Set by",
            <Expression key="expression" code={model.expression} hint={!named} />,
          ],
          model.provider !== null && ["Provider", model.provider],
          model.id !== null && model.id !== model.name && ["Id", model.id],
          model.offline && ["Offline", "A test or function model: no provider is called."],
        ])}
      />
    ),
  };
}

function settingsSection(agent: AgentBlueprint): SectionItem {
  const { settings } = agent;
  const computed = (key: string): ReactNode | null => {
    const code = settings.expressions[key];
    return code === undefined ? null : <Expression key={key} code={code} />;
  };
  const value = (key: string, literal: string | number | null): ReactNode | null =>
    computed(key) ?? literal;
  const row = (label: string, shown: ReactNode | null): Fact | null =>
    shown === null ? null : [label, shown];

  // One model setting may be a literal while the next is computed: `model_settings.<key>`.
  const modelRows: Fact[] = Object.entries(settings.model_settings ?? {}).map(([key, literal]) => [
    key,
    <code key={key}>{JSON.stringify(literal)}</code>,
  ]);
  for (const [key, code] of Object.entries(settings.expressions)) {
    const name = /^model_settings\.(.+)$/.exec(key)?.[1];
    if (name) modelRows.push([name, <Expression key={key} code={code} hint={false} />]);
  }

  const facts = known([
    row("Dependencies", agent.deps_type && <code className="break-all">{agent.deps_type}</code>),
    row("End strategy", value("end_strategy", settings.end_strategy)),
    row("Tool retries", value("tool_retries", settings.tool_retries)),
    row("Output retries", value("output_retries", settings.output_retries)),
    row(
      "Tool timeout",
      value("tool_timeout", settings.tool_timeout === null ? null : `${settings.tool_timeout} s`),
    ),
    row("Model settings", computed("model_settings")),
  ]);
  return {
    id: "settings",
    title: "Settings",
    content:
      facts.length === 0 && modelRows.length === 0 ? (
        <Note>
          {agent.source === "code"
            ? "The code sets none, so the defaults of the installed Pydantic AI apply."
            : "None are known."}
        </Note>
      ) : (
        <>
          {facts.length > 0 && <Facts items={facts} />}
          {modelRows.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-faint text-ui">Model settings</span>
              <Facts items={modelRows} />
            </div>
          )}
        </>
      ),
  };
}

function DelegateTarget({ delegate }: { delegate: Delegate }) {
  const target = useViewer((state) => state.data.agents[delegate.agent_id]);
  return (
    <>
      {target ? (
        <a
          href={formatRoute({ name: "agent", id: target.id })}
          className="font-medium text-agent underline underline-offset-2"
        >
          {target.label}
        </a>
      ) : (
        <span className="text-muted">an agent this session has not seen</span>
      )}
      {target?.location && (
        <code className="text-[11px] text-faint" title={target.location.file}>
          {fileName(target.location.file)}:{target.location.line}
        </code>
      )}
      <Chip tone={delegate.evidence === "observed" ? "ok" : "neutral"}>
        {delegate.evidence === "observed" ? "seen in a run" : "found in code"}
      </Chip>
    </>
  );
}

function outputSection(agent: AgentBlueprint): SectionItem {
  const { output } = agent;
  return {
    id: "output",
    title: "Output",
    aside: output.type_name ?? undefined,
    content: (
      <>
        <Facts
          items={known([
            ["Mode", output.mode],
            output.allows_text !== null && [
              "Plain text",
              output.allows_text ? "allowed" : "not allowed",
            ],
            output.tool_names.length > 0 && ["Output tools", output.tool_names.join(", ")],
            output.location && [
              "Defined at",
              <SourceRef key="location" location={output.location} prefix="" />,
            ],
          ])}
        />
        {output.json_schema !== null ? (
          <>
            <SchemaTable schema={output.json_schema} caption={`Output of ${agent.label}`} />
            <RawSchema schema={output.json_schema} label="output schema" />
          </>
        ) : (
          <Note>
            {output.mode === "text"
              ? "The agent answers in plain text, so there is no schema."
              : "No output schema is known for this agent."}
          </Note>
        )}
      </>
    ),
  };
}

function AgentMermaid({ agent }: { agent: AgentBlueprint }) {
  const data = useViewer((state) => state.data);
  const source = useMemo(() => {
    const model = canvasModel(data, agent.id, null);
    return model ? agentMermaid(model) : null;
  }, [data, agent.id]);
  return source === null ? null : <MermaidSource source={source} />;
}

function agentSections(agent: AgentBlueprint): SectionItem[] {
  const builtin = agent.capabilities.filter((c) => c.builtin);
  const custom = agent.capabilities.filter((c) => !c.builtin);
  return present([
    warningsSection(agent.warnings),
    modelSection(agent),
    {
      id: "instructions",
      title: "Instructions",
      aside: agent.instructions.length > 0 ? String(agent.instructions.length) : undefined,
      content:
        agent.instructions.length === 0 ? (
          <Note>
            {agent.source === "observed"
              ? "Not known yet. The resolved instructions appear on each run."
              : "This agent has no instructions or system prompts."}
          </Note>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {agent.instructions.map((item, i) => (
              <InstructionItem key={`${item.kind}-${i}`} item={item} />
            ))}
          </ul>
        ),
    },
    {
      id: "toolsets",
      title: "Toolsets",
      content: (
        <>
          {agent.toolsets.length === 0 ? (
            <Note>This agent has no tools.</Note>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {agent.toolsets.map((toolset) => {
                const count = agent.tools.filter((tool) => tool.toolset === toolset.id).length;
                return (
                  <li key={toolset.id} className="flex flex-col gap-1 text-ui">
                    <div className="flex items-baseline gap-2">
                      <Icon name="toolset" size={13} className="shrink-0 self-center text-faint" />
                      <span className="min-w-0 flex-1 break-words font-medium">
                        {toolset.label}
                      </span>
                      <span className="shrink-0 text-faint text-meta">
                        {toolsetMeta(toolset, count)}
                      </span>
                    </div>
                    {toolset.wrappers.length > 0 && (
                      <p className="m-0 flex flex-wrap items-center gap-1.5 text-faint text-meta">
                        wrapped by
                        {toolset.wrappers.map((wrapper) => (
                          <Chip key={wrapper} mono>
                            {wrapper}
                          </Chip>
                        ))}
                      </p>
                    )}
                    {toolset.expression !== null && (
                      <Expression code={toolset.expression} hint={false} />
                    )}
                    <SourceRef location={toolset.location} />
                  </li>
                );
              })}
            </ul>
          )}
          {agent.native_tools.length > 0 && (
            <p className="m-0 flex flex-wrap items-center gap-1.5 text-muted text-ui">
              Provider-side tools:
              {agent.native_tools.map((name) => (
                <Chip key={name} tone="tool" mono>
                  {name}
                </Chip>
              ))}
            </p>
          )}
        </>
      ),
    },
    outputSection(agent),
    agent.delegates.length > 0 && {
      id: "delegates",
      title: "Delegates to",
      aside: String(agent.delegates.length),
      content: (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {agent.delegates.map((delegate) => (
            <li
              key={`${delegate.tool_name}-${delegate.agent_id}`}
              className="flex flex-wrap items-center gap-2 text-ui"
            >
              <code className="text-tool">{delegate.tool_name}</code>
              <Icon name="arrowRight" size={14} className="text-faint" label="calls" />
              <DelegateTarget delegate={delegate} />
            </li>
          ))}
        </ul>
      ),
    },
    settingsSection(agent),
    agent.capabilities.length > 0 && {
      id: "capabilities",
      title: "Capabilities",
      content: (
        <>
          {custom.length > 0 && (
            <p className="m-0 flex flex-wrap gap-1.5">
              {custom.map((c) => (
                <Chip key={c.name} mono>
                  {c.name}
                </Chip>
              ))}
            </p>
          )}
          {builtin.length > 0 && (
            <details className="text-ui">
              <summary className="text-muted">
                Built-in <Badge>{builtin.length}</Badge>
              </summary>
              <p className="m-0 mt-1.5 flex flex-wrap gap-1.5">
                {builtin.map((c) => (
                  <Chip key={c.name} mono>
                    {c.name}
                  </Chip>
                ))}
              </p>
            </details>
          )}
        </>
      ),
    },
    { id: "mermaid", title: "Mermaid", content: <AgentMermaid agent={agent} /> },
  ]);
}

export function AgentInspector({
  agent,
  reveal,
}: {
  agent: AgentBlueprint;
  reveal?: Reveal | null;
}) {
  return (
    <>
      <InspectorHeader tone="agent" icon="agent" kind="Agent blueprint" title={agent.label}>
        {agent.description && <p className="m-0 text-muted text-ui">{agent.description}</p>}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProvenanceChip source={agent.source} />
          <SourceRef location={agent.location} />
        </div>
      </InspectorHeader>
      <Sections label="Agent blueprint" items={agentSections(agent)} reveal={reveal} />
    </>
  );
}

// ---------- tool ----------

export function ToolInspector({ agent, tool }: { agent: AgentBlueprint; tool: Tool }) {
  const toolset = agent.toolsets.find((t) => t.id === tool.toolset);
  const delegate = agent.delegates.find((d) => d.tool_name === tool.name);
  const retries =
    tool.max_retries !== null
      ? String(tool.max_retries)
      : agent.settings.tool_retries !== null
        ? `${agent.settings.tool_retries} (agent default)`
        : null;
  // Null for a tool that is defined outside the files that were read.
  const parameters = tool.parameters_schema;
  return (
    <>
      <InspectorHeader tone="tool" icon="tool" kind="Tool" title={tool.name} mono>
        {tool.description && <p className="m-0 text-muted text-ui">{tool.description}</p>}
        <p className="m-0 text-muted text-ui">
          of agent <span className="font-medium text-fg">{agent.label}</span>
        </p>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProvenanceChip source={tool.origin} />
          <SourceRef location={tool.location} />
        </div>
        {tool.origin === "observed" && (
          <Note>
            The blueprint did not list this tool, so only what the model was told about it is known.
          </Note>
        )}
      </InspectorHeader>
      <Sections
        label="Tool"
        items={present([
          {
            id: "parameters",
            title: "Parameters",
            content:
              parameters === null ? (
                <Note>
                  This tool is defined outside the files that were read, so its parameters are not
                  known.
                </Note>
              ) : (
                <>
                  <SchemaTable schema={parameters} caption={`Parameters of ${tool.name}`} />
                  <RawSchema schema={parameters} label="parameters schema" />
                </>
              ),
          },
          {
            id: "returns",
            title: "Returns",
            content:
              tool.return_schema === null ? (
                <Note>No return schema is declared for this tool.</Note>
              ) : (
                <>
                  <SchemaTable
                    schema={tool.return_schema}
                    caption={`Return value of ${tool.name}`}
                  />
                  <RawSchema schema={tool.return_schema} label="return schema" />
                </>
              ),
          },
          {
            id: "definition",
            title: "Definition",
            content: (
              <>
                <Facts
                  items={known([
                    toolset && ["Toolset", toolset.label],
                    ["Kind", tool.kind],
                    tool.is_async !== null && ["Call style", tool.is_async ? "async" : "sync"],
                    tool.takes_ctx !== null && [
                      "Run context",
                      tool.takes_ctx ? "takes ctx" : "no ctx",
                    ],
                    retries !== null && ["Max retries", retries],
                    tool.function !== null && [
                      "Function",
                      <code key="function" className="break-all">
                        {tool.function}
                      </code>,
                    ],
                  ])}
                />
                {delegate && (
                  <p className="m-0 flex flex-wrap items-center gap-2 text-ui">
                    Delegates to
                    <DelegateTarget delegate={delegate} />
                  </p>
                )}
              </>
            ),
          },
        ])}
      />
    </>
  );
}

/** A tool a run called that no blueprint lists: there is nothing static to show. */
export function UnlistedToolInspector({ agent, name }: { agent: AgentBlueprint; name: string }) {
  return (
    <>
      <InspectorHeader tone="tool" icon="tool" kind="Tool" title={name} mono>
        <p className="m-0 text-muted text-ui">
          of agent <span className="font-medium text-fg">{agent.label}</span>
        </p>
      </InspectorHeader>
      <div className="p-3">
        <Note>
          The blueprint does not list this tool: it comes from a toolset that resolves its tools at
          run time. Select one of its calls in the timeline to see arguments and results.
        </Note>
      </div>
    </>
  );
}

// ---------- graph ----------

export function GraphInspector({
  graph,
  reveal,
}: {
  graph: GraphBlueprint;
  reveal?: Reveal | null;
}) {
  const types = known([
    graph.state_type !== null && ["State", <code key="state">{graph.state_type}</code>],
    graph.input_type !== null && ["Input", <code key="input">{graph.input_type}</code>],
    graph.output_type !== null && ["Output", <code key="output">{graph.output_type}</code>],
  ]);
  return (
    <>
      <InspectorHeader tone="graph" icon="graph" kind="Graph blueprint" title={graph.label}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProvenanceChip source={graph.source} />
          <SourceRef location={graph.location} />
        </div>
      </InspectorHeader>
      <Sections
        label="Graph blueprint"
        reveal={reveal}
        items={present([
          warningsSection(graph.warnings),
          types.length > 0 && { id: "types", title: "Types", content: <Facts items={types} /> },
          {
            id: "nodes",
            title: "Nodes",
            aside: String(graph.nodes.length),
            content: (
              <>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                  {graph.nodes.map((node) => (
                    <li key={node.id} className="flex flex-wrap items-center gap-2 text-ui">
                      <Chip tone="graph">{nodeKind(node.kind)}</Chip>
                      <span className="font-medium">{graphNodeLabel(node)}</span>
                      {node.note && <span className="text-muted">{node.note}</span>}
                    </li>
                  ))}
                </ul>
                <Note>Select a node on the canvas to see what leads into and out of it.</Note>
              </>
            ),
          },
          {
            id: "mermaid",
            title: "Mermaid",
            content:
              graph.mermaid === null ? (
                <Note>This graph could not be rendered as Mermaid.</Note>
              ) : (
                <MermaidSource source={graph.mermaid} />
              ),
          },
        ])}
      />
    </>
  );
}

export function GraphNodeInspector({ graph, node }: { graph: GraphBlueprint; node: GraphNode }) {
  const labelOf = (id: string): string => {
    const other = graph.nodes.find((n) => n.id === id);
    return other ? graphNodeLabel(other) : id;
  };
  const incoming = graph.edges.filter((edge) => edge.target === node.id);
  const outgoing = graph.edges.filter((edge) => edge.source === node.id);
  const list = (edges: typeof incoming, end: "source" | "target") =>
    edges.length === 0 ? (
      <p className="m-0 text-muted text-ui">None.</p>
    ) : (
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {edges.map((edge, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 text-ui">
            <Icon name="arrowRight" size={14} className="text-faint" />
            <span className="font-medium">{labelOf(edge[end])}</span>
            {edge.label && <Chip>{edge.label}</Chip>}
          </li>
        ))}
      </ul>
    );
  return (
    <>
      <InspectorHeader
        tone="graph"
        icon={nodeKind(node.kind) === "decision" ? "decision" : "graph"}
        kind={`Graph ${nodeKind(node.kind)}`}
        title={graphNodeLabel(node)}
      >
        <p className="m-0 text-muted text-ui">
          in graph <span className="font-medium text-fg">{graph.label}</span>
        </p>
        <SourceRef location={node.location} />
      </InspectorHeader>
      <Sections
        label="Graph node"
        items={[
          {
            id: "node",
            title: "Node",
            content: (
              <Facts
                items={known([
                  ["Id", <code key="id">{node.id}</code>],
                  ["Kind", node.kind],
                  node.note !== null && ["Note", node.note],
                ])}
              />
            ),
          },
          { id: "from", title: "Comes from", content: list(incoming, "source") },
          { id: "to", title: "Leads to", content: list(outgoing, "target") },
        ]}
      />
    </>
  );
}
