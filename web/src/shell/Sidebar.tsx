import { type ReactNode, useMemo } from "react";
import { projectSpan, type RunSummary, runs, runTotals } from "../data/selectors";
import { useViewer } from "../data/store";
import { usePlaybackTime } from "../data/usePlayback";
import { Badge, StatusDot } from "../ui/Badge";
import { Skeleton } from "../ui/EmptyState";
import { Icon, type IconName } from "../ui/Icon";
import { cx, kindMeta, type Tone, toneClass } from "../ui/tone";
import { formatCount, formatDuration } from "./format";
import { formatRoute, type Route } from "./router";

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: ReactNode;
}) {
  const id = `sidebar-${title.toLowerCase()}`;
  return (
    <nav aria-labelledby={id} className="flex min-h-0 flex-col">
      <h2
        id={id}
        className="m-0 flex items-center justify-between px-3 pt-3 pb-1.5 font-semibold text-faint text-meta uppercase tracking-[0.06em]"
      >
        {title}
        <span className="font-normal tracking-normal">{formatCount(count)}</span>
      </h2>
      {children}
    </nav>
  );
}

const rowClass = (active: boolean): string =>
  cx(
    "group flex min-h-8 items-center gap-2 px-3 py-1 text-fg no-underline",
    "transition-colors duration-150 ease-out hover:bg-surface-2 focus-visible:outline-offset-[-2px]",
    active && "tone-rail bg-surface-2",
  );

function BlueprintLink({
  route,
  active,
  icon,
  tone,
  label,
  meta,
  badge,
}: {
  route: Route;
  active: boolean;
  icon: IconName;
  tone: Tone;
  label: string;
  meta: string | null;
  badge?: ReactNode;
}) {
  return (
    <li>
      <a
        href={formatRoute(route)}
        aria-current={active ? "page" : undefined}
        className={cx(toneClass[tone], rowClass(active))}
      >
        <Icon name={icon} size={14} className="tone-text" />
        <span className="min-w-0 flex-1 truncate font-medium text-ui">{label}</span>
        {badge}
        {meta && (
          <span className="max-w-24 shrink-0 truncate font-mono text-faint text-meta max-md:hidden">
            {meta}
          </span>
        )}
      </a>
    </li>
  );
}

export function BlueprintList({ route }: { route: Route }) {
  const data = useViewer((state) => state.data);
  const count = data.agentOrder.length + data.graphOrder.length;
  return (
    <Section title="Blueprints" count={count}>
      {count === 0 ? (
        <p className="m-0 px-3 pb-2 text-faint text-ui">None yet.</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {data.agentOrder.map((id) => {
            const agent = data.agents[id];
            if (!agent) return null;
            return (
              <BlueprintLink
                key={id}
                route={{ name: "agent", id }}
                active={route.name === "agent" && route.id === id}
                icon="agent"
                tone="agent"
                label={agent.label}
                meta={agent.model?.name ?? null}
                badge={
                  agent.warnings.length > 0 ? (
                    <Badge tone="retry">
                      <span className="sr-only">Warnings: </span>
                      {agent.warnings.length}
                    </Badge>
                  ) : undefined
                }
              />
            );
          })}
          {data.graphOrder.map((id) => {
            const graph = data.graphs[id];
            if (!graph) return null;
            return (
              <BlueprintLink
                key={id}
                route={{ name: "graph", id }}
                active={route.name === "graph" && route.id === id}
                icon="graph"
                tone="graph"
                label={graph.label}
                meta={`${graph.nodes.length} nodes`}
              />
            );
          })}
        </ul>
      )}
    </Section>
  );
}

export function RunListItem({ run, active }: { run: RunSummary; active: boolean }) {
  const kind = kindMeta[run.kind];
  const tokens = run.usage.input_tokens + run.usage.output_tokens;
  return (
    <li className="animate-fade-in">
      <a
        href={formatRoute({ name: "run", traceId: run.traceId, spanId: null })}
        aria-current={active ? "page" : undefined}
        className={cx(toneClass[kind.tone], rowClass(active), "items-start py-1.5")}
      >
        <StatusDot status={run.status} className="mt-0.5" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium text-ui">{run.name}</span>
            <Icon name={kind.icon} size={12} className="tone-text" label={kind.label} />
          </span>
          <span className="flex gap-2 font-mono text-faint text-meta">
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
  const shown = useMemo((): RunSummary => {
    const root = data.spans[run.rootSpanId];
    const projected = root ? projectSpan(root, t) : null;
    if (!projected) return run;
    const totals = runTotals(data, run.traceId, t);
    return { ...run, status: projected.status, durationMs: totals.durationMs, usage: totals.usage };
  }, [data, run, t]);
  return <RunListItem run={shown} active />;
}

export function RunList({ route }: { route: Route }) {
  const data = useViewer((state) => state.data);
  const list = useMemo(() => runs(data), [data]);
  return (
    <Section title="Runs" count={list.length}>
      {list.length === 0 ? (
        <p className="m-0 px-3 pb-2 text-faint text-ui">No runs yet.</p>
      ) : (
        <ul className="m-0 min-h-0 list-none overflow-y-auto p-0">
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

export function Sidebar({ route }: { route: Route }) {
  return (
    <aside
      aria-label="Blueprints and runs"
      className="flex w-60 shrink-0 flex-col border-line border-r bg-surface max-xl:w-52 max-md:w-48"
    >
      <BlueprintList route={route} />
      <div className="mx-3 mt-2 border-line border-t" />
      <RunList route={route} />
    </aside>
  );
}

/** "Connecting": the sidebar's shape, without content. */
export function SidebarSkeleton() {
  return (
    <aside
      aria-hidden="true"
      className="flex w-60 shrink-0 flex-col gap-2 border-line border-r bg-surface p-3 max-xl:w-52 max-md:w-48"
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
