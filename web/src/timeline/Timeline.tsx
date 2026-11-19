/**
 * The timeline dock: the playback bar, then the span tree with a waterfall. The ruler above
 * the waterfall is the scrubber, so the playhead, the bars and the clock share one time axis.
 *
 * Rows are the spans visible at the replay position, depth first; parallel tool calls are
 * siblings. The tree is an ARIA tree with one tab stop; Up, Down, Home and End move through it.
 */
import {
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type Bounds, type Playback, seek } from "../data/playback";
import { displayStatus, type RunView } from "../data/selectors";
import { useViewer } from "../data/store";
import { spanDuration, statusLabel } from "../inspector/parts";
import { formatDuration, plural } from "../shell/format";
import { StatusDot } from "../ui/Badge";
import { Icon } from "../ui/Icon";
import { MiddleTruncate } from "../ui/MiddleTruncate";
import { cx, kindMeta, statusMeta, toneClass } from "../ui/tone";
import { useStickToBottom } from "../ui/useStickToBottom";
import type { Span } from "../wire";
import { PlaybackBar } from "./PlaybackBar";
import { barExtent, fraction, rulerTicks, tickLabel } from "./ruler";

/**
 * Name column and waterfall column. Shared by the header, the rows and the playhead layer.
 * On a phone the name column gives up width so the waterfall keeps some.
 */
const COLUMNS =
  "grid grid-cols-[minmax(240px,34%)_minmax(0,1fr)] max-sm:grid-cols-[minmax(150px,46%)_minmax(0,1fr)]";
/** The waterfall track: inset on the right so a duration label fits after the last bar. */
const TRACK = "relative ml-3 mr-[76px]";

const percent = (value: number): string => `${(value * 100).toFixed(3)}%`;

const PHONE = "(max-width: 639px)";

/** How many ruler ticks fit: eight labels overlap in the narrow waterfall of a phone. */
function useTickCount(): number {
  const query = (): boolean =>
    typeof window.matchMedia === "function" && window.matchMedia(PHONE).matches;
  const [phone, setPhone] = useState(query);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(PHONE);
    const change = (): void => setPhone(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  return phone ? 3 : 8;
}

/** The ruler, which is also the scrubber: drag or use the arrow keys to move through time. */
export function Ruler({ playback, bounds }: { playback: Playback; bounds: Bounds }) {
  const update = useViewer((state) => state.updatePlayback);
  const dragging = useRef(false);
  const total = Math.max(0, bounds.end - bounds.start);
  const position = playback.mode === "live" ? 1 : fraction(playback.t, bounds);
  const tickCount = useTickCount();
  const ticks = useMemo(() => rulerTicks(total, tickCount), [total, tickCount]);

  const seekTo = (t: number): void => update((p) => seek(p, t, bounds));
  const seekAt = (event: PointerEvent<HTMLDivElement>): void => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const at = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    seekTo(bounds.start + at * total);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const current = bounds.start + position * total;
    const nudge = total * (event.shiftKey ? 0.1 : 0.02);
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") seekTo(current - nudge);
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") seekTo(current + nudge);
    else if (event.key === "Home") seekTo(bounds.start);
    else if (event.key === "End") seekTo(bounds.end);
    else return;
    event.preventDefault();
  };

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label="Replay position"
      aria-orientation="horizontal"
      aria-valuemin={0}
      aria-valuemax={Math.round(total)}
      aria-valuenow={Math.round(position * total)}
      aria-valuetext={`${formatDuration(position * total)} of ${formatDuration(total)}`}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        dragging.current = true;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        seekAt(event);
      }}
      onPointerMove={(event) => {
        if (dragging.current) seekAt(event);
      }}
      onPointerUp={() => {
        dragging.current = false;
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
      onKeyDown={onKeyDown}
      className={cx(TRACK, "h-full cursor-ew-resize touch-none select-none")}
    >
      {ticks.map((tick) => (
        <span
          key={tick}
          className="absolute inset-y-0 flex items-end whitespace-nowrap border-line-strong border-l pb-0.5 pl-1 font-mono text-[10px] text-faint leading-none"
          style={{ left: percent(total > 0 ? tick / total : 0) }}
        >
          {tickLabel(tick)}
        </span>
      ))}
      <span
        aria-hidden="true"
        className="-translate-x-1/2 absolute top-0 h-3 w-2.5 rounded-b-[3px] bg-accent"
        style={{ left: percent(position) }}
      />
    </div>
  );
}

function barClass(span: Span): string {
  const status = displayStatus(span);
  if (status === "running") return "tone-accent pviz-bar-running";
  if (status === "error") return "tone-error";
  if (status === "retry") return "tone-retry";
  return toneClass[kindMeta[span.kind].tone];
}

export function TimelineRow({
  span,
  depth,
  selected,
  tabStop,
  bounds,
  now,
  onSelect,
  onKeyDown,
}: {
  span: Span;
  depth: number;
  selected: boolean;
  tabStop: boolean;
  bounds: Bounds;
  now: number;
  onSelect: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
}) {
  const kind = kindMeta[span.kind];
  const status = displayStatus(span);
  const { left, width } = barExtent(span.start_ms, span.end_ms, now, bounds);
  return (
    <div
      role="treeitem"
      data-span={span.span_id}
      aria-level={depth + 1}
      aria-selected={selected}
      tabIndex={tabStop ? 0 : -1}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      className={cx(
        COLUMNS,
        toneClass[kind.tone],
        "h-7 animate-fade-in cursor-pointer items-center border-line/60 border-b",
        "transition-colors duration-150 ease-out hover:bg-surface-2 focus-visible:outline-offset-[-2px]",
        selected && "tone-rail bg-surface-2",
      )}
    >
      <div
        className="flex min-w-0 items-center gap-2 pr-2"
        style={{ paddingLeft: 12 + depth * 14 }}
      >
        <Icon name={kind.icon} size={14} className="tone-text" label={kind.label} />
        <MiddleTruncate text={span.name} className="min-w-0 flex-1 font-mono text-data" />
        <StatusDot
          status={status}
          showLabel
          label={statusLabel(span)}
          className="shrink-0 whitespace-nowrap"
        />
      </div>
      <div className={cx(TRACK, "h-full")}>
        <span
          aria-hidden="true"
          className={cx(
            barClass(span),
            "-translate-y-1/2 absolute top-1/2 h-2.5 min-w-[3px] rounded-[3px] bg-(--tone)",
          )}
          style={{ left: percent(left), width: percent(width) }}
        />
        <span
          className="-translate-y-1/2 absolute top-1/2 ml-1.5 whitespace-nowrap font-mono text-faint text-meta"
          style={{ left: `max(${percent(left + width)}, calc(${percent(left)} + 3px))` }}
        >
          {formatDuration(spanDuration(span, now))}
        </span>
      </div>
    </div>
  );
}

export function Timeline({
  view,
  bounds,
  playback,
  now,
  live,
  selectedId,
  onSelect,
  onStep,
  note,
}: {
  view: RunView;
  bounds: Bounds;
  playback: Playback;
  /** The time running spans are measured against. */
  now: number;
  live: boolean;
  selectedId: string | null;
  onSelect: (spanId: string) => void;
  onStep: (direction: 1 | -1) => void;
  /** Shown in the playback bar. */
  note?: ReactNode;
}) {
  const rowCount = view.order.length;
  // While the clock is moving and nothing is selected, follow the newest row. Scrolling up to
  // read earlier rows lets go of it, so the list does not snap back with every new span.
  const scroller = useStickToBottom<HTMLDivElement>(
    playback.mode !== "paused" && !selectedId,
    rowCount,
  );
  const total = Math.max(0, bounds.end - bounds.start);
  const position = playback.mode === "live" ? 1 : fraction(playback.t, bounds);
  const tickCount = useTickCount();
  const ticks = useMemo(() => rulerTicks(total, tickCount), [total, tickCount]);
  // One tab stop: the selected row, or the first.
  const tabStop = selectedId && view.spans[selectedId] ? selectedId : view.order[0];

  // Keep the selected row in sight when the selection moves (j and k, a click on the canvas).
  useEffect(() => {
    if (!selectedId) return;
    scroller.current
      ?.querySelector<HTMLElement>(`[data-span="${selectedId}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, scroller]);

  const onRowKeyDown = (event: KeyboardEvent<HTMLDivElement>, id: string): void => {
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
    // Enter selects. Space is left alone: it plays and pauses, wherever the focus is.
    if (event.key === "Enter") {
      event.preventDefault();
      onSelect(id);
    } else if (target !== undefined) {
      event.preventDefault();
      onSelect(target);
      scroller.current?.querySelector<HTMLElement>(`[data-span="${target}"]`)?.focus();
    }
  };

  return (
    <>
      <PlaybackBar playback={playback} bounds={bounds} live={live} onStep={onStep}>
        {note}
      </PlaybackBar>
      <div ref={scroller} className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div className={cx(COLUMNS, "sticky top-0 z-20 h-7 border-line border-b bg-surface")}>
          <div className="flex items-center justify-between gap-2 px-3 font-semibold text-faint text-meta uppercase tracking-[0.06em]">
            <span>Spans</span>
            <span className="font-normal normal-case tracking-normal">
              {plural(rowCount, "span")}
            </span>
          </div>
          <Ruler playback={playback} bounds={bounds} />
        </div>
        <div className="relative min-h-[calc(100%-28px)]">
          {/* Gridlines and the playhead, behind and over the rows on the same columns. */}
          <div aria-hidden="true" className={cx(COLUMNS, "pointer-events-none absolute inset-0")}>
            <div />
            <div className={TRACK}>
              {ticks.map((tick) => (
                <span
                  key={tick}
                  className="absolute inset-y-0 border-line/70 border-l"
                  style={{ left: percent(total > 0 ? tick / total : 0) }}
                />
              ))}
              <span
                className="-translate-x-1/2 absolute inset-y-0 z-10 w-px bg-accent"
                style={{ left: percent(position) }}
              />
            </div>
          </div>
          {rowCount === 0 ? (
            <p className="m-0 flex items-center gap-1.5 px-3 py-3 text-muted text-ui">
              <Icon name={statusMeta.idle.icon} size={14} className="text-faint" />
              The run has not started at this point in time. Press play, or drag the ruler.
            </p>
          ) : (
            <div role="tree" aria-label="Spans of this run" className="relative">
              {view.order.map((id) => {
                const span = view.spans[id];
                if (!span) return null;
                return (
                  <TimelineRow
                    key={id}
                    span={span}
                    depth={view.depth[id] ?? 0}
                    selected={id === selectedId}
                    tabStop={id === tabStop}
                    bounds={bounds}
                    now={now}
                    onSelect={() => onSelect(id)}
                    onKeyDown={(event) => onRowKeyDown(event, id)}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
