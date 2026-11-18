/**
 * The inspector for a selected span. Tabs depend on the span kind:
 *
 *   agent run      Conversation · Output · Usage · Raw
 *   model request  Response · Input messages · Instructions · Usage · Raw
 *   tool call      Arguments · Result · Error / Retry · Raw
 *   graph run/node Inputs · Output · Raw
 *   anything else  Raw
 *
 * The span is the projection at the replay position: fields that only exist once a span has
 * ended are null before that, and each tab says so instead of showing a blank.
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
  StickyTabs,
  spanDuration,
  statusLabel,
  TextBlock,
  Value,
} from "./parts";

type Ctx = { view: RunView; now: number };

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
            <>
              {soFar?.messages && (
                <ConversationView messages={soFar.messages} label="Conversation so far" />
              )}
              {soFar?.stream && <StreamingText parts={soFar.stream} />}
            </>
          )}
        </div>
      ),
    },
    {
      id: "output",
      label: "Output",
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
      label: "Usage",
      content: (
        <div className="flex flex-col gap-3">
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
                {formatCount(data.usage.input_tokens)} in / {formatCount(data.usage.output_tokens)}{" "}
                out
              </span>
              . That figure includes any sub-agent that shares its usage, so run totals are summed
              from model requests instead.
            </p>
          )}
        </div>
      ),
    },
  ];
}

function modelRequestTabs(
  span: ModelRequestSpan,
  { now }: Ctx,
  stream: readonly StreamPart[] | undefined,
): TabItem[] {
  const running = span.status === "running";
  const { data } = span;
  const settings = Object.keys(data.settings).length > 0 ? data.settings : null;
  return [
    {
      id: "response",
      label: "Response",
      content:
        data.output_messages !== null ? (
          <ConversationView
            messages={data.output_messages}
            label="Response"
            empty="The model returned no content."
          />
        ) : stream ? (
          <StreamingText parts={stream} />
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
    {
      id: "input",
      label: "Input messages",
      content:
        data.input_messages === null ? (
          <Missing what="The input" running={running} none="No input messages were recorded." />
        ) : (
          <ConversationView messages={data.input_messages} label="Input messages" />
        ),
    },
    {
      id: "instructions",
      label: "Instructions",
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
      id: "usage",
      label: "Usage",
      content: (
        <div className="flex flex-col gap-4">
          <Facts
            items={[
              ["Input tokens", data.usage ? formatCount(data.usage.input_tokens) : orDash(null)],
              ["Output tokens", data.usage ? formatCount(data.usage.output_tokens) : orDash(null)],
              ["Duration", formatDuration(spanDuration(span, now))],
              ["Model", data.model_name],
              ["Provider", orDash(data.provider)],
              ["Response model", orDash(data.response_model)],
            ]}
          />
          {running && <Note icon="running">Token counts arrive when the request ends.</Note>}
          {settings && (
            <Block title="Request settings">
              <JsonTree value={settings} label="request settings" />
            </Block>
          )}
        </div>
      ),
    },
  ];
}

function toolCallTabs(span: ToolCallSpan): TabItem[] {
  const running = span.status === "running";
  const { data } = span;
  const failed = span.status === "error";
  return [
    {
      id: "arguments",
      label: "Arguments",
      content:
        data.arguments === null ? (
          <Missing what="Arguments" running={false} none="This call has no arguments." />
        ) : (
          <Value value={data.arguments} label={`arguments of ${data.tool_name}`} />
        ),
    },
    {
      id: "result",
      label: "Result",
      content:
        data.result === null ? (
          <Missing
            what="The result"
            running={running}
            none={failed ? "The call failed without a result." : "The tool returned nothing."}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {data.retry && (
              <Note icon="retry">This text went back to the model as the reason to try again.</Note>
            )}
            <Value value={data.result} label={`result of ${data.tool_name}`} />
          </div>
        ),
    },
    {
      id: "error",
      label: "Error / Retry",
      content: (
        <div className="flex flex-col gap-3">
          {data.retry && (
            <Facts
              items={[
                ["Attempt", `${data.retry.attempt} of ${data.retry.max_retries} retries`],
                ["Cause", data.retry.cause],
              ]}
            />
          )}
          {span.error ? (
            <ErrorBox error={span.error} retry={data.retry !== null} />
          ) : (
            <Note icon={running ? "running" : "check"}>
              {running ? "Still running. No error so far." : "This call did not fail."}
            </Note>
          )}
          {data.retry && (
            <Note>
              A retry is not a failure: the tool asked the model to call it again, and the run went
              on.
            </Note>
          )}
        </div>
      ),
    },
  ];
}

function graphTabs(span: GraphRunSpan | GraphNodeSpan): TabItem[] {
  const running = span.status === "running";
  return [
    {
      id: "inputs",
      label: "Inputs",
      content:
        span.data.inputs === null ? (
          <Missing what="Inputs" running={false} none="No inputs." />
        ) : (
          <Value value={span.data.inputs} label="inputs" />
        ),
    },
    {
      id: "output",
      label: "Output",
      content:
        span.kind === "graph_node" ? (
          <Note>
            Outputs are recorded for the whole graph run, not per node. A node's result shows as the
            inputs of the node that ran next.
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
  ];
}

function kindTabs(span: Span, ctx: Ctx, stream: readonly StreamPart[] | undefined): TabItem[] {
  switch (span.kind) {
    case "agent_run":
      return agentRunTabs(span, ctx);
    case "model_request":
      return modelRequestTabs(span, ctx, stream);
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
  // A tool call's error lives in its own tab; every other kind shows it up front.
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
        {span.kind === "tool_call" && span.error && (
          // Three lines here; the whole message is in the Error / Retry tab.
          <p className="m-0 line-clamp-3 whitespace-pre-wrap break-words text-ui">
            <span className="font-medium font-mono text-data">{span.error.type}</span>
            {span.error.message && <span className="text-fg">: {span.error.message}</span>}
          </p>
        )}
      </InspectorHeader>
      <StickyTabs group={span.kind} label="Span details" items={tabs} />
    </>
  );
}
