/**
 * The run screen. It already replays: the clock, the projection and selection are real.
 *
 * Stage seams, by Workspace slot:
 * - `main`: `SpanTree` (a plain span tree) gives way to the canvas with the run overlay;
 * - `dock`: the tree moves there as `timeline/Timeline` with a waterfall and `PlaybackBar`
 *   (the `PlaybackControls` here are the minimal version of that bar);
 * - `inspector`: `SpanDetail` gives way to `inspector/Inspector` with per-kind tabs.
 * Selection is the `?span=` part of the route; keep it there.
 */
import { type KeyboardEvent, useEffect, useMemo, useRef } from "react";
import {
  type Bounds,
  changeSpeed,
  goLive,
  type Playback,
  playFromStart,
  progress,
  seek,
  step,
  togglePlay,
} from "../data/playback";
import {
  displayStatus,
  type RunView,
  runBoundaries,
  runBounds,
  runTotals,
  runView,
  type StreamPart,
} from "../data/selectors";
import { useViewer } from "../data/store";
import { useAutoReplay, usePlaybackDriver, usePlaybackTime } from "../data/usePlayback";
import { formatCount, formatDuration, plural } from "../shell/format";
import { navigate } from "../shell/router";
import { useShortcuts } from "../shell/shortcuts";
import { NotFound } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip, StatusDot } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Redacted } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { JsonTree } from "../ui/JsonTree";
import { Tabs } from "../ui/overlays";
import { cx, kindMeta, statusMeta, toneClass } from "../ui/tone";
import type { Span } from "../wire";
import { ScreenHeader } from "./parts";

/** How long a span has taken at the view's time. */
function spanDuration(span: Span, now: number): number {
  return Math.max(0, (span.end_ms ?? now) - span.start_ms);
}

function statusLabel(span: Span): string {
  const status = displayStatus(span);
  if (status === "retry" && span.kind === "tool_call" && span.data.retry) {
    const { attempt, max_retries } = span.data.retry;
    return `Retry ${attempt} of ${max_retries}`;
  }
  return statusMeta[status].label;
}

export function PlaybackControls({ playback, bounds }: { playback: Playback; bounds: Bounds }) {
  const update = useViewer((state) => state.updatePlayback);
  const atEnd = playback.mode !== "playing" && playback.t >= bounds.end;
  const elapsed = Math.min(playback.t, bounds.end) - bounds.start;
  const total = bounds.end - bounds.start;
  return (
    <div className="flex shrink-0 flex-col items-end gap-1.5">
      <div className="flex items-center gap-1.5">
        <Button
          icon={playback.mode === "playing" ? "pause" : "play"}
          onClick={() => update((p) => togglePlay(p, bounds))}
        >
          {playback.mode === "playing" ? "Pause" : atEnd ? "Replay" : "Play"}
        </Button>
        <Button
          variant="ghost"
          icon="skipEnd"
          disabled={atEnd}
          onClick={() => update((p) => seek(p, bounds.end, bounds))}
        >
          Skip to end
        </Button>
      </div>
      <div className="flex items-center gap-2 font-mono text-faint text-meta">
        <span>
          {formatDuration(Math.max(0, elapsed))} / {formatDuration(total)}
        </span>
        <span>{playback.speed}x</span>
      </div>
      <div
        role="progressbar"
        aria-label="Replay position"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress(playback, bounds) * 100)}
        className="h-1 w-44 overflow-hidden rounded-full bg-surface-2"
      >
        <div
          className="h-full origin-left bg-accent"
          style={{ transform: `scaleX(${progress(playback, bounds)})` }}
        />
      </div>
    </div>
  );
}

export function SpanTree({
  view,
  selectedId,
  now,
  onSelect,
}: {
  view: RunView;
  selectedId: string | null;
  /** The time running spans are measured against. */
  now: number;
  onSelect: (spanId: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  // One tab stop: the selected row, or the first.
  const tabStop = selectedId && view.spans[selectedId] ? selectedId : view.order[0];

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>, id: string): void => {
    const index = view.order.indexOf(id);
    const target =
      event.key === "ArrowDown"
        ? view.order[index + 1]
        : event.key === "ArrowUp"
          ? view.order[index - 1]
          : event.key === "Home"
            ? view.order[0]
            : event.key === "End"
              ? view.order.at(-1)
              : undefined;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    } else if (target !== undefined) {
      event.preventDefault();
      onSelect(target);
      container.current?.querySelector<HTMLElement>(`[data-span="${target}"]`)?.focus();
    }
  };

  return (
    <div
      ref={container}
      role="tree"
      aria-label="Spans of this run"
      className="overflow-hidden rounded-card border border-line bg-surface"
    >
      {view.order.map((id) => {
        const span = view.spans[id];
        if (!span) return null;
        const kind = kindMeta[span.kind];
        const status = displayStatus(span);
        const selected = id === selectedId;
        return (
          <div
            key={id}
            role="treeitem"
            data-span={id}
            aria-level={(view.depth[id] ?? 0) + 1}
            aria-selected={selected}
            tabIndex={id === tabStop ? 0 : -1}
            onClick={() => onSelect(id)}
            onKeyDown={(event) => onKeyDown(event, id)}
            className={cx(
              toneClass[kind.tone],
              "flex h-8 animate-fade-in cursor-pointer items-center gap-2 border-line border-b pr-3 last:border-b-0",
              "transition-colors duration-150 ease-out hover:bg-surface-2 focus-visible:outline-offset-[-2px]",
              selected && "tone-rail bg-surface-2",
            )}
            style={{ paddingLeft: 12 + (view.depth[id] ?? 0) * 16 }}
          >
            <Icon name={kind.icon} size={14} className="tone-text" label={kind.label} />
            <span className="min-w-0 flex-1 truncate font-mono text-data">{span.name}</span>
            {span.agent_name && span.kind !== "agent_run" && (
              <span className="truncate text-faint text-meta max-sm:hidden">{span.agent_name}</span>
            )}
            <span className="w-16 shrink-0 text-right font-mono text-faint text-meta">
              {formatDuration(spanDuration(span, now))}
            </span>
            <StatusDot
              status={status}
              showLabel
              label={statusLabel(span)}
              className="w-24 shrink-0"
            />
          </div>
        );
      })}
    </div>
  );
}

function StreamingParts({ parts }: { parts: readonly StreamPart[] }) {
  return (
    <section className="flex flex-col gap-2" aria-label="Response so far">
      {parts.map((part) => (
        <div key={part.index} className="flex flex-col gap-1">
          <span className="font-semibold text-faint text-meta uppercase tracking-[0.06em]">
            {part.kind === "tool_call"
              ? `Tool call: ${part.tool_name ?? "unknown"}`
              : part.kind === "thinking"
                ? "Thinking"
                : "Text"}
          </span>
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

export function SpanDetail({
  span,
  stream,
  now,
}: {
  span: Span;
  stream: readonly StreamPart[] | undefined;
  now: number;
}) {
  const contentOff = useViewer((state) => state.data.session?.include_content === false);
  const kind = kindMeta[span.kind];
  const hasRaw = Object.keys(span.attributes).length > 0;

  const details = (
    <div className="flex flex-col gap-4">
      {span.error && (
        <div
          className={cx(
            displayStatus(span) === "retry" ? "tone-retry" : "tone-error",
            "tone-soft flex flex-col gap-1 rounded-control border p-2.5",
          )}
        >
          <p className="m-0 font-medium font-mono text-data">{span.error.type}</p>
          {span.error.message && (
            <p className="m-0 whitespace-pre-wrap text-fg text-ui">{span.error.message}</p>
          )}
          {span.error.stacktrace && (
            <details>
              <summary className="text-data">Stack trace</summary>
              <pre className="m-0 mt-1 overflow-x-auto text-fg text-meta">
                {span.error.stacktrace}
              </pre>
            </details>
          )}
        </div>
      )}
      {stream && <StreamingParts parts={stream} />}
      {contentOff && <Redacted />}
      {span.data !== null && <JsonTree value={span.data} label="span data" openDepth={2} />}
    </div>
  );

  return (
    <>
      <header
        className={cx(toneClass[kind.tone], "flex flex-col gap-1.5 border-line border-b p-3")}
      >
        <div className="flex items-center gap-2">
          <Chip tone={kind.tone} icon={kind.icon}>
            {kind.label}
          </Chip>
          <StatusDot status={displayStatus(span)} showLabel label={statusLabel(span)} />
          <span className="ml-auto font-mono text-faint text-meta">
            {formatDuration(spanDuration(span, now))}
          </span>
        </div>
        <h3 className="m-0 break-all font-mono font-semibold text-em">{span.name}</h3>
      </header>
      <Tabs
        key={span.span_id}
        label="Span details"
        className="flex-1"
        items={[
          { id: "details", label: "Details", content: details },
          {
            id: "raw",
            label: "Raw",
            content: hasRaw ? (
              <JsonTree value={span.attributes} label="raw attributes" />
            ) : (
              <p className="m-0 text-muted text-ui">
                {span.status === "running"
                  ? "Raw attributes arrive when the span ends."
                  : "This span has no other attributes."}
              </p>
            ),
          },
        ]}
      />
    </>
  );
}

export function RunScreen({ traceId, spanId }: { traceId: string; spanId: string | null }) {
  const data = useViewer((state) => state.data);
  const playback = useViewer((state) => state.playback);
  const update = useViewer((state) => state.updatePlayback);
  const live = useViewer((state) => state.source.kind === "live");
  const t = usePlaybackTime();

  const bounds = useMemo(() => runBounds(data, traceId), [data, traceId]);
  usePlaybackDriver(bounds);
  useAutoReplay(traceId, bounds);

  const view = useMemo(() => runView(data, traceId, t), [data, traceId, t]);
  const totals = useMemo(() => runTotals(data, traceId, t), [data, traceId, t]);
  const now = Math.min(t, Math.max(data.lastTs, bounds?.end ?? 0));

  const select = (id: string | null): void =>
    navigate({ name: "run", traceId, spanId: id }, { replace: true });

  const move = (direction: 1 | -1): void => {
    if (!view || view.order.length === 0) return;
    const index = spanId ? view.order.indexOf(spanId) : -1;
    const next = view.order[Math.min(view.order.length - 1, Math.max(0, index + direction))];
    if (next) select(next);
  };

  useShortcuts(
    {
      nextSpan: () => move(1),
      prevSpan: () => move(-1),
      clearSelection: () => select(null),
      togglePlay: () => bounds && update((p) => togglePlay(p, bounds)),
      slower: () => update((p) => changeSpeed(p, -1)),
      faster: () => update((p) => changeSpeed(p, 1)),
      stepBack: () => bounds && update((p) => step(p, runBoundaries(data, traceId), -1, bounds)),
      stepForward: () => bounds && update((p) => step(p, runBoundaries(data, traceId), 1, bounds)),
      goLive: () => bounds && update((p) => (live ? goLive(p) : seek(p, bounds.end, bounds))),
    },
    bounds !== null,
  );

  // A live run that was paused for inspection and then left: re-pin when the screen closes.
  useEffect(() => () => useViewer.getState().updatePlayback((p) => (live ? goLive(p) : p)), [live]);

  if (!view || !bounds) {
    return (
      <NotFound
        what="Run"
        detail="This session has no run with that id. It may have been evicted from a long session, or the link may come from another session."
      />
    );
  }

  const root = view.rootId ? view.spans[view.rootId] : undefined;
  const fullRoot = data.spans[data.traces[traceId]?.[0] ?? ""];
  const kind = kindMeta[root?.kind ?? fullRoot?.kind ?? "other"];
  const selected = spanId ? view.spans[spanId] : undefined;
  const tokens = totals.usage.input_tokens + totals.usage.output_tokens;

  return (
    <Workspace
      inspectorLabel="Span details"
      main={
        <div className="mx-auto flex max-w-4xl flex-col gap-5 px-6 py-6">
          <ScreenHeader
            icon={kind.icon}
            tone={kind.tone}
            kind={kind.label}
            title={root?.name ?? fullRoot?.name ?? "Run"}
            chips={
              <>
                <StatusDot status={view.status} showLabel />
                <Chip mono>{formatDuration(totals.durationMs)}</Chip>
                <Chip tone="model" icon="model">
                  {plural(totals.requests, "request")}
                </Chip>
                <Chip tone="tool" icon="tool">
                  {plural(totals.toolCalls, "tool call")}
                </Chip>
                {tokens > 0 && (
                  <Chip mono>
                    {formatCount(totals.usage.input_tokens)} in /{" "}
                    {formatCount(totals.usage.output_tokens)} out
                  </Chip>
                )}
                {totals.retries > 0 && (
                  <Chip tone="retry" icon="retry">
                    {plural(totals.retries, "retry", "retries")}
                  </Chip>
                )}
                {totals.errors > 0 && (
                  <Chip tone="error" icon="error">
                    {plural(totals.errors, "error")}
                  </Chip>
                )}
              </>
            }
            aside={
              live && playback.mode === "live" ? undefined : (
                <PlaybackControls playback={playback} bounds={bounds} />
              )
            }
          />
          {view.order.length === 0 ? (
            <p className="m-0 text-muted text-ui">The run has not started at this point in time.</p>
          ) : (
            <SpanTree view={view} selectedId={spanId} now={now} onSelect={select} />
          )}
          {!live && view.status === "running" && playback.mode !== "playing" && t >= bounds.end && (
            <p className="m-0 flex items-center gap-1.5 text-muted text-ui">
              <Icon name="info" size={14} />
              The recording ends here, before this run finished.
            </p>
          )}
        </div>
      }
      inspector={
        selected ? (
          <SpanDetail span={selected} stream={view.streams[selected.span_id]} now={now} />
        ) : (
          <EmptyState
            icon="span"
            title={spanId ? "Not started yet" : "Select a span"}
            className="px-4 py-8"
          >
            <p className="m-0">
              {spanId
                ? "The selected span begins later than the current replay position."
                : "Pick a row to see its messages, arguments, result and raw attributes. Use j and k to move through the run."}
            </p>
            {spanId && (
              <Button onClick={() => update((p) => playFromStart(p, bounds))} icon="play">
                Replay from the start
              </Button>
            )}
          </EmptyState>
        )
      }
    />
  );
}
