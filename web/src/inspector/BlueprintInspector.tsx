/**
 * The inspector for blueprint parts. A blueprint is short and read together, so each panel is
 * one scroll of sections under headings that stay in view; there are no tabs here.
 *
 *   agent       Warnings · Model · Instructions · Toolsets · Output · Delegates to · Settings ·
 *               Capabilities · Mermaid
 *   toolset     Tools · Definition
 *   tool        Parameters · Returns · Definition
 *   capability  Definition · Skills
 *   skill       When to use · Instructions · Definition
 *   graph       Warnings · Types · Nodes · Mermaid
 *   graph node  Node · Comes from · Leads to
 *
 * A section with nothing to say is left out, and so is a setting the blueprint does not set.
 * What Pydantic AI added by default is folded under "From Pydantic AI", open while defaults
 * are shown, and every such part keeps its panel.
 * Every part a panel names (a tool, its toolset, its agent, a node an edge leads to) can be
 * picked from the panel, as it can on the canvas (see `pick.tsx`).
 */
import { type ReactNode, useMemo } from "react";
import { graphNodeLabel, nodeKind } from "../canvas/layout";
import { agentMermaid } from "../data/mermaid";
import {
  CAPABILITY_CATEGORIES,
  capabilityCategory,
  categoryNames,
  defaultCapability,
  LOAD_CAPABILITY,
  libraryToolsets,
  type ShownCapability,
  shownCapabilities,
} from "../data/model";
import { canvasModel, fileName } from "../data/selectors";
import { useViewer } from "../data/store";
import { Facts } from "../screens/parts";
import { useShowDefaults } from "../shell/defaults";
import { plural } from "../shell/format";
import { formatRoute } from "../shell/router";
import { Badge, Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { Icon } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { PathText } from "../ui/PathText";
import { SchemaTable } from "../ui/SchemaTable";
import type {
  AgentBlueprint,
  Capability,
  Delegate,
  GraphBlueprint,
  GraphNode,
  Instruction,
  Json,
  Skill,
  Tool,
  Toolset,
} from "../wire";
import { InspectorHeader, Note, type Reveal, type SectionItem, Sections, TextBlock } from "./parts";
import { PickLink, PickRow } from "./pick";
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

/**
 * A prompt written as a triple-quoted string that opens and closes on lines of its own starts
 * and ends with line breaks that only the source needed. Lines that are blank from end to end
 * are left off; the indentation of the first line of text is kept.
 */
export const trimBlankLines = (text: string): string =>
  text.replace(/^(?:[^\S\n]*\n)+/, "").replace(/(?:\n[^\S\n]*)+$/, "");

function InstructionItem({ item }: { item: Instruction }) {
  // A template (an f-string, a `str.format` call) keeps its text, with holes: it is dynamic and
  // still readable, whole.
  const template = item.dynamic && item.text !== null;
  const text = item.text === null ? null : trimBlankLines(item.text);
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
      {text !== null ? (
        template ? (
          <>
            <TemplateText text={text} />
            <p className="m-0 text-faint text-meta">
              A template: the marked parts are filled in when the code runs.
            </p>
          </>
        ) : (
          <TextBlock text={text} />
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
  if (toolset.opaque && count === 0) return "decided at run time";
  return plural(count, "tool");
}

/**
 * How many parameters a tool takes: the fields of its parameters schema. Null when that is not
 * known: the tool is defined outside the files that were read, or its schema is not an object.
 */
export function parameterCount(schema: Json | null): number | null {
  if (schema === null || typeof schema !== "object" || Array.isArray(schema)) return null;
  const { properties } = schema;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    return Object.keys(properties).length;
  }
  return schema.type === "object" ? 0 : null;
}

const parameterWords = (count: number | null): string =>
  count === null
    ? "parameters not known"
    : count === 0
      ? "no parameters"
      : plural(count, "parameter");

/** The first line of a docstring that has text on it: what a list has room for. */
export function firstLine(text: string | null): string | null {
  const line = text
    ?.split("\n")
    .map((part) => part.trim())
    .find((part) => part !== "");
  return line ?? null;
}

const toolsOf = (agent: AgentBlueprint, toolset: Toolset): Tool[] =>
  agent.tools.filter((tool) => tool.toolset === toolset.id);

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
  // computed is known only as the expression. A name read from the environment is the variable's.
  const { env } = model;
  const named = env === null && (model.expression === null || model.name !== model.expression);
  return {
    id: "model",
    title: "Model",
    content: (
      <Facts
        items={known([
          named && ["Name", <code key="name">{model.name}</code>],
          env !== null && [
            model.model_class !== null ? "Model name" : "Model",
            <EnvironmentName key="env" variable={env.variable} />,
          ],
          env !== null && [
            "Default",
            env.default !== null ? (
              <span key="default" className="flex min-w-0 flex-col items-start gap-0.5">
                <code className="break-all">{env.default}</code>
                <span className="text-faint text-meta">
                  Only if {env.variable} is not set; the environment decides.
                </span>
              </span>
            ) : (
              <span key="default" className="text-muted">
                None in the source: {env.variable} has to be set.
              </span>
            ),
          ],
          model.model_class !== null && ["Class", <code key="class">{model.model_class}</code>],
          model.expression !== null && [
            named || model.model_class !== null ? "Built by" : "Set by",
            <Expression key="expression" code={model.expression} hint={!named && env === null} />,
          ],
          model.provider !== null && ["Provider", model.provider],
          model.id !== null && model.id !== model.name && ["Id", model.id],
          model.offline && ["Offline", "A test or function model: no provider is called."],
        ])}
      />
    ),
  };
}

/** The name of an environment variable a value is read from when the code runs. */
function EnvironmentName({ variable }: { variable: string }) {
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <span className="inline-flex max-w-full items-center gap-1.5">
        <Chip icon="code">env</Chip>
        <code className="break-all">{variable}</code>
      </span>
      <span className="text-faint text-meta">Read from the environment when the code runs.</span>
    </span>
  );
}

/**
 * What Pydantic AI added by default, folded away: open while defaults are shown, so that what
 * the author wrote comes first either way.
 */
function FromPydanticAI({
  title = "From Pydantic AI",
  count,
  open,
  children,
}: {
  title?: string;
  count: number;
  open: boolean;
  children: ReactNode;
}) {
  return (
    <details open={open} className="text-ui">
      <summary className="text-muted">
        {title} <Badge>{count}</Badge>
      </summary>
      <div className="mt-2 flex flex-col gap-3">{children}</div>
    </details>
  );
}

/** A value Pydantic AI set by default, where defaults are shown. */
function DefaultValue({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {children}
      <Chip>default</Chip>
    </span>
  );
}

function settingsSection(agent: AgentBlueprint, showDefaults: boolean): SectionItem {
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

  // A setting whose value is the one Pydantic AI gives every agent is folded away with the
  // other defaults, or marked as one where they are shown.
  const fields: [key: string, label: string, shown: ReactNode | null][] = [
    ["end_strategy", "End strategy", value("end_strategy", settings.end_strategy)],
    ["tool_retries", "Tool retries", value("tool_retries", settings.tool_retries)],
    ["output_retries", "Output retries", value("output_retries", settings.output_retries)],
    [
      "tool_timeout",
      "Tool timeout",
      value("tool_timeout", settings.tool_timeout === null ? null : `${settings.tool_timeout} s`),
    ],
  ];
  const byDefault = (key: string): boolean => settings.defaults.includes(key);
  const facts = known([
    row("Dependencies", agent.deps_type && <code className="break-all">{agent.deps_type}</code>),
    ...fields
      .filter(([key]) => showDefaults || !byDefault(key))
      .map(([key, label, shown]) =>
        row(label, shown !== null && byDefault(key) ? <DefaultValue>{shown}</DefaultValue> : shown),
      ),
    row("Model settings", computed("model_settings")),
  ]);
  const folded = showDefaults
    ? []
    : known(fields.filter(([key]) => byDefault(key)).map(([, label, shown]) => row(label, shown)));
  return {
    id: "settings",
    title: "Settings",
    content:
      facts.length === 0 && modelRows.length === 0 && folded.length === 0 ? (
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
          {folded.length > 0 && (
            <FromPydanticAI title="Pydantic AI defaults" count={folded.length} open={false}>
              <Facts items={folded} />
            </FromPydanticAI>
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

function DetailValue({ value }: { value: Json }) {
  if (typeof value === "string") return <code className="break-all">{value}</code>;
  if (Array.isArray(value) && value.every((v) => typeof v === "string")) {
    return (
      <span className="flex flex-wrap gap-1.5">
        {value.map((v, i) => (
          <Chip key={`${v}-${i}`} mono>
            {String(v)}
          </Chip>
        ))}
      </span>
    );
  }
  return <code>{JSON.stringify(value)}</code>;
}

/** Where a capability is defined (Core, Harness) and whether it loads on demand. */
function CapabilityChips({ item }: { item: Capability }) {
  return (
    <>
      {item.package === "pydantic_ai" && <Chip>Core</Chip>}
      {item.package === "pydantic_ai_harness" && <Chip>Harness</Chip>}
      {item.deferred && <Chip tone="accent">on demand</Chip>}
    </>
  );
}

/** What a capability was given, and the toolsets and provider-side tools it gives the agent. */
const capabilityFacts = (agent: AgentBlueprint, item: Capability): Fact[] =>
  known([
    ...Object.entries(item.details).map(
      ([key, value]): Fact => [key, <DetailValue key={key} value={value} />],
    ),
    ...Object.entries(item.expressions).map(
      ([key, code]): Fact => [key, <Expression key={key} code={code} hint={false} />],
    ),
    item.toolsets.length > 0 && [
      "Toolsets",
      <span key="toolsets" className="flex flex-wrap gap-x-3 gap-y-1">
        {item.toolsets.map((id) => (
          <PickLink key={id} target={{ kind: "toolset", agentId: agent.id, toolsetId: id }}>
            {agent.toolsets.find((t) => t.id === id)?.label ?? id}
          </PickLink>
        ))}
      </span>,
    ],
    item.native_tools.length > 0 && [
      "Provider-side tools",
      <span key="native" className="flex flex-wrap gap-1.5">
        {item.native_tools.map((kind) => (
          <Chip key={kind} tone="tool" mono>
            {kind}
          </Chip>
        ))}
      </span>,
    ],
  ]);

function CapabilityItem({ agent, entry }: { agent: AgentBlueprint; entry: ShownCapability }) {
  const { capability: item, index } = entry;
  const facts = known([
    ...capabilityFacts(agent, item),
    item.skills.length > 0 && [
      "Skills",
      <span key="skills" className="flex flex-wrap gap-x-3 gap-y-1">
        {item.skills.map((skill) => (
          <PickLink
            key={skill.name}
            target={{ kind: "skill", agentId: agent.id, index, name: skill.name }}
            className="break-all font-mono text-data"
          >
            {skill.name}
          </PickLink>
        ))}
      </span>,
    ],
  ]);
  return (
    <li className="flex flex-col gap-1.5 text-ui">
      <div className="flex flex-wrap items-center gap-1.5">
        <PickLink
          target={{ kind: "capability", agentId: agent.id, index }}
          className="break-all font-medium font-mono text-data text-fg"
        >
          {item.name}
        </PickLink>
        <CapabilityChips item={item} />
      </div>
      <Facts items={facts} />
    </li>
  );
}

function ToolsetItem({ agent, toolset }: { agent: AgentBlueprint; toolset: Toolset }) {
  const tools = toolsOf(agent, toolset);
  return (
    <li className="flex flex-col gap-1 text-ui">
      <div className="flex items-baseline gap-2">
        <Icon name="toolset" size={13} className="shrink-0 self-center text-faint" />
        <span className="min-w-0 flex-1 break-words font-medium">
          <PickLink target={{ kind: "toolset", agentId: agent.id, toolsetId: toolset.id }}>
            {toolset.label}
          </PickLink>
        </span>
        <span className="shrink-0 text-faint text-meta">{toolsetMeta(toolset, tools.length)}</span>
      </div>
      {tools.length > 0 && (
        <ul
          aria-label={`Tools of ${toolset.label}`}
          className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 pl-[21px]"
        >
          {tools.map((tool) => (
            <li key={tool.name} className="min-w-0 max-w-full">
              <PickLink
                target={{ kind: "tool", agentId: agent.id, name: tool.name }}
                className="break-all font-mono text-data text-tool"
              >
                {tool.name}
              </PickLink>
            </li>
          ))}
        </ul>
      )}
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
      {toolset.expression !== null && <Expression code={toolset.expression} hint={false} />}
      <SourceRef location={toolset.location} />
    </li>
  );
}

function NativeTools({ kinds }: { kinds: readonly string[] }) {
  return (
    <p className="m-0 flex flex-wrap items-center gap-1.5 text-muted text-ui">
      Provider-side tools:
      {kinds.map((name) => (
        <Chip key={name} tone="tool" mono>
          {name}
        </Chip>
      ))}
    </p>
  );
}

function agentSections(agent: AgentBlueprint, showDefaults: boolean): SectionItem[] {
  const shown = shownCapabilities(agent);
  const added = agent.capabilities.flatMap((capability, index) =>
    defaultCapability(capability) ? [{ capability, index }] : [],
  );
  const library = libraryToolsets(agent);
  const ownToolsets = agent.toolsets.filter((toolset) => !library.has(toolset.id));
  const givenToolsets = agent.toolsets.filter((toolset) => library.has(toolset.id));
  // A provider-side tool only capabilities Pydantic AI added give is one of its defaults too.
  const addedNative = agent.native_tools.filter((kind) => {
    const givers = agent.capabilities.filter((item) => item.native_tools.includes(kind));
    return givers.length > 0 && givers.every(defaultCapability);
  });
  const ownNative = agent.native_tools.filter((kind) => !addedNative.includes(kind));
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
          {agent.toolsets.length === 0 && <Note>This agent has no tools.</Note>}
          {ownToolsets.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {ownToolsets.map((toolset) => (
                <ToolsetItem key={toolset.id} agent={agent} toolset={toolset} />
              ))}
            </ul>
          )}
          {ownNative.length > 0 && <NativeTools kinds={ownNative} />}
          {givenToolsets.length + addedNative.length > 0 && (
            <FromPydanticAI count={givenToolsets.length + addedNative.length} open={showDefaults}>
              {givenToolsets.length > 0 && (
                <ul className="m-0 flex list-none flex-col gap-3 p-0">
                  {givenToolsets.map((toolset) => (
                    <ToolsetItem key={toolset.id} agent={agent} toolset={toolset} />
                  ))}
                </ul>
              )}
              {addedNative.length > 0 && <NativeTools kinds={addedNative} />}
            </FromPydanticAI>
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
              <PickLink
                target={{ kind: "tool", agentId: agent.id, name: delegate.tool_name }}
                className="font-mono text-data text-tool"
              >
                {delegate.tool_name}
              </PickLink>
              <Icon name="arrowRight" size={14} className="text-faint" label="calls" />
              <DelegateTarget delegate={delegate} />
            </li>
          ))}
        </ul>
      ),
    },
    settingsSection(agent, showDefaults),
    agent.capabilities.length > 0 && {
      id: "capabilities",
      title: "Capabilities",
      aside: shown.length > 0 ? String(shown.length) : undefined,
      content: (
        <>
          {CAPABILITY_CATEGORIES.map(([id, label]) => {
            const members = shown.filter((entry) => capabilityCategory(entry.capability) === id);
            return (
              members.length > 0 && (
                <section key={id} aria-label={label} className="flex flex-col gap-2">
                  <h5 className="m-0 font-medium text-faint text-meta">
                    {label} <Badge>{members.length}</Badge>
                  </h5>
                  <ul className="m-0 flex list-none flex-col gap-3 p-0">
                    {members.map((entry) => (
                      <CapabilityItem key={entry.index} agent={agent} entry={entry} />
                    ))}
                  </ul>
                </section>
              )
            );
          })}
          {added.length > 0 && (
            <FromPydanticAI count={added.length} open={showDefaults}>
              <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0">
                {added.map(({ capability, index }) => (
                  <li key={index} className="flex min-w-0 max-w-full items-baseline gap-1.5">
                    <PickLink
                      target={{ kind: "capability", agentId: agent.id, index }}
                      className="break-all font-mono text-data"
                    >
                      {capability.name}
                    </PickLink>
                    {capability.stack && (
                      <span className="shrink-0 text-faint text-meta">via {capability.stack}</span>
                    )}
                  </li>
                ))}
              </ul>
            </FromPydanticAI>
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
  const showDefaults = useShowDefaults();
  return (
    <>
      <InspectorHeader tone="agent" icon="agent" kind="Agent blueprint" title={agent.label}>
        {agent.description && <p className="m-0 text-muted text-ui">{agent.description}</p>}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProvenanceChip source={agent.source} />
          <SourceRef location={agent.location} />
        </div>
      </InspectorHeader>
      <Sections
        label="Agent blueprint"
        items={agentSections(agent, showDefaults)}
        reveal={reveal}
      />
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
        <OfAgent agent={agent} toolset={toolset} />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProvenanceChip source={tool.origin} />
          <SourceRef location={tool.location} />
        </div>
        {tool.origin === "observed" && (
          <Note>
            The blueprint did not list this tool, so only what the model was told about it is known.
          </Note>
        )}
        {tool.library_version !== null ? (
          <Note>
            Defined by Pydantic AI, and read from the source of the installed Pydantic AI{" "}
            {tool.library_version}. The project may use another version, in which it can differ.
          </Note>
        ) : (
          libraryToolsets(agent).has(tool.toolset) && <Note>Added by Pydantic AI by default.</Note>
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
                  {tool.library_version !== null
                    ? "The function this tool runs is chosen when the code runs, so its parameters are not known."
                    : "This tool is defined outside the files that were read, so its parameters are not known."}
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

/**
 * Whose part this is: "of agent triage", or "in Agent tools of triage" for a tool. Each name
 * picks its part, so a panel always leads back up.
 */
function OfAgent({ agent, toolset }: { agent: AgentBlueprint; toolset?: Toolset | undefined }) {
  const agentLink = (
    <PickLink target={{ kind: "agent", agentId: agent.id }} className="font-medium text-fg">
      {agent.label}
    </PickLink>
  );
  return (
    <p className="m-0 text-muted text-ui">
      {toolset ? (
        <>
          in{" "}
          <PickLink
            target={{ kind: "toolset", agentId: agent.id, toolsetId: toolset.id }}
            className="font-medium text-fg"
          >
            {toolset.label}
          </PickLink>{" "}
          of {agentLink}
        </>
      ) : (
        <>of agent {agentLink}</>
      )}
    </p>
  );
}

/** What `load_capability` can load on this agent: its skills and its on-demand capabilities. */
function Loadable({ agent }: { agent: AgentBlueprint }) {
  const items = agent.capabilities.flatMap((capability, index) => [
    ...capability.skills.map((skill) => ({
      key: `skill:${index}:${skill.name}`,
      target: { kind: "skill", agentId: agent.id, index, name: skill.name } as const,
      name: skill.name,
      what: `skill of ${capability.name}`,
    })),
    ...(capability.deferred
      ? [
          {
            key: `capability:${index}`,
            target: { kind: "capability", agentId: agent.id, index } as const,
            name: capability.capability_id ?? capability.name,
            what: "capability",
          },
        ]
      : []),
  ]);
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5 text-ui">
      <span className="text-faint">It can load</span>
      <ul aria-label="What it can load" className="m-0 flex list-none flex-col gap-1 p-0">
        {items.map((item) => (
          <li key={item.key} className="flex flex-wrap items-baseline gap-1.5">
            <PickLink target={item.target} className="break-all font-mono text-data">
              {item.name}
            </PickLink>
            <span className="text-faint text-meta">{item.what}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A tool a run called that no blueprint lists: there is nothing static to show. */
export function UnlistedToolInspector({ agent, name }: { agent: AgentBlueprint; name: string }) {
  if (name === LOAD_CAPABILITY) {
    return (
      <>
        <InspectorHeader tone="tool" icon="tool" kind="Tool" title={name} mono>
          <OfAgent agent={agent} />
        </InspectorHeader>
        <div className="flex flex-col gap-3 p-3">
          <Note>
            Pydantic AI adds this tool to an agent with on-demand capabilities or skills; the model
            calls it with an id. Select one of its calls in the timeline to see what it loaded.
          </Note>
          <Loadable agent={agent} />
        </div>
      </>
    );
  }
  return (
    <>
      <InspectorHeader tone="tool" icon="tool" kind="Tool" title={name} mono>
        <OfAgent agent={agent} />
      </InspectorHeader>
      <div className="p-3">
        <Note>
          The blueprint does not list this tool: it comes from a toolset whose tools are decided at
          run time. Select one of its calls in the timeline to see arguments and results.
        </Note>
      </div>
    </>
  );
}

// ---------- toolset ----------

const TOOLSET_KINDS: Record<string, string> = {
  agent: "The agent's own tools",
  function: "Function toolset",
  mcp: "MCP server",
  dynamic: "Built for each run",
  other: "Other toolset",
};

export function ToolsetInspector({ agent, toolset }: { agent: AgentBlueprint; toolset: Toolset }) {
  const tools = toolsOf(agent, toolset);
  const owners = agent.capabilities.flatMap((capability, index) =>
    capability.toolsets.includes(toolset.id) ? [{ capability, index }] : [],
  );
  return (
    <>
      <InspectorHeader tone="tool" icon="toolset" kind="Toolset" title={toolset.label}>
        <OfAgent agent={agent} />
        <SourceRef location={toolset.location} />
        {libraryToolsets(agent).has(toolset.id) && <Note>Added by Pydantic AI by default.</Note>}
      </InspectorHeader>
      <Sections
        label="Toolset"
        items={[
          {
            id: "tools",
            title: "Tools",
            aside: tools.length > 0 ? String(tools.length) : undefined,
            content:
              tools.length === 0 ? (
                <Note>
                  {toolset.opaque
                    ? "This toolset lists its tools only when the code runs, so they are not known from the blueprint."
                    : "No tools are registered on this toolset."}
                </Note>
              ) : (
                // -mx-2: the rows' own padding lines their text up with the rest of the panel.
                <ul aria-label={`Tools of ${toolset.label}`} className="-mx-2 m-0 list-none p-0">
                  {tools.map((tool) => {
                    const summary = firstLine(tool.description);
                    const delegates = agent.delegates.some((d) => d.tool_name === tool.name);
                    return (
                      <li key={tool.name}>
                        <PickRow target={{ kind: "tool", agentId: agent.id, name: tool.name }}>
                          <span className="flex min-w-0 items-center gap-2">
                            <Icon
                              name={delegates ? "agent" : "tool"}
                              size={13}
                              className="shrink-0 text-tool"
                            />
                            <code className="min-w-0 flex-1 break-all text-data text-fg">
                              {tool.name}
                            </code>
                            <span className="shrink-0 text-faint text-meta">
                              {parameterWords(parameterCount(tool.parameters_schema))}
                            </span>
                          </span>
                          {summary && (
                            <span className="truncate pl-[21px] text-meta text-muted">
                              {summary}
                            </span>
                          )}
                        </PickRow>
                      </li>
                    );
                  })}
                </ul>
              ),
          },
          {
            id: "definition",
            title: "Definition",
            content: (
              <Facts
                items={known([
                  ["Kind", TOOLSET_KINDS[toolset.kind] ?? toolset.kind],
                  // The agent's own toolset is known to Pydantic AI by the placeholder "<agent>",
                  // which says nothing that the kind does not.
                  toolset.toolset_id !== null &&
                    toolset.kind !== "agent" && [
                      "Id",
                      <code key="id" className="break-all">
                        {toolset.toolset_id}
                      </code>,
                    ],
                  toolset.wrappers.length > 0 && [
                    "Wrapped by",
                    <span key="wrappers" className="flex flex-wrap gap-1.5">
                      {toolset.wrappers.map((wrapper) => (
                        <Chip key={wrapper} mono>
                          {wrapper}
                        </Chip>
                      ))}
                    </span>,
                  ],
                  owners.length > 0 && [
                    "Given by",
                    <span key="owners" className="flex flex-wrap gap-x-3 gap-y-1">
                      {owners.map(({ capability, index }) => (
                        <PickLink
                          key={index}
                          target={{ kind: "capability", agentId: agent.id, index }}
                          className="break-all font-mono text-data"
                        >
                          {capability.name}
                        </PickLink>
                      ))}
                    </span>,
                  ],
                  toolset.expression !== null && [
                    "Built by",
                    <Expression key="expression" code={toolset.expression} />,
                  ],
                ])}
              />
            ),
          },
        ]}
      />
    </>
  );
}

// ---------- capability ----------

export function CapabilityInspector({
  agent,
  index,
  capability,
}: {
  agent: AgentBlueprint;
  /** Its index in `blueprint.capabilities`. */
  index: number;
  capability: Capability;
}) {
  const facts = known([
    capability.deferred && ["Loaded", "On demand, when the model asks for it"],
    ...capabilityFacts(agent, capability),
  ]);
  return (
    <>
      <InspectorHeader
        tone="neutral"
        icon="capability"
        kind="Capability"
        title={capability.name}
        mono
      >
        <OfAgent agent={agent} />
        <div className="flex flex-wrap items-center gap-1.5">
          <CapabilityChips item={capability} />
          <span className="text-muted text-ui">{categoryNames(capability).label}</span>
        </div>
        {capability.builtin && <Note>Added by Pydantic AI to every agent.</Note>}
        {capability.stack && <Note>Added by {capability.stack}.</Note>}
      </InspectorHeader>
      <Sections
        label="Capability"
        items={present([
          {
            id: "definition",
            title: "Definition",
            content:
              facts.length > 0 ? (
                <Facts items={facts} />
              ) : (
                <Note>
                  It was given nothing the blueprint records, and gives the agent no toolset or
                  provider-side tool.
                </Note>
              ),
          },
          capability.skills.length > 0 && {
            id: "skills",
            title: "Skills",
            aside: String(capability.skills.length),
            content: (
              // -mx-2: the rows' own padding lines their text up with the rest of the panel.
              <ul aria-label={`Skills of ${capability.name}`} className="-mx-2 m-0 list-none p-0">
                {capability.skills.map((skill) => (
                  <li key={skill.name}>
                    <PickRow target={{ kind: "skill", agentId: agent.id, index, name: skill.name }}>
                      <span className="flex min-w-0 items-center gap-2">
                        <Icon name="instructions" size={13} className="shrink-0 text-faint" />
                        <code className="min-w-0 flex-1 break-all text-data text-fg">
                          {skill.name}
                        </code>
                        {!skill.model_invocable && <Chip className="shrink-0">hidden</Chip>}
                      </span>
                      <span className="truncate pl-[21px] text-meta text-muted">
                        {firstLine(skill.description)}
                      </span>
                    </PickRow>
                  </li>
                ))}
              </ul>
            ),
          },
        ])}
      />
    </>
  );
}

// ---------- skill ----------

export function SkillInspector({
  agent,
  index,
  capability,
  skill,
}: {
  agent: AgentBlueprint;
  /** The index of the capability that offers it in `blueprint.capabilities`. */
  index: number;
  capability: Capability;
  skill: Skill;
}) {
  const body = trimBlankLines(skill.body);
  return (
    <>
      <InspectorHeader tone="neutral" icon="instructions" kind="Skill" title={skill.name} mono>
        <p className="m-0 text-muted text-ui">
          of{" "}
          <PickLink
            target={{ kind: "capability", agentId: agent.id, index }}
            className="font-medium font-mono text-data text-fg"
          >
            {capability.name}
          </PickLink>{" "}
          ·{" "}
          <PickLink target={{ kind: "agent", agentId: agent.id }} className="font-medium text-fg">
            {agent.label}
          </PickLink>
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          {skill.model_invocable ? (
            <Chip tone="accent">on demand</Chip>
          ) : (
            <Chip>hidden from the model</Chip>
          )}
        </div>
        <p className="m-0 text-muted text-ui">{skill.description}</p>
      </InspectorHeader>
      <Sections
        label="Skill"
        items={present([
          skill.when_to_use !== null && {
            id: "when",
            title: "When to use",
            content: <p className="m-0 text-fg text-ui">{skill.when_to_use}</p>,
          },
          {
            id: "instructions",
            title: "Instructions",
            content:
              body === "" ? (
                <Note>Its SKILL.md has nothing after the frontmatter.</Note>
              ) : (
                <>
                  <TextBlock
                    text={body}
                    className="rounded-control border border-line bg-bg px-2.5 py-2"
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Note>The Markdown the model gets when it loads the skill.</Note>
                    <CopyButton text={body} label="Copy the instructions" />
                  </div>
                </>
              ),
          },
          {
            id: "definition",
            title: "Definition",
            content: (
              <>
                <Facts
                  items={[
                    [
                      "Path",
                      <code key="path" className="flex min-w-0 text-data" title={skill.path}>
                        <PathText path={skill.path} />
                      </code>,
                    ],
                    [
                      "Loaded with",
                      <span key="id">
                        <code>{LOAD_CAPABILITY}</code> id <code>{skill.name}</code>
                      </span>,
                    ],
                  ]}
                />
                {!skill.model_invocable && (
                  <Note>
                    Its SKILL.md asks that the model not be told of it, so it is left out of the
                    catalog the model sees.
                  </Note>
                )}
              </>
            ),
          },
        ])}
      />
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
                      <PickLink
                        target={{ kind: "graphNode", nodeId: node.id }}
                        className="font-medium"
                      >
                        {graphNodeLabel(node)}
                      </PickLink>
                      {node.note && <span className="text-muted">{node.note}</span>}
                    </li>
                  ))}
                </ul>
                <Note>
                  Pick a node, here or on the canvas, to see what leads into and out of it.
                </Note>
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
            <PickLink target={{ kind: "graphNode", nodeId: edge[end] }} className="font-medium">
              {labelOf(edge[end])}
            </PickLink>
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
          in graph{" "}
          <PickLink target={null} className="font-medium text-fg">
            {graph.label}
          </PickLink>
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
