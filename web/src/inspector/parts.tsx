/** Pieces shared by the inspector panels: header, sticky tabs, notes and value blocks. */
import { type ReactNode, useEffect, useRef, useState } from "react";
import { displayStatus } from "../data/selectors";
import { useViewer } from "../data/store";
import { Redacted } from "../ui/EmptyState";
import { Icon, type IconName } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { type TabItem, Tabs } from "../ui/overlays";
import { cx, statusMeta, type Tone, toneClass } from "../ui/tone";
import { useStickToBottom } from "../ui/useStickToBottom";
import type { Json, Span, SpanError } from "../wire";

/** How long a span has taken, measured to `now` while it is running. */
export function spanDuration(span: Span, now: number): number {
  return Math.max(0, (span.end_ms ?? now) - span.start_ms);
}

/** The status in words; a retried tool call says which attempt it was. */
export function statusLabel(span: Span): string {
  const status = displayStatus(span);
  if (status === "retry" && span.kind === "tool_call" && span.data.retry) {
    const { attempt, max_retries } = span.data.retry;
    return `Retry ${attempt} of ${max_retries}`;
  }
  return statusMeta[status].label;
}

export function InspectorHeader({
  tone,
  icon,
  kind,
  title,
  mono = false,
  status,
  meta,
  children,
}: {
  tone: Tone;
  icon: IconName;
  /** "Tool call", "Agent blueprint". */
  kind: string;
  title: string;
  /** Identifiers (tool and model names) are set in monospace. */
  mono?: boolean;
  status?: ReactNode;
  /** Right-aligned facts: a duration, a count. */
  meta?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header
      className={cx(toneClass[tone], "flex shrink-0 flex-col gap-1.5 border-line border-b p-3")}
    >
      <div className="flex min-h-5 items-center gap-2">
        <span className="tone-text flex items-center gap-1.5 font-semibold text-meta uppercase tracking-[0.06em]">
          <Icon name={icon} size={14} />
          {kind}
        </span>
        {status}
        {meta && <span className="ml-auto font-mono text-faint text-meta">{meta}</span>}
      </div>
      <h3 className={cx("m-0 break-all font-semibold text-title", mono && "font-mono text-em")}>
        {title}
      </h3>
      {children}
    </header>
  );
}

/** Tab choice per kind of thing, kept while the viewer is open: moving from one tool call to
 * the next keeps you on "Result". */
const remembered = new Map<string, string>();

/** Opens a tab ahead of time, e.g. "Output" when the output node is clicked. */
export function rememberTab(group: string, id: string): void {
  remembered.set(group, id);
}

/** For tests: forget every remembered tab. */
export function resetInspectorTabs(): void {
  remembered.clear();
}

export function StickyTabs({
  group,
  label,
  items,
}: {
  /** What the choice is remembered under: "tool_call", "agent_run". */
  group: string;
  label: string;
  items: TabItem[];
}) {
  const [, rerender] = useState(0);
  const wanted = remembered.get(group);
  const value = items.some((item) => item.id === wanted) ? wanted : items[0]?.id;
  const [only] = items;
  // One tab is no choice: its content is shown without a strip.
  if (items.length === 1 && only) {
    return (
      <section
        aria-label={label}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll area must take focus to be scrolled from the keyboard.
        tabIndex={0}
        className={cx(
          "relative min-h-0 flex-1 overflow-auto focus-visible:outline-offset-[-2px]",
          !only.flush && "p-3",
        )}
      >
        {only.content}
      </section>
    );
  }
  return (
    <Tabs
      label={label}
      className="min-h-0 flex-1"
      items={items}
      value={value}
      onValueChange={(id) => {
        remembered.set(group, id);
        rerender((n) => n + 1);
      }}
    />
  );
}

const tabPanelOf = (element: HTMLElement): HTMLElement | null =>
  element.closest<HTMLElement>('[role="tabpanel"]');

/**
 * Content that is still being written: a streaming response, the conversation of a run in
 * flight. While the clock moves, the tab panel around it stays on the newest text, unless the
 * reader has scrolled up. Key it by span, so each span starts out following.
 */
export function Tail({ growth, children }: { growth: number; children: ReactNode }) {
  const moving = useViewer((state) => state.playback.mode !== "paused");
  const ref = useStickToBottom<HTMLDivElement>(moving, growth, tabPanelOf);
  return (
    <div ref={ref} className="flex flex-col gap-4">
      {children}
    </div>
  );
}

/** A quiet line that says why there is nothing to show. */
export function Note({ icon = "info", children }: { icon?: IconName; children: ReactNode }) {
  return (
    <p className="m-0 flex items-start gap-1.5 text-muted text-ui">
      <Icon name={icon} size={14} className="mt-0.5 text-faint" />
      <span>{children}</span>
    </p>
  );
}

/** True when the session was captured with `include_content=False`. */
export function useContentOff(): boolean {
  return useViewer((state) => state.data.session?.include_content === false);
}

/**
 * Stands in for a value that is not there, and says why: the span is still running, content
 * capture is off, or there simply is none.
 */
export function Missing({
  what,
  running,
  none,
}: {
  /** "Result", "Arguments": names the value in the redaction note. */
  what: string;
  /** The span has not ended at the current replay position. */
  running: boolean;
  /** What to say when the value is genuinely absent. */
  none: string;
}) {
  const contentOff = useContentOff();
  if (running) return <Note icon="running">{what} arrives when this span ends.</Note>;
  if (contentOff) return <Redacted what={what} />;
  return <Note>{none}</Note>;
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <span className="font-semibold text-faint text-meta uppercase tracking-[0.06em]">
      {children}
    </span>
  );
}

const LONG_TEXT = 1600;

/** Plain text as written (prompts, replies), folded when very long. Never rendered as HTML. */
export function TextBlock({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > LONG_TEXT;
  return (
    <div className={cx("whitespace-pre-wrap break-words text-fg text-ui", className)}>
      {long && !open ? `${text.slice(0, LONG_TEXT)}...` : text}
      {long && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="ml-2 border-0 bg-transparent p-0 font-medium text-accent text-data"
        >
          {open ? "Show less" : `Show all ${text.length.toLocaleString("en-US")} characters`}
        </button>
      )}
    </div>
  );
}

/** A captured value: text as text, anything structured as a tree. */
export function Value({ value, label }: { value: Json; label: string }) {
  if (typeof value === "string") {
    return (
      <TextBlock text={value} className="rounded-control border border-line bg-bg px-2.5 py-2" />
    );
  }
  return <JsonTree value={value} label={label} openDepth={3} />;
}

export function ErrorBox({ error, retry }: { error: SpanError; retry: boolean }) {
  return (
    <section
      aria-label={retry ? "Retry reason" : "Error"}
      className={cx(
        retry ? "tone-retry" : "tone-error",
        "tone-soft flex flex-col gap-1 rounded-control border p-2.5",
      )}
    >
      <p className="m-0 flex items-center gap-1.5 font-medium font-mono text-data">
        <Icon name={retry ? "retry" : "error"} size={14} />
        <span className="break-all">{error.type}</span>
      </p>
      {error.message && (
        <p className="m-0 whitespace-pre-wrap break-words text-fg text-ui">{error.message}</p>
      )}
      {error.stacktrace && (
        <details>
          <summary className="text-data">Stack trace</summary>
          <pre className="m-0 mt-1 max-h-64 overflow-auto text-fg text-meta">
            {error.stacktrace}
          </pre>
        </details>
      )}
    </section>
  );
}

export type SectionItem = {
  /** Unique within the panel; `Sections` can be asked to bring it into view by this id. */
  id: string;
  title: string;
  /** A short fact beside the title: a count, a type name. */
  aside?: ReactNode;
  content: ReactNode;
};

/**
 * Short parts of one thing stacked under headings, for use inside something that scrolls (a
 * tab panel, or `Sections`). Each heading stays at the top while its part is being read, so a
 * long panel never loses the name of what is on screen.
 */
export function SectionList({ items }: { items: SectionItem[] }) {
  return (
    <>
      {items.map((item) => (
        // A heading is enough structure: a landmark per section would be noise.
        <div key={item.id} data-section={item.id}>
          <h4 className="sticky top-0 z-10 m-0 flex items-baseline justify-between gap-2 border-line border-b bg-surface-2 px-3 py-1">
            <Label>{item.title}</Label>
            {item.aside && <span className="text-faint text-meta">{item.aside}</span>}
          </h4>
          <div className="flex flex-col gap-3 px-3 pt-2.5 pb-4">{item.content}</div>
        </div>
      ))}
    </>
  );
}

/** Asks `Sections` to bring a section into view. A new object asks again for the same one. */
export type Reveal = { section: string };

/**
 * A whole panel as stacked sections in one scroll: the layout for content that is short and
 * read together (a blueprint, a tool). Tabs stay for content that is long or different in kind.
 */
export function Sections({
  label,
  items,
  reveal,
}: {
  /** Names the scroll area for assistive tech, e.g. "Agent blueprint". */
  label: string;
  items: SectionItem[];
  reveal?: Reveal | null;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const panel = ref.current;
    if (!reveal || !panel) return;
    const target = [...panel.querySelectorAll("[data-section]")].find(
      (element) => element.getAttribute("data-section") === reveal.section,
    );
    if (!target) return;
    // Not scrollIntoView: it scrolls every ancestor that can scroll, and the shell's fixed
    // layout can (its overflow is hidden, not absent). Only this panel may move.
    panel.scrollTop += target.getBoundingClientRect().top - panel.getBoundingClientRect().top;
  }, [reveal]);
  return (
    <section
      ref={ref}
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll area must take focus to be scrolled from the keyboard.
      tabIndex={0}
      // relative: what is absolutely positioned inside (every `sr-only` label is) must scroll
      // and be clipped with the panel, not be laid out against the page.
      className="relative min-h-0 flex-1 overflow-y-auto focus-visible:outline-offset-[-2px]"
    >
      <SectionList items={items} />
    </section>
  );
}

/** A titled block inside a tab. */
export function Block({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="m-0 flex items-baseline justify-between gap-2">
        <Label>{title}</Label>
        {aside && <span className="text-faint text-meta">{aside}</span>}
      </h4>
      {children}
    </section>
  );
}
