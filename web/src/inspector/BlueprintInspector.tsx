/**
 * The inspector for blueprint parts:
 *
 *   agent       Overview · Instructions · Output
 *   tool        Overview · Parameters · Returns
 *   graph       Overview · Mermaid
 *   graph node  Overview
 */
import { graphNodeLabel, nodeKind } from "../canvas/layout";
import { useViewer } from "../data/store";
import { Facts, orDash } from "../screens/parts";
import { plural } from "../shell/format";
import { formatRoute } from "../shell/router";
import { Badge, Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { Icon } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { SchemaTable } from "../ui/SchemaTable";
import type {
  AgentBlueprint,
  GraphBlueprint,
  GraphNode,
  Instruction,
  Json,
  Tool,
  Toolset,
} from "../wire";
import { Block, InspectorHeader, Note, StickyTabs, TextBlock } from "./parts";

function Warnings({ warnings }: { warnings: readonly string[] }) {
  if (warnings.length === 0) return null;
  return (
    <Block title="Warnings" aside={String(warnings.length)}>
      <ul className="tone-retry m-0 flex list-none flex-col gap-1.5 p-0">
        {warnings.map((warning) => (
          <li key={warning} className="flex items-start gap-2 text-ui">
            <Icon name="warning" size={14} className="tone-text mt-0.5" />
            <span>{warning}</span>
          </li>
        ))}
      </ul>
    </Block>
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

// ---------- agent ----------

function InstructionItem({ item }: { item: Instruction }) {
  return (
    <li className="flex flex-col gap-1.5 border-line border-b pb-3 last:border-b-0 last:pb-0">
      <div className="flex items-center gap-1.5">
        <Chip>{item.kind === "system_prompt" ? "System prompt" : "Instructions"}</Chip>
        {item.dynamic && <Chip tone="accent">Dynamic</Chip>}
      </div>
      {item.text !== null ? (
        <TextBlock text={item.text} />
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

function AgentOverview({ agent }: { agent: AgentBlueprint }) {
  const agents = useViewer((state) => state.data.agents);
  const { settings, model } = agent;
  const builtin = agent.capabilities.filter((c) => c.builtin);
  const custom = agent.capabilities.filter((c) => !c.builtin);
  return (
    <div className="flex flex-col gap-5">
      {agent.source === "observed" && (
        <Note>
          This blueprint was built from what runs showed. Register the agent with{" "}
          <code className="text-fg">serve()</code> or <code className="text-fg">record()</code> to
          see its instructions, schemas and settings in full.
        </Note>
      )}
      <Warnings warnings={agent.warnings} />
      <Block title="Model">
        {model ? (
          <Facts
            items={[
              ["Name", <code key="name">{model.name}</code>],
              ["Provider", orDash(model.provider)],
              ["Id", orDash(model.id)],
              [
                "Offline",
                model.offline ? "yes: a test or function model, no provider is called" : "no",
              ],
            ]}
          />
        ) : (
          <Note>No model is set on the agent; one is passed at run time.</Note>
        )}
      </Block>
      <Block title="Settings">
        <Facts
          items={[
            ["Dependencies", orDash(agent.deps_type)],
            ["End strategy", orDash(settings.end_strategy)],
            ["Tool retries", orDash(settings.tool_retries)],
            ["Output retries", orDash(settings.output_retries)],
            [
              "Tool timeout",
              orDash(settings.tool_timeout === null ? null : `${settings.tool_timeout} s`),
            ],
            [
              "Model settings",
              settings.model_settings ? (
                <code key="model-settings">{JSON.stringify(settings.model_settings)}</code>
              ) : (
                orDash(null)
              ),
            ],
          ]}
        />
      </Block>
      <Block title="Toolsets" aside={plural(agent.tools.length, "tool")}>
        {agent.toolsets.length === 0 ? (
          <Note>This agent has no tools.</Note>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {agent.toolsets.map((toolset) => {
              const count = agent.tools.filter((tool) => tool.toolset === toolset.id).length;
              return (
                <li
                  key={toolset.id}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ui"
                >
                  <Icon name="toolset" size={13} className="text-faint" />
                  <span className="font-medium">{toolset.label}</span>
                  {toolset.wrappers.map((wrapper) => (
                    <Chip key={wrapper} mono>
                      {wrapper}
                    </Chip>
                  ))}
                  <span className="ml-auto text-faint text-meta">
                    {toolsetMeta(toolset, count)}
                  </span>
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
      </Block>
      {agent.delegates.length > 0 && (
        <Block title="Delegates to">
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {agent.delegates.map((delegate) => {
              const target = agents[delegate.agent_id];
              return (
                <li
                  key={`${delegate.tool_name}-${delegate.agent_id}`}
                  className="flex flex-wrap items-center gap-2 text-ui"
                >
                  <code className="text-tool">{delegate.tool_name}</code>
                  <Icon name="arrowRight" size={14} className="text-faint" label="calls" />
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
                  <Chip tone={delegate.evidence === "observed" ? "ok" : "neutral"}>
                    {delegate.evidence === "observed" ? "seen in a run" : "found in code"}
                  </Chip>
                </li>
              );
            })}
          </ul>
        </Block>
      )}
      <Block title="Capabilities">
        {custom.length === 0 ? (
          <p className="m-0 text-muted text-ui">None added.</p>
        ) : (
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
      </Block>
    </div>
  );
}

export function AgentInspector({ agent }: { agent: AgentBlueprint }) {
  const { output } = agent;
  return (
    <>
      <InspectorHeader tone="agent" icon="agent" kind="Agent blueprint" title={agent.label}>
        {agent.description && <p className="m-0 text-muted text-ui">{agent.description}</p>}
        <div className="flex flex-wrap items-center gap-1.5">
          {agent.model ? (
            <Chip tone="model" icon="model" mono>
              {agent.model.name}
            </Chip>
          ) : (
            <Chip icon="model">No model set</Chip>
          )}
          {agent.model?.offline && <Chip>offline model</Chip>}
          {agent.source === "observed" && (
            <Chip tone="accent" icon="info">
              Observed from runs
            </Chip>
          )}
          {agent.warnings.length > 0 && (
            <Chip tone="retry" icon="warning">
              {plural(agent.warnings.length, "warning")}
            </Chip>
          )}
        </div>
      </InspectorHeader>
      <StickyTabs
        group="agent"
        label="Agent blueprint"
        items={[
          { id: "overview", label: "Overview", content: <AgentOverview agent={agent} /> },
          {
            id: "instructions",
            label: "Instructions",
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
            id: "output",
            label: "Output",
            content: (
              <div className="flex flex-col gap-4">
                <Facts
                  items={[
                    ["Mode", output.mode],
                    [
                      "Type",
                      output.type_name ? <code key="type">{output.type_name}</code> : orDash(null),
                    ],
                    [
                      "Plain text",
                      output.allows_text === null
                        ? orDash(null)
                        : output.allows_text
                          ? "allowed"
                          : "not allowed",
                    ],
                    ["Output tools", orDash(output.tool_names.join(", "))],
                  ]}
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
              </div>
            ),
          },
        ]}
      />
    </>
  );
}

// ---------- tool ----------

export function ToolInspector({ agent, tool }: { agent: AgentBlueprint; tool: Tool }) {
  const agents = useViewer((state) => state.data.agents);
  const toolset = agent.toolsets.find((t) => t.id === tool.toolset);
  const delegate = agent.delegates.find((d) => d.tool_name === tool.name);
  const target = delegate ? agents[delegate.agent_id] : undefined;
  const retries =
    tool.max_retries !== null
      ? String(tool.max_retries)
      : agent.settings.tool_retries !== null
        ? `${agent.settings.tool_retries} (agent default)`
        : null;
  return (
    <>
      <InspectorHeader tone="tool" icon="tool" kind="Tool" title={tool.name} mono>
        {tool.description && <p className="m-0 text-muted text-ui">{tool.description}</p>}
        <p className="m-0 text-muted text-ui">
          of agent <span className="font-medium text-fg">{agent.label}</span>
        </p>
      </InspectorHeader>
      <StickyTabs
        group="tool"
        label="Tool"
        items={[
          {
            id: "overview",
            label: "Overview",
            content: (
              <div className="flex flex-col gap-4">
                {tool.origin === "observed" && (
                  <Note>
                    Seen in a run. The blueprint did not list this tool, so only what the model was
                    told about it is known.
                  </Note>
                )}
                <Facts
                  items={[
                    ["Toolset", orDash(toolset?.label ?? null)],
                    ["Kind", tool.kind],
                    [
                      "Call style",
                      tool.is_async === null ? orDash(null) : tool.is_async ? "async" : "sync",
                    ],
                    [
                      "Run context",
                      tool.takes_ctx === null
                        ? orDash(null)
                        : tool.takes_ctx
                          ? "takes ctx"
                          : "no ctx",
                    ],
                    ["Max retries", orDash(retries)],
                    [
                      "Function",
                      tool.function ? (
                        <code key="function" className="break-all">
                          {tool.function}
                        </code>
                      ) : (
                        orDash(null)
                      ),
                    ],
                  ]}
                />
                {delegate && (
                  <p className="m-0 flex flex-wrap items-center gap-2 text-ui">
                    Delegates to
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
                    <Chip tone={delegate.evidence === "observed" ? "ok" : "neutral"}>
                      {delegate.evidence === "observed" ? "seen in a run" : "found in code"}
                    </Chip>
                  </p>
                )}
              </div>
            ),
          },
          {
            id: "parameters",
            label: "Parameters",
            content: (
              <div className="flex flex-col gap-3">
                <SchemaTable
                  schema={tool.parameters_schema}
                  caption={`Parameters of ${tool.name}`}
                />
                <RawSchema schema={tool.parameters_schema} label="parameters schema" />
              </div>
            ),
          },
          {
            id: "returns",
            label: "Returns",
            content:
              tool.return_schema === null ? (
                <Note>No return schema is declared for this tool.</Note>
              ) : (
                <div className="flex flex-col gap-3">
                  <SchemaTable
                    schema={tool.return_schema}
                    caption={`Return value of ${tool.name}`}
                  />
                  <RawSchema schema={tool.return_schema} label="return schema" />
                </div>
              ),
          },
        ]}
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

export function GraphInspector({ graph }: { graph: GraphBlueprint }) {
  return (
    <>
      <InspectorHeader tone="graph" icon="graph" kind="Graph blueprint" title={graph.label}>
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone="graph">{plural(graph.nodes.length, "node")}</Chip>
          <Chip tone="graph">{plural(graph.edges.length, "edge")}</Chip>
          {graph.warnings.length > 0 && (
            <Chip tone="retry" icon="warning">
              {plural(graph.warnings.length, "warning")}
            </Chip>
          )}
        </div>
      </InspectorHeader>
      <StickyTabs
        group="graph"
        label="Graph blueprint"
        items={[
          {
            id: "overview",
            label: "Overview",
            content: (
              <div className="flex flex-col gap-5">
                <Warnings warnings={graph.warnings} />
                <Block title="Types">
                  <Facts
                    items={[
                      ["State", orDash(graph.state_type)],
                      ["Input", orDash(graph.input_type)],
                      ["Output", orDash(graph.output_type)],
                    ]}
                  />
                </Block>
                <Block title="Nodes" aside={String(graph.nodes.length)}>
                  <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                    {graph.nodes.map((node) => (
                      <li key={node.id} className="flex flex-wrap items-center gap-2 text-ui">
                        <Chip tone="graph">{nodeKind(node.kind)}</Chip>
                        <span className="font-medium">{graphNodeLabel(node)}</span>
                        {node.note && <span className="text-muted">{node.note}</span>}
                      </li>
                    ))}
                  </ul>
                </Block>
                <Note>Select a node on the canvas to see what leads into and out of it.</Note>
              </div>
            ),
          },
          {
            id: "mermaid",
            label: "Mermaid",
            content:
              graph.mermaid === null ? (
                <Note>This graph could not be rendered as Mermaid.</Note>
              ) : (
                <div className="flex flex-col items-start gap-2">
                  <CopyButton text={graph.mermaid} label="Copy Mermaid source" />
                  <pre className="m-0 w-full overflow-x-auto rounded-control border border-line bg-bg p-2.5 text-data text-fg">
                    {graph.mermaid}
                  </pre>
                </div>
              ),
          },
        ]}
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
      </InspectorHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto p-3">
        <Facts
          items={[
            ["Id", <code key="id">{node.id}</code>],
            ["Kind", node.kind],
            ["Note", orDash(node.note)],
          ]}
        />
        <Block title="Comes from">{list(incoming, "source")}</Block>
        <Block title="Leads to">{list(outgoing, "target")}</Block>
      </div>
    </>
  );
}
