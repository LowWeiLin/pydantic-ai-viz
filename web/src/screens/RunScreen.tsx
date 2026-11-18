/**
 * The run screen: the blueprint canvas with the run drawn on it, the timeline dock with the
 * playback bar, and the inspector for the selected span.
 *
 * Everything here is a projection of the event log at the playback time. The selected span is
 * the `?span=` part of the route; the canvas subject and a picked blueprint part are local.
 */
import { useEffect, useMemo, useState } from "react";
import { AgentCanvas } from "../canvas/AgentCanvas";
import { GraphCanvas } from "../canvas/GraphCanvas";
import { agentModelSignature, graphModelSignature, useSettled } from "../canvas/signature";
import { type CanvasTarget, latestSpanFor, spanTargets } from "../canvas/targets";
import { subjectMermaid } from "../data/mermaid";
import { changeSpeed, goLive, playFromStart, seek, step, togglePlay } from "../data/playback";
import {
  canvasModel,
  graphCanvasModel,
  type RunSubject,
  type RunView,
  runBoundaries,
  runSubjects,
  runTotals,
  runView,
} from "../data/selectors";
import { useViewer } from "../data/store";
import {
  useAutoReplay,
  usePlaybackDriver,
  usePlaybackTime,
  useRunBounds,
} from "../data/usePlayback";
import { BlueprintInspector, SpanInspector } from "../inspector/Inspector";
import { rememberTab } from "../inspector/parts";
import { formatCount, formatDuration, plural } from "../shell/format";
import { navigate } from "../shell/router";
import { useShortcuts } from "../shell/shortcuts";
import { NotFound } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Timeline } from "../timeline/Timeline";
import { Chip, StatusDot } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CopyButton } from "../ui/CopyButton";
import { EmptyState } from "../ui/EmptyState";
import { kindMeta } from "../ui/tone";
import { ScreenBar, Segmented } from "./parts";

const LIVE = Number.POSITIVE_INFINITY;
const subjectKey = (subject: RunSubject): string => `${subject.kind}:${subject.id}`;

function RunCanvas({
  view,
  subject,
  subjects,
  onSubject,
  spanId,
  picked,
  onPick,
  onClear,
}: {
  view: RunView;
  subject: RunSubject;
  subjects: RunSubject[];
  onSubject: (key: string) => void;
  spanId: string | null;
  picked: CanvasTarget | null;
  /** A node was clicked; `latest` is the newest span on it, if it has been touched. */
  onPick: (target: CanvasTarget, latest: string | null) => void;
  onClear: () => void;
}) {
  const data = useViewer((state) => state.data);
  const agentNow = subject.kind === "agent" ? canvasModel(data, subject.id, view) : null;
  const graphNow = subject.kind === "graph" ? graphCanvasModel(data, subject.id, view) : null;
  const agentModel = useSettled(agentNow, agentNow ? agentModelSignature(agentNow) : "", data);
  const graphModel = useSettled(graphNow, graphNow ? graphModelSignature(graphNow) : "", data);

  const selectedVisible = spanId !== null && view.spans[spanId] !== undefined;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a span's ancestors do not change while it is visible.
  const highlight = useMemo<readonly CanvasTarget[]>(() => {
    if (spanId && selectedVisible) return spanTargets(data, view, spanId);
    return picked ? [picked] : [];
  }, [data, view.traceId, spanId, selectedVisible, picked]);

  const switcher =
    subjects.length > 1 ? (
      <Segmented
        label="Canvas subject"
        value={subjectKey(subject)}
        onChange={onSubject}
        options={subjects.map((s) => ({
          value: subjectKey(s),
          label: s.label,
          icon: s.kind === "graph" ? "graph" : "agent",
        }))}
      />
    ) : undefined;

  if (agentModel) {
    return (
      <AgentCanvas
        model={agentModel}
        overlay
        highlight={highlight}
        onPick={(target) => onPick(target, latestSpanFor(agentModel, target))}
        onClear={onClear}
        topLeft={switcher}
      />
    );
  }
  if (graphModel) {
    return (
      <GraphCanvas
        model={graphModel}
        overlay
        highlight={highlight}
        onPick={(target) => onPick(target, latestSpanFor(graphModel, target))}
        onClear={onClear}
        topLeft={switcher}
      />
    );
  }
  return null;
}

export function RunScreen({ traceId, spanId }: { traceId: string; spanId: string | null }) {
  const data = useViewer((state) => state.data);
  const playback = useViewer((state) => state.playback);
  const update = useViewer((state) => state.updatePlayback);
  const live = useViewer((state) => state.source.kind === "live");
  const t = usePlaybackTime();

  const bounds = useRunBounds(traceId);
  usePlaybackDriver(bounds);
  useAutoReplay(traceId, bounds, spanId);

  const view = useMemo(() => runView(data, traceId, t), [data, traceId, t]);
  const totals = useMemo(() => runTotals(data, traceId, t), [data, traceId, t]);
  const now = Math.min(t, Math.max(data.lastTs, bounds?.end ?? 0));

  // The switcher lists everything the whole run involves, not just what has started by now.
  const subjects = useMemo(() => {
    const whole = runView(data, traceId, LIVE);
    return whole ? runSubjects(data, whole) : [];
  }, [data, traceId]);
  const [chosenSubject, setChosenSubject] = useState<string | null>(null);
  const subject = subjects.find((s) => subjectKey(s) === chosenSubject) ?? subjects[0] ?? null;
  const [picked, setPicked] = useState<CanvasTarget | null>(null);

  const select = (id: string | null): void =>
    navigate({ name: "run", traceId, spanId: id }, { replace: true });

  const move = (direction: 1 | -1): void => {
    if (!view || view.order.length === 0) return;
    const index = spanId ? view.order.indexOf(spanId) : -1;
    const next = view.order[Math.min(view.order.length - 1, Math.max(0, index + direction))];
    if (next) select(next);
  };

  const stepBy = (direction: 1 | -1): void => {
    if (bounds) update((p) => step(p, runBoundaries(data, traceId), direction, bounds));
  };

  useShortcuts(
    {
      nextSpan: () => move(1),
      prevSpan: () => move(-1),
      clearSelection: () => {
        setPicked(null);
        select(null);
      },
      togglePlay: () => bounds && update((p) => togglePlay(p, bounds)),
      slower: () => update((p) => changeSpeed(p, -1)),
      faster: () => update((p) => changeSpeed(p, 1)),
      stepBack: () => stepBy(-1),
      stepForward: () => stepBy(1),
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
  // Selected, but not started yet at the replay position: the empty state offers to go to it.
  const later = spanId && !selected ? data.spans[spanId] : undefined;
  // With nothing selected the inspector shows the run itself, as a blueprint screen shows
  // its agent.
  const inspected = selected ?? (spanId === null && picked === null ? root : undefined);
  const tokens = totals.usage.input_tokens + totals.usage.output_tokens;
  const cutShort =
    !live && view.status === "running" && playback.mode !== "playing" && t >= bounds.end;
  const graph = subject?.kind === "graph" ? data.graphs[subject.id] : null;
  // The blueprint the run is drawn on can be copied as Mermaid: the picture without the run.
  const hasMermaid = subject !== null && (subject.kind === "agent" || graph?.mermaid != null);

  return (
    <Workspace
      inspectorLabel="Span details"
      main={
        <div className="flex h-full flex-col">
          <ScreenBar
            icon={kind.icon}
            tone={kind.tone}
            kind={kind.label}
            title={root?.name ?? fullRoot?.name ?? "Run"}
            chips={
              <>
                <StatusDot status={view.status} showLabel className="mr-1" />
                <Chip mono>
                  {formatDuration(Math.max(0, Math.min(t, bounds.end) - bounds.start))}
                </Chip>
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
              hasMermaid ? (
                <CopyButton text={() => subjectMermaid(data, subject) ?? ""} label="Copy Mermaid" />
              ) : undefined
            }
          />
          <div className="min-h-0 flex-1">
            {subject ? (
              <RunCanvas
                key={subjectKey(subject)}
                view={view}
                subject={subject}
                subjects={subjects}
                onSubject={setChosenSubject}
                spanId={spanId}
                picked={picked}
                onPick={(target, latest) => {
                  if (target.kind === "output") rememberTab("agent_run", "output");
                  setPicked(latest ? null : target);
                  select(latest);
                }}
                onClear={() => {
                  setPicked(null);
                  select(null);
                }}
              />
            ) : (
              <div className="dot-grid h-full overflow-auto">
                <EmptyState icon="span" title="No blueprint for this run" className="py-8">
                  <p className="m-0">
                    The session has no agent or graph blueprint that matches this run, so there is
                    no canvas to draw it on. The timeline below still has every span.
                  </p>
                </EmptyState>
              </div>
            )}
          </div>
        </div>
      }
      dock={
        <Timeline
          view={view}
          bounds={bounds}
          playback={playback}
          now={now}
          live={live}
          selectedId={spanId}
          onSelect={(id) => {
            setPicked(null);
            select(id);
          }}
          onStep={stepBy}
          note={cutShort ? "The recording ends here, before this run finished." : undefined}
        />
      }
      inspector={
        inspected ? (
          <SpanInspector span={inspected} view={view} now={now} />
        ) : picked ? (
          <BlueprintInspector target={picked} graph={graph} />
        ) : (
          <EmptyState icon="span" title="Not started yet" className="px-4 py-8">
            <p className="m-0">
              {spanId
                ? "The selected span begins later than the current replay position."
                : "The run begins later than the current replay position."}
            </p>
            {later ? (
              <Button
                onClick={() => update((p) => seek(p, later.end_ms ?? later.start_ms, bounds))}
                icon="stepForward"
              >
                Jump to this span
              </Button>
            ) : (
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
