/**
 * The agent blueprint screen. Stage seam: `AgentOutline` is the text view of a blueprint;
 * the canvas (`canvas/AgentCanvas`) takes over the `main` slot and the outline's sections
 * move into the inspector tabs (Overview, Instructions, Output).
 */
import { useViewer } from "../data/store";
import { plural } from "../shell/format";
import { formatRoute } from "../shell/router";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Badge, Chip } from "../ui/Badge";
import { Icon } from "../ui/Icon";
import { Tooltip } from "../ui/overlays";
import { SchemaTable } from "../ui/SchemaTable";
import type { AgentBlueprint, Instruction, Tool } from "../wire";
import { Card, Facts, orDash, ScreenHeader, Section } from "./parts";

function InstructionItem({ item }: { item: Instruction }) {
  const kind = item.kind === "system_prompt" ? "System prompt" : "Instructions";
  return (
    <li className="flex flex-col gap-1.5 border-line border-b p-3 last:border-b-0">
      <div className="flex items-center gap-1.5">
        <Chip>{kind}</Chip>
        {item.dynamic && <Chip tone="accent">Dynamic</Chip>}
      </div>
      {item.text !== null ? (
        <p className="m-0 whitespace-pre-wrap text-fg text-ui">{item.text}</p>
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

function ToolItem({ tool }: { tool: Tool }) {
  const traits = [
    tool.kind !== "function" ? tool.kind : null,
    tool.is_async === null ? null : tool.is_async ? "async" : "sync",
    tool.takes_ctx ? "takes ctx" : null,
    tool.max_retries !== null ? `max retries ${tool.max_retries}` : null,
    tool.origin === "observed" ? "observed" : null,
  ].filter((t): t is string => t !== null);
  return (
    <li className="tone-tool border-line border-b last:border-b-0">
      <details className="group">
        <summary className="flex list-none items-start gap-2 p-3 hover:bg-surface-2 focus-visible:outline-offset-[-2px] [&::-webkit-details-marker]:hidden">
          <Icon
            name="chevronRight"
            size={12}
            className="mt-1 text-faint transition-transform duration-150 ease-out group-open:rotate-90"
          />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className="tone-text font-medium font-mono text-data">{tool.name}</span>
              <span className="text-faint text-meta">{traits.join(" · ")}</span>
            </span>
            {tool.description && <span className="text-muted text-ui">{tool.description}</span>}
          </span>
        </summary>
        <div className="flex flex-col gap-3 px-3 pb-3 pl-8">
          <SchemaTable schema={tool.parameters_schema} caption={`Parameters of ${tool.name}`} />
          {tool.return_schema !== null && (
            <SchemaTable schema={tool.return_schema} caption={`Return value of ${tool.name}`} />
          )}
          {tool.function && (
            <p className="m-0 break-all font-mono text-faint text-meta">{tool.function}</p>
          )}
        </div>
      </details>
    </li>
  );
}

export function AgentOutline({ agent }: { agent: AgentBlueprint }) {
  const agents = useViewer((state) => state.data.agents);
  const builtin = agent.capabilities.filter((c) => c.builtin);
  const custom = agent.capabilities.filter((c) => !c.builtin);
  const { settings, output } = agent;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-7 px-6 py-6">
      <ScreenHeader
        icon="agent"
        tone="agent"
        kind="Agent blueprint"
        title={agent.label}
        description={agent.description}
        chips={
          <>
            {agent.model ? (
              <Chip tone="model" icon="model" mono>
                {agent.model.name}
              </Chip>
            ) : (
              <Chip icon="model">No model set</Chip>
            )}
            {agent.model?.offline && (
              <Tooltip content="A test or function model: runs offline and calls no provider.">
                <span className="inline-flex">
                  <Chip>offline model</Chip>
                </span>
              </Tooltip>
            )}
            {agent.source === "observed" && (
              <Tooltip content="Built from what its runs showed. Pass the agent to serve() or record() to see instructions, schemas and settings in full.">
                <span className="inline-flex">
                  <Chip tone="accent" icon="info">
                    Observed from runs
                  </Chip>
                </span>
              </Tooltip>
            )}
            {agent.warnings.length > 0 && (
              <Chip tone="retry" icon="warning">
                {agent.warnings.length === 1 ? "1 warning" : `${agent.warnings.length} warnings`}
              </Chip>
            )}
          </>
        }
      />

      {agent.warnings.length > 0 && (
        <Section title="Warnings" count={agent.warnings.length}>
          <ul className="tone-retry m-0 flex list-none flex-col gap-1 p-0">
            {agent.warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-2 text-ui">
                <Icon name="warning" size={14} className="tone-text mt-0.5" />
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Instructions" count={agent.instructions.length}>
        {agent.instructions.length === 0 ? (
          <p className="m-0 text-muted text-ui">
            {agent.source === "observed"
              ? "Not known yet. The resolved instructions appear on each run."
              : "This agent has no instructions or system prompts."}
          </p>
        ) : (
          <Card>
            <ul className="m-0 list-none p-0">
              {agent.instructions.map((item, i) => (
                <InstructionItem key={`${item.kind}-${i}`} item={item} />
              ))}
            </ul>
          </Card>
        )}
      </Section>

      <Section title="Tools" count={agent.tools.length}>
        {agent.toolsets.length === 0 && agent.tools.length === 0 && (
          <p className="m-0 text-muted text-ui">This agent has no tools.</p>
        )}
        {agent.toolsets.map((toolset) => {
          const tools = agent.tools.filter((tool) => tool.toolset === toolset.id);
          return (
            <Card key={toolset.id}>
              <header className="flex flex-wrap items-center gap-2 border-line border-b bg-surface-2 px-3 py-1.5">
                <h4 className="m-0 font-medium text-ui">{toolset.label}</h4>
                <span className="text-faint text-meta">{toolset.kind}</span>
                {toolset.wrappers.map((wrapper) => (
                  <Chip key={wrapper} mono>
                    {wrapper}
                  </Chip>
                ))}
                <span className="ml-auto text-faint text-meta">
                  {toolset.opaque ? "resolved at run time" : plural(tools.length, "tool")}
                </span>
              </header>
              {tools.length > 0 ? (
                <ul className="m-0 list-none p-0">
                  {tools.map((tool) => (
                    <ToolItem key={tool.name} tool={tool} />
                  ))}
                </ul>
              ) : (
                <p className="m-0 p-3 text-muted text-ui">
                  {toolset.opaque
                    ? "This toolset lists its tools only while a run is in progress. The tools it provides show up once a run uses them."
                    : "No tools."}
                </p>
              )}
            </Card>
          );
        })}
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
      </Section>

      {agent.delegates.length > 0 && (
        <Section title="Delegates to" count={agent.delegates.length}>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {agent.delegates.map((delegate) => {
              const target = agents[delegate.agent_id];
              return (
                <li
                  key={`${delegate.tool_name}-${delegate.agent_id}`}
                  className="flex flex-wrap items-center gap-2 text-ui"
                >
                  <code className="text-tool">{delegate.tool_name}</code>
                  <Icon name="output" size={14} className="text-faint" label="calls" />
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
        </Section>
      )}

      <Section title="Output">
        <Facts
          items={[
            ["Mode", output.mode],
            ["Type", orDash(output.type_name)],
            [
              "Plain text allowed",
              output.allows_text === null ? orDash(null) : output.allows_text ? "yes" : "no",
            ],
            ["Output tools", orDash(output.tool_names.join(", "))],
          ]}
        />
        {output.json_schema !== null && (
          <SchemaTable schema={output.json_schema} caption={`Output of ${agent.label}`} />
        )}
      </Section>

      <Section title="Settings">
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
            ["Capabilities", orDash(custom.map((c) => c.name).join(", "))],
          ]}
        />
        {builtin.length > 0 && (
          <details className="text-ui">
            <summary className="text-muted">
              Built-in capabilities <Badge>{builtin.length}</Badge>
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
      </Section>
    </div>
  );
}

export function AgentScreen({ id }: { id: string }) {
  const agent = useViewer((state) => state.data.agents[id]);
  if (!agent) {
    return (
      <NotFound
        what="Agent blueprint"
        detail={`This session has no agent blueprint with the id "${id}". The link may come from another session.`}
      />
    );
  }
  return (
    <Workspace
      main={
        <>
          <WaitingForRun />
          <AgentOutline agent={agent} />
        </>
      }
    />
  );
}
