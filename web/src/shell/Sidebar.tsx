import { type ReactNode, useMemo, useState } from "react";
import {
  BLUEPRINT_FILTER_FROM,
  type BlueprintEntry,
  blueprintIndex,
  projectSpan,
  type RunSummary,
  runs,
  runTotals,
} from "../data/selectors";
import { useViewer } from "../data/store";
import { usePlaybackTime, useRunBounds } from "../data/usePlayback";
import { StatusDot } from "../ui/Badge";
import { Skeleton } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { PathText } from "../ui/PathText";
import { cx, kindMeta, toneClass } from "../ui/tone";
import { formatClock, formatCount, formatDuration, plural } from "./format";
import { formatRoute, type Route } from "./router";
import { type AboutBlueprints, type ShellStatus, useShellStatus } from "./status";

const headingClass =
  "m-0 flex shrink-0 items-center justify-between px-3 pt-3 pb-1.5 font-semibold text-faint text-meta uppercase tracking-[0.06em]";

function Section({
  title,
  count,
  className,
  children,
}: {
  title: string;
  /** Shown beside the title: a number, or "3 of 40" while a filter is on. */
  count: string;
  className: string;
  children: ReactNode;
}) {
  const id = `sidebar-${title.toLowerCase()}`;
  return (
    <nav aria-labelledby={id} className={cx("flex min-h-0 flex-col", className)}>
      <h2 id={id} className={headingClass}>
        {title}
        <span className="font-normal normal-case tracking-normal">{count}</span>
      </h2>
      {children}
    </nav>
  );
}

/**
 * A list that scrolls inside the sidebar. It is `relative` so that what is absolutely
 * positioned in a row (every `sr-only` label is) scrolls and is clipped with the row. Laid out
 * against a far ancestor instead, such a label makes the whole document as tall as the list.
 */
const scrollList = "relative m-0 min-h-0 list-none overflow-y-auto p-0";

const rowClass = (active: boolean): string =>
  cx(
    "group flex min-h-8 items-center gap-2 px-3 py-1 text-fg no-underline",
    "transition-colors duration-150 ease-out hover:bg-surface-2 focus-visible:outline-offset-[-2px]",
    active && "tone-rail bg-surface-2",
  );

function BlueprintLink({ entry, route }: { entry: BlueprintEntry; route: Route }) {
  const active = route.name === entry.kind && route.id === entry.id;
  return (
    <li>
      <a
        href={formatRoute({ name: entry.kind, id: entry.id })}
        aria-current={active ? "page" : undefined}
        className={cx(toneClass[entry.kind], rowClass(active))}
      >
        <Icon name={entry.kind} size={14} className="tone-text" />
        {/* The title gives the whole name where the column cuts it short. */}
        <span className="min-w-0 flex-1 truncate font-medium text-ui" title={entry.label}>
          {entry.label}
        </span>
        {entry.warnings > 0 && (
          // Which blueprints the analysis could not read in full, at a glance down a long list.
          // The warnings themselves are in the blueprint's panel.
          <>
            <span
              aria-hidden="true"
              title={plural(entry.warnings, "warning")}
              className="flex shrink-0 items-center gap-0.5 text-faint text-meta tabular-nums"
            >
              <Icon name="warning" size={12} />
              {entry.warnings}
            </span>
            <span className="sr-only">, {plural(entry.warnings, "warning")}</span>
          </>
        )}
      </a>
    </li>
  );
}

/** A file path as a group heading: the folder gives way first, then the end of the name. */
function FileHeading({ file }: { file: string | null }) {
  return (
    <h3
      title={file ?? undefined}
      className="sticky top-0 z-10 m-0 flex items-center gap-1.5 bg-surface px-3 pt-2 pb-1 font-normal text-data"
    >
      <Icon name={file === null ? "object" : "file"} size={12} className="text-faint" />
      {file === null ? (
        <span className="min-w-0 flex-1 truncate text-muted">Not read from source</span>
      ) : (
        <PathText
          path={file}
          className="flex-1 font-mono text-muted"
          folderClassName="text-faint"
        />
      )}
    </h3>
  );
}

export function BlueprintList({ route, fill }: { route: Route; fill: boolean }) {
  const agents = useViewer((state) => state.data.agents);
  const agentOrder = useViewer((state) => state.data.agentOrder);
  const graphs = useViewer((state) => state.data.graphs);
  const graphOrder = useViewer((state) => state.data.graphOrder);
  const [query, setQuery] = useState("");
  const index = useMemo(
    () => blueprintIndex({ agents, agentOrder, graphs, graphOrder }, query),
    [agents, agentOrder, graphs, graphOrder, query],
  );
  const filterable = index.total >= BLUEPRINT_FILTER_FROM;
  const filtering = filterable && query.trim() !== "";
  const links = (entries: readonly BlueprintEntry[]) =>
    entries.map((entry) => (
      <BlueprintLink key={`${entry.kind}:${entry.id}`} entry={entry} route={route} />
    ));
  return (
    // With runs below, the list keeps its own height, up to a share of the sidebar, and scrolls
    // past that. Without them it takes the room the runs would have had.
    <Section
      title="Blueprints"
      count={
        filtering
          ? `${formatCount(index.matched)} of ${formatCount(index.total)}`
          : formatCount(index.total)
      }
      className={fill ? "flex-1" : "max-h-[45%] shrink-0"}
    >
      {filterable && (
        <div className="relative mx-3 mb-1.5 shrink-0">
          <Icon
            name="search"
            size={13}
            className="-translate-y-1/2 pointer-events-none absolute top-1/2 left-2 text-faint"
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && query !== "") {
                event.preventDefault();
                setQuery("");
              }
            }}
            aria-label="Filter blueprints"
            placeholder="Filter by name or file"
            className="h-7 w-full rounded-control border border-line-strong bg-bg pr-2 pl-7 text-fg text-ui placeholder:text-faint"
          />
        </div>
      )}
      {index.total === 0 ? (
        <p className="m-0 px-3 pb-2 text-faint text-ui">None yet.</p>
      ) : index.matched === 0 ? (
        <p className="m-0 px-3 pb-2 text-muted text-ui" role="status">
          No blueprint matches "{query.trim()}".
        </p>
      ) : index.byFile ? (
        <ul className={cx(scrollList, "pb-1")}>
          {index.groups.map((group) => (
            <li key={group.file ?? ""}>
              <FileHeading file={group.file} />
              <ul className="m-0 list-none p-0">{links(group.entries)}</ul>
            </li>
          ))}
        </ul>
      ) : (
        <ul className={scrollList}>{index.groups.map((group) => links(group.entries))}</ul>
      )}
    </Section>
  );
}

export function RunListItem({ run, active }: { run: RunSummary; active: boolean }) {
  const kind = kindMeta[run.kind];
  const tokens = run.usage.input_tokens + run.usage.output_tokens;
  // The same agent is run again and again, so the name says little. When it started tells
  // one run from the next.
  const started = new Date(run.startMs);
  return (
    <li className="animate-fade-in">
      <a
        href={formatRoute({ name: "run", traceId: run.traceId, spanId: null })}
        aria-current={active ? "page" : undefined}
        title={`Started ${started.toLocaleString()}, trace ${run.traceId}`}
        className={cx(toneClass[kind.tone], rowClass(active), "items-start py-1.5")}
      >
        <StatusDot status={run.status} className="mt-0.5" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium text-ui">{run.name}</span>
            <Icon name={kind.icon} size={12} className="tone-text" label={kind.label} />
          </span>
          <span className="flex flex-wrap gap-x-2 font-mono text-faint text-meta">
            <time dateTime={started.toISOString()}>{formatClock(run.startMs)}</time>
            <span>{formatDuration(run.durationMs)}</span>
            {tokens > 0 && <span>{formatCount(tokens)} tok</span>}
          </span>
        </span>
      </a>
    </li>
  );
}

/** The run on screen follows the replay clock, like everything else about it. */
function OpenRunItem({ run }: { run: RunSummary }) {
  const data = useViewer((state) => state.data);
  const t = usePlaybackTime();
  const bounds = useRunBounds(run.traceId);
  const shown = useMemo((): RunSummary => {
    const root = data.spans[run.rootSpanId];
    const projected = root ? projectSpan(root, t) : null;
    if (!projected) return run;
    const totals = runTotals(data, run.traceId, t);
    // The same clock as the run screen: a run watched live counts on between events.
    const durationMs = bounds ? Math.max(0, Math.min(t, bounds.end) - bounds.start) : 0;
    return { ...run, status: projected.status, durationMs, usage: totals.usage };
  }, [data, run, t, bounds]);
  return <RunListItem run={shown} active />;
}

const RUNS_EMPTY: Record<NonNullable<ShellStatus["runsEmpty"]>, string> = {
  recording: "No runs in this recording.",
  export: "No runs in this export.",
};

export function RunList({
  route,
  empty,
}: {
  route: Route;
  /** What to say when there is no run; null when the screen already says it. */
  empty: ShellStatus["runsEmpty"];
}) {
  const data = useViewer((state) => state.data);
  const list = useMemo(() => runs(data), [data]);
  return (
    <Section title="Runs" count={formatCount(list.length)} className="flex-1">
      {list.length === 0 ? (
        empty && <p className="m-0 px-3 pb-2 text-faint text-ui">{RUNS_EMPTY[empty]}</p>
      ) : (
        <ul className={scrollList}>
          {list.map((run) =>
            route.name === "run" && route.traceId === run.traceId ? (
              <OpenRunItem key={run.traceId} run={run} />
            ) : (
              <RunListItem key={run.traceId} run={run} active={false} />
            ),
          )}
        </ul>
      )}
    </Section>
  );
}

const ORIGIN: Record<AboutBlueprints["origin"], { title: string; icon: "code" | "object" }> = {
  code: { title: "Read from source code", icon: "code" },
  object: { title: "Read from live objects", icon: "object" },
  mixed: { title: "Read from source code and live objects", icon: "code" },
};

const HOW: Record<AboutBlueprints["origin"], string> = {
  code: "The code was parsed and never run, so there are no runs here.",
  object:
    "The code was imported to read what it builds. No agent was run, so there are no runs here.",
  mixed:
    "Some of the code was parsed and some was imported. No agent was run, so there are no runs here.",
};

/**
 * Where the runs would be in a session that can have them: what a blueprint-only view is, why
 * it has no runs, and how to get one.
 */
export function AboutView({ about }: { about: AboutBlueprints }) {
  const origin = ORIGIN[about.origin];
  return (
    <section
      aria-labelledby="sidebar-about"
      className="flex max-h-[50%] shrink-0 flex-col overflow-y-auto"
    >
      <h2 id="sidebar-about" className={headingClass}>
        About this view
      </h2>
      <div className="flex flex-col gap-2 px-3 pb-3 text-muted text-ui">
        <p className="m-0 flex items-center gap-1.5 font-medium text-fg">
          <Icon name={origin.icon} size={14} className="text-faint" />
          {origin.title}
        </p>
        <p className="m-0">{HOW[about.origin]}</p>
        <p className="m-0">
          To see a run, call{" "}
          {/* A narrow sidebar breaks the call after the module, not in the middle of a word. */}
          <code className="break-words text-fg">
            pydantic_ai_viz.
            <wbr />
            serve(agent)
          </code>{" "}
          in your program, then run the agent.
        </p>
      </div>
    </section>
  );
}

/** On a phone the sidebar is a drawer over the screen, shown only when `open`. */
export function Sidebar({ route, open = false }: { route: Route; open?: boolean }) {
  const status = useShellStatus();
  return (
    <aside
      id="sidebar"
      aria-label={status.about ? "Blueprints" : "Blueprints and runs"}
      className={cx(
        "flex w-60 shrink-0 flex-col border-line border-r bg-surface max-xl:w-52 max-md:w-48",
        "max-sm:absolute max-sm:inset-y-0 max-sm:left-0 max-sm:z-30 max-sm:w-[min(280px,85%)] max-sm:shadow-float",
        !open && "max-sm:hidden",
      )}
    >
      <BlueprintList route={route} fill={status.about !== null} />
      <div className="mx-3 mt-2 shrink-0 border-line border-t" />
      {status.about ? (
        <AboutView about={status.about} />
      ) : (
        <RunList route={route} empty={status.runsEmpty} />
      )}
    </aside>
  );
}

/** "Connecting": the sidebar's shape, without content. */
export function SidebarSkeleton() {
  return (
    <aside
      aria-hidden="true"
      className="flex w-60 shrink-0 flex-col gap-2 border-line border-r bg-surface p-3 max-xl:w-52 max-md:w-48 max-sm:hidden"
    >
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-6 w-4/5" />
      <Skeleton className="mt-4 h-3 w-12" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-3/4" />
    </aside>
  );
}
