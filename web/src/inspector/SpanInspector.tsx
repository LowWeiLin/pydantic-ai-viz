/**
 * The inspector for a selected span. A tab is kept for what is long (a message list) or
 * different in kind (the raw attributes); what is short and read together is stacked as
 * sections inside one tab:
 *
 *   agent run      Conversation | Output (Output · Usage) | Raw
 *   model request  Response (Request · Response) | Input (Instructions · Messages) | Raw
 *   tool call      Details (Error or Retry · Arguments · Result) | Raw
 *   graph run/node Details (Inputs · Output) | Raw
 *   anything else  the raw attributes, with no tab strip
 *
 * The span is the projection at the replay position: fields that only exist once a span has
 * ended are null before that, and each part says so instead of showing a blank.
 */
import { conversationSoFar, displayStatus, type RunView, type StreamPart } from "../data/selectors";
import { Facts, orDash } from "../screens/parts";
import { formatCount, formatDuration } from "../shell/format";
import { StatusDot } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { JsonTree } from "../ui/JsonTree";
import type { TabItem } from "../ui/overlays";
import { kindMeta } from "../ui/tone";
import type {
  AgentRunSpan,
  GraphNodeSpan,
  GraphRunSpan,
  ModelRequestSpan,
  Span,
  ToolCallSpan,
  Usage,
} from "../wire";
import { ConversationView, StreamingText } from "./Conversation";
import {
  Block,
  ErrorBox,
  InspectorHeader,
  Missing,
  Note,
  type SectionItem,
  SectionList,
  StickyTabs,
  spanDuration,
  statusLabel,
  Tail,
  TextBlock,
  Value,
} from "./parts";

type Ctx = { view: RunView; now: number };

/** A tab whose content is short parts read together, stacked under headings. */
const sectionsTab = (id: string, label: string, items: SectionItem[]): TabItem => ({
  id,
  label,
  flush: true,
  content: <SectionList items={items} />,
});

// ---------- shared tabs ----------

function Id({ value }: { value: string | null }) {
  if (value === null) return orDash(null);
  return (
    <span className="inline-flex max-w-full items-center gap-1">
      <code className="min-w-0 truncate text-fg">{value}</code>
      <CopyButton text={value} label={`Copy ${value}`} compact />
    </span>
  );
}

export function RawAttributes({ span, view }: { span: Span; view: RunView }) {
  const hasRaw = Object.keys(span.attributes).length > 0;
  return (
    <div className="flex flex-col gap-4">
      <Block title="Identity">
        <Facts
          items={[
            ["Span", <Id key="span" value={span.span_id} />],
            ["Parent", <Id key="parent" value={span.parent_id} />],
            ["Trace", <Id key="trace" value={span.trace_id} />],
            ["Agent run", <Id key="run" value={span.run_id} />],
            ["Started", `+${formatDuration(Math.max(0, span.start_ms - view.startMs))}`],
          ]}
        />
      </Block>
      <Block title="Other attributes">
        {hasRaw ? (
          <JsonTree value={span.attributes} label="raw attributes" />
        ) : (
          <Note>
            {span.status === "running"
              ? "Raw attributes arrive when the span ends."
              : "This span has no other attributes."}
          </Note>
        )}
      </Block>
    </div>
  );
}

const th = "border-line border-b px-2 py-1.5 text-left font-medium text-faint text-meta";
const num = "px-2 py-1.5 text-right font-mono";

export type UsageRow = { label: string; usage: Usage | null; durationMs: number | null };

/** Token counts per row with a total. Rows without usage (still running) show a dash. */
export function UsageTable({ rows, caption }: { rows: UsageRow[]; caption: string }) {
  const total = rows.reduce(
    (sum, row) => ({
      input_tokens: sum.input_tokens + (row.usage?.input_tokens ?? 0),
      output_tokens: sum.output_tokens + (row.usage?.output_tokens ?? 0),
    }),
    { input_tokens: 0, output_tokens: 0 },
  );
  const timed = rows.some((row) => row.durationMs !== null);
  const time = rows.reduce((sum, row) => sum + (row.durationMs ?? 0), 0);
  const cell = (value: number | undefined) => (value === undefined ? "-" : formatCount(value));
  return (
    <div className="overflow-x-auto rounded-control border border-line">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-2">
          <tr>
            <th scope="col" className={th}>
              Request
            </th>
            <th scope="col" className={`${th} text-right!`}>
              Input
            </th>
            <th scope="col" className={`${th} text-right!`}>
              Output
            </th>
            <th scope="col" className={`${th} text-right!`}>
              Time
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-line border-b">
              <th scope="row" className="px-2 py-1.5 text-left font-normal text-fg">
                {row.label}
              </th>
              <td className={num}>{cell(row.usage?.input_tokens)}</td>
              <td className={num}>{cell(row.usage?.output_tokens)}</td>
              <td className={`${num} text-muted`}>
                {row.durationMs === null ? "-" : formatDuration(row.durationMs)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-medium">
            <th scope="row" className="px-2 py-1.5 text-left text-fg">
              Total
            </th>
            <td className={num}>{formatCount(total.input_tokens)}</td>
            <td className={num}>{formatCount(total.output_tokens)}</td>
            <td className={`${num} text-muted`}>{timed ? formatDuration(time) : "-"}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// ---------- per kind ----------

/** How much has been streamed so far; it changes with every delta. */
const streamLength = (parts: readonly StreamPart[] | null | undefined): number =>
  (parts ?? []).reduce((length, part) => length + part.text.length, 0);

function agentRunTabs(span: AgentRunSpan, { view, now }: Ctx): TabItem[] {
  const running = span.status === "running";
  const { data } = span;
  const requests = (view.children[span.span_id] ?? []).flatMap((id) => {
    const child = view.spans[id];
    return child?.kind === "model_request" ? [child] : [];
  });
  // Until the run ends, its requests tell the story so far.
  const soFar = data.messages === null && running ? conversationSoFar(view, span.span_id) : null;
  const nothingYet = soFar === null || (soFar.messages === null && soFar.stream === null);
  return [
    {
      id: "conversation",
      label: "Conversation",
      content: (
        <div className="flex flex-col gap-4">
          {data.instructions !== null && (
            <details>
              <summary className="font-medium text-muted text-ui">Instructions</summary>
              <TextBlock
                text={data.instructions}
                className="mt-1.5 rounded-control border border-line bg-bg px-2.5 py-2"
              />
            </details>
          )}
          {data.messages !== null ? (
            <ConversationView messages={data.messages} label="Conversation" />
          ) : nothingYet ? (
            <Missing
              what="The conversation"
              running={running}
              none="No messages were recorded for this run."
            />
          ) : (
            <Tail
              key={span.span_id}
              growth={(soFar?.messages?.length ?? 0) + streamLength(soFar?.stream)}
            >
              {soFar?.messages && (
                <ConversationView messages={soFar.messages} label="Conversation so far" />
              )}
              {soFar?.stream && <StreamingText parts={soFar.stream} />}
            </Tail>
          )}
        </div>
      ),
    },
    sectionsTab("output", "Output", [
      {
        id: "output",
        title: "Output",
        content:
          data.output === null ? (
            <Missing
              what="The output"
              running={running}
              none={
                span.status === "error"
                  ? "The run failed before it produced an output."
                  : "This run has no output."
              }
            />
          ) : (
            <Value value={data.output} label="output" />
          ),
      },
      {
        id: "usage",
        title: "Usage",
        content: (
          <>
            {requests.length === 0 ? (
              <Note>No model requests yet.</Note>
            ) : (
              <UsageTable
                caption={`Token usage of ${span.name} by model request`}
                rows={requests.map((request, i) => ({
                  label: `${i + 1}. ${request.data.model_name}`,
                  usage: request.data.usage,
                  durationMs: spanDuration(request, now),
                }))}
              />
            )}
            {data.usage && (
              <p className="m-0 text-muted text-ui">
                The agent reported{" "}
                <span className="font-mono text-fg">
                  {formatCount(data.usage.input_tokens)} in /{" "}
                  {formatCount(data.usage.output_tokens)} out
                </span>
                . That figure includes any sub-agent that shares its usage, so run totals are summed
                from model requests instead.
              </p>
            )}
          </>
        ),
      },
    ]),
  ];
}

function modelRequestTabs(
  span: ModelRequestSpan,
  stream: readonly StreamPart[] | undefined,
): TabItem[] {
  const running = span.status === "running";
  const { data } = span;
  const settings = Object.keys(data.settings).length > 0 ? data.settings : null;
  // Worth a line only when the provider answered with another model than the one asked for.
  const answeredBy =
    data.response_model !== null && data.response_model !== data.model_name
      ? data.response_model
      : null;
  return [
    sectionsTab("response", "Response", [
      {
        id: "request",
        title: "Request",
        content: (
          <>
            {/* The model and the duration are in the header; what is here is what it adds. */}
            <Facts
              items={[
                ["Provider", orDash(data.provider)],
                ...(answeredBy ? [["Answered by", answeredBy] as [string, string]] : []),
                [
                  "Tokens",
                  data.usage
                    ? `${formatCount(data.usage.input_tokens)} in / ${formatCount(data.usage.output_tokens)} out`
                    : orDash(null),
                ],
              ]}
            />
            {running && <Note icon="running">Token counts arrive when the request ends.</Note>}
            {settings && (
              <details>
                <summary className="font-medium text-muted text-ui">Request settings</summary>
                <JsonTree value={settings} label="request settings" className="mt-1.5" />
              </details>
            )}
          </>
        ),
      },
      {
        // Last, so that a response still being streamed grows at the bottom of the panel.
        id: "response",
        title: "Response",
        content:
          data.output_messages !== null ? (
            <ConversationView
              messages={data.output_messages}
              label="Response"
              empty="The model returned no content."
            />
          ) : stream ? (
            <Tail key={span.span_id} growth={streamLength(stream)}>
              <StreamingText parts={stream} />
            </Tail>
          ) : (
            <Missing
              what="The response"
              running={running}
              none={
                span.status === "error" ? "The request failed before a response arrived." : "None."
              }
            />
          ),
      },
    ]),
    sectionsTab("input", "Input", [
      {
        id: "instructions",
        title: "Instructions",
        content:
          data.instructions === null ? (
            <Missing
              what="The instructions"
              running={running}
              none="This request carried no instructions."
            />
          ) : (
            <TextBlock text={data.instructions} />
          ),
      },
      {
        id: "messages",
        title: "Input messages",
        content:
          data.input_messages === null ? (
            <Missing what="The input" running={running} none="No input messages were recorded." />
          ) : (
            <ConversationView messages={data.input_messages} label="Input messages" />
          ),
      },
    ]),
  ];
}

function toolCallTabs(span: ToolCallSpan): TabItem[] {
  const running = span.status === "running";
  const { data } = span;
  const failed = span.status === "error";
  const problem: SectionItem | null =
    span.error || data.retry
      ? {
          // First: when a call went wrong, that is what the reader came for.
          id: "error",
          title: data.retry ? "Retry" : "Error",
          content: (
            <>
              {/* Which attempt it was is the status in the header ("Retry 1 of 2"). */}
              {data.retry && <Facts items={[["Cause", data.retry.cause]]} />}
              {span.error && <ErrorBox error={span.error} retry={data.retry !== null} />}
              {data.retry && (
                <Note>
                  A retry is not a failure: the tool asked the model to call it again, and the run
                  went on.
                </Note>
              )}
            </>
          ),
        }
      : null;
  return [
    sectionsTab("details", "Details", [
      ...(problem ? [problem] : []),
      {
        id: "arguments",
        title: "Arguments",
        content:
          data.arguments === null ? (
            <Missing what="Arguments" running={false} none="This call has no arguments." />
          ) : (
            <Value value={data.arguments} label={`arguments of ${data.tool_name}`} />
          ),
      },
      {
        id: "result",
        title: "Result",
        content:
          data.result === null ? (
            <Missing
              what="The result"
              running={running}
              none={failed ? "The call failed without a result." : "The tool returned nothing."}
            />
          ) : (
            <>
              {data.retry && (
                <Note icon="retry">
                  This text went back to the model as the reason to try again.
                </Note>
              )}
              <Value value={data.result} label={`result of ${data.tool_name}`} />
            </>
          ),
      },
    ]),
  ];
}

function graphTabs(span: GraphRunSpan | GraphNodeSpan): TabItem[] {
  const running = span.status === "running";
  return [
    sectionsTab("details", "Details", [
      {
        id: "inputs",
        title: "Inputs",
        content:
          span.data.inputs === null ? (
            <Missing what="Inputs" running={false} none="No inputs." />
          ) : (
            <Value value={span.data.inputs} label="inputs" />
          ),
      },
      {
        id: "output",
        title: "Output",
        content:
          span.kind === "graph_node" ? (
            <Note>
              Outputs are recorded for the whole graph run, not per node. A node's result shows as
              the inputs of the node that ran next.
            </Note>
          ) : span.data.output === null ? (
            <Missing
              what="The output"
              running={running}
              none={span.status === "error" ? "The graph failed before it finished." : "No output."}
            />
          ) : (
            <Value value={span.data.output} label="output" />
          ),
      },
    ]),
  ];
}

function kindTabs(span: Span, ctx: Ctx, stream: readonly StreamPart[] | undefined): TabItem[] {
  switch (span.kind) {
    case "agent_run":
      return agentRunTabs(span, ctx);
    case "model_request":
      return modelRequestTabs(span, stream);
    case "tool_call":
      return toolCallTabs(span);
    case "graph_run":
    case "graph_node":
      return graphTabs(span);
    default:
      return [];
  }
}

export function SpanInspector({
  span,
  view,
  now,
}: {
  span: Span;
  view: RunView;
  /** The time running spans are measured against. */
  now: number;
}) {
  const kind = kindMeta[span.kind];
  const status = displayStatus(span);
  const tabs = [
    ...kindTabs(span, { view, now }, view.streams[span.span_id]),
    { id: "raw", label: "Raw", content: <RawAttributes span={span} view={view} /> },
  ];
  // A tool call's error is the first section of its details; every other kind shows it here.
  const showError = span.error !== null && span.kind !== "tool_call";
  const owner = span.agent_name !== null && span.kind !== "agent_run" ? span.agent_name : null;
  return (
    <>
      <InspectorHeader
        tone={kind.tone}
        icon={kind.icon}
        kind={kind.label}
        title={span.name}
        mono
        status={<StatusDot status={status} showLabel label={statusLabel(span)} />}
        meta={formatDuration(spanDuration(span, now))}
      >
        {owner && (
          <p className="m-0 break-words text-muted text-ui">
            in agent <span className="font-medium text-fg">{owner}</span>
          </p>
        )}
        {/* The header does not scroll, so an error in it is capped: a message of thousands of
            characters would otherwise push the tabs out of the panel. */}
        {showError && span.error && (
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll area must take focus to be scrolled from the keyboard.
          <div tabIndex={0} className="max-h-40 overflow-y-auto rounded-control">
            <ErrorBox error={span.error} retry={false} />
          </div>
        )}
      </InspectorHeader>
      <StickyTabs group={span.kind} label="Span details" items={tabs} />
    </>
  );
}
