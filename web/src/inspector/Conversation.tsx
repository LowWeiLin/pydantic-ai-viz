/**
 * Messages as a conversation: one block per message, tool calls as cards with their result
 * folded in. Everything is rendered as text.
 */
import type { StreamPart } from "../data/selectors";
import { Redacted } from "../ui/EmptyState";
import { Icon, type IconName } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { cx, type Tone, toneClass } from "../ui/tone";
import type { Json, Message, Part } from "../wire";
import { Label, Note, TextBlock, useContentOff, Value } from "./parts";

type ToolCallPart = Extract<Part, { type: "tool_call" }>;
type ToolResultPart = Extract<Part, { type: "tool_result" }>;

export type ConversationPart =
  | Exclude<Part, ToolCallPart>
  | (ToolCallPart & { result: ToolResultPart | null });

export type ConversationMessage = { role: Message["role"]; parts: ConversationPart[] };

/**
 * Pairs each tool call with the result that answers it (same call id, later in the list) and
 * drops the result from where it stood. Messages left empty by that are removed. A result with
 * no matching call stays where it is.
 */
export function pairToolResults(messages: readonly Message[]): ConversationMessage[] {
  const taken = new Set<ToolResultPart>();
  const resultFor = (call: ToolCallPart, from: number): ToolResultPart | null => {
    if (call.id === null) return null;
    for (const message of messages.slice(from)) {
      for (const part of message.parts) {
        if (part.type === "tool_result" && part.id === call.id && !taken.has(part)) {
          taken.add(part);
          return part;
        }
      }
    }
    return null;
  };
  const paired = messages.map((message, index) => ({
    role: message.role,
    parts: message.parts.map(
      (part): ConversationPart =>
        part.type === "tool_call" ? { ...part, result: resultFor(part, index + 1) } : part,
    ),
    source: message.parts,
  }));
  return paired
    .map(({ role, parts, source }) => ({
      role,
      parts: parts.filter((_, i) => {
        const original = source[i];
        return !(original?.type === "tool_result" && taken.has(original));
      }),
    }))
    .filter((message) => message.parts.length > 0);
}

const roles: Record<Message["role"], { label: string; icon: IconName; tone: Tone }> = {
  system: { label: "System", icon: "instructions", tone: "neutral" },
  user: { label: "User", icon: "user", tone: "accent" },
  assistant: { label: "Assistant", icon: "model", tone: "model" },
  tool: { label: "Tool", icon: "tool", tone: "tool" },
};

function Captured({ value, what, label }: { value: Json | null; what: string; label: string }) {
  const contentOff = useContentOff();
  if (value === null) {
    return contentOff ? (
      <Redacted what={what} />
    ) : (
      <span className="text-faint text-ui">None.</span>
    );
  }
  if (typeof value === "string") return <TextBlock text={value} />;
  return <JsonTree value={value} label={label} openDepth={2} />;
}

export function ToolCallCard({
  name,
  callId,
  args,
  result,
}: {
  name: string;
  callId: string | null;
  args: Json | null;
  /** `undefined`: no result was recorded for this call (it may still be running). */
  result: Json | null | undefined;
}) {
  return (
    <div className="tone-tool overflow-hidden rounded-control border border-line bg-surface">
      <div className="flex h-7 items-center gap-2 border-line border-b bg-surface-2 px-2.5">
        <Icon name="tool" size={13} className="tone-text" />
        <code className="min-w-0 truncate font-medium text-fg">{name}</code>
        {callId && (
          <span className="ml-auto max-w-[45%] shrink-0 truncate font-mono text-faint text-meta">
            {callId}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1 px-2.5 py-2">
        <Label>Arguments</Label>
        <Captured value={args} what="Arguments" label={`arguments of ${name}`} />
      </div>
      {result !== undefined && (
        <div className="flex flex-col gap-1 border-line border-t px-2.5 py-2">
          <Label>Result</Label>
          <Captured value={result} what="Result" label={`result of ${name}`} />
        </div>
      )}
    </div>
  );
}

function PartView({ part }: { part: ConversationPart }) {
  const contentOff = useContentOff();
  switch (part.type) {
    case "text":
      if (part.content === null) {
        return contentOff ? <Redacted what="Text" /> : null;
      }
      return <TextBlock text={part.content} />;
    case "thinking":
      return (
        <div className="flex flex-col gap-0.5 border-line-strong border-l-2 pl-2.5">
          <Label>Thinking</Label>
          {part.content === null ? (
            contentOff ? (
              <Redacted what="Thinking" />
            ) : null
          ) : (
            <TextBlock text={part.content} className="text-muted! italic" />
          )}
        </div>
      );
    case "tool_call":
      return (
        <ToolCallCard
          name={part.name}
          callId={part.id}
          args={part.arguments}
          result={part.result ? part.result.result : undefined}
        />
      );
    case "tool_result":
      return (
        <div className="tone-tool overflow-hidden rounded-control border border-line bg-surface">
          <div className="flex h-7 items-center gap-2 border-line border-b bg-surface-2 px-2.5">
            <Icon name="tool" size={13} className="tone-text" />
            <code className="min-w-0 truncate font-medium text-fg">{part.name}</code>
            <span className="ml-auto text-faint text-meta">result</span>
          </div>
          <div className="px-2.5 py-2">
            <Captured value={part.result} what="Result" label={`result of ${part.name}`} />
          </div>
        </div>
      );
    default:
      return <Value value={part.raw} label="message part" />;
  }
}

export function MessageBubble({ message }: { message: ConversationMessage }) {
  const role = roles[message.role];
  return (
    <li className={cx(toneClass[role.tone], "flex flex-col gap-1.5")}>
      <span className="tone-text flex items-center gap-1.5 font-semibold text-meta uppercase tracking-[0.06em]">
        <Icon name={role.icon} size={13} />
        {role.label}
      </span>
      <div
        className={cx(
          "flex flex-col gap-2",
          message.role === "user" && "tone-soft rounded-control border px-2.5 py-2 text-fg!",
        )}
      >
        {message.parts.map((part, i) => (
          <PartView key={i} part={part} />
        ))}
      </div>
    </li>
  );
}

export function ConversationView({
  messages,
  label,
  empty = "No messages.",
}: {
  messages: readonly Message[];
  /** Names the list for assistive tech: "Conversation", "Input messages". */
  label: string;
  empty?: string;
}) {
  const items = pairToolResults(messages);
  if (items.length === 0) return <Note>{empty}</Note>;
  return (
    <ol aria-label={label} className="m-0 flex list-none flex-col gap-4 p-0">
      {items.map((message, i) => (
        <MessageBubble key={i} message={message} />
      ))}
    </ol>
  );
}

/** The response of a model request while it is still streaming in. */
export function StreamingText({ parts }: { parts: readonly StreamPart[] }) {
  return (
    <section className="tone-model flex flex-col gap-3" aria-label="Response so far">
      <span className="tone-text flex items-center gap-1.5 font-semibold text-meta uppercase tracking-[0.06em]">
        <Icon name="model" size={13} />
        Assistant
        <span className="font-normal text-faint normal-case tracking-normal">streaming</span>
      </span>
      {parts.map((part) => (
        <div key={part.index} className="flex flex-col gap-1">
          {part.kind !== "text" && (
            <Label>
              {part.kind === "tool_call" ? `Tool call: ${part.tool_name ?? "unknown"}` : "Thinking"}
            </Label>
          )}
          <p
            className={cx(
              "m-0 whitespace-pre-wrap break-words text-ui",
              part.kind === "tool_call" && "font-mono text-data",
              part.kind === "thinking" && "text-muted italic",
            )}
          >
            {part.text}
            <span
              aria-hidden="true"
              className="ml-0.5 inline-block h-3 w-1.5 bg-accent align-middle"
            />
          </p>
        </div>
      ))}
    </section>
  );
}
