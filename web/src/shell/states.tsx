/** The designed "nothing to show" screens: connecting, empty, failed, not found. */
import type { Failure } from "../data/store";
import { useViewer } from "../data/store";
import { ButtonLink } from "../ui/Button";
import { EmptyState, Skeleton, Snippet } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { DropZone } from "./DropZone";
import { formatRoute } from "./router";

const SERVE_SNIPPET = `import pydantic_ai_viz as pviz

pviz.serve(agent)   # then run your agent as usual`;

/** "Connecting": the canvas area's shape, with no content. */
export function ConnectingSkeleton() {
  return (
    <div role="status" aria-label="Connecting" className="dot-grid flex-1 overflow-hidden p-8">
      <div className="mx-auto flex max-w-2xl items-center gap-10 pt-16">
        <Skeleton className="h-28 w-56 rounded-card!" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-9 w-44" />
          <Skeleton className="h-9 w-44" />
          <Skeleton className="h-9 w-44" />
        </div>
        <Skeleton className="h-12 w-36" />
      </div>
    </div>
  );
}

/** A non-blocking strip: the connection dropped, what is on screen stays. */
export function ReconnectingBanner() {
  const lost = useViewer((state) => state.connection === "reconnecting");
  if (!lost) return null;
  return (
    <div
      role="status"
      className="tone-retry tone-soft flex shrink-0 animate-fade-in items-center gap-2 border-b px-3 py-1.5 text-ui"
    >
      <Icon name="retry" size={14} className="animate-spin" />
      <span>
        <strong className="font-semibold">Reconnecting...</strong>{" "}
        <span className="text-fg">
          The session stopped answering. Everything received so far is still here.
        </span>
      </span>
    </div>
  );
}

/** No blueprints and no runs: say exactly how to get some. */
export function Welcome() {
  const live = useViewer((state) => state.source.kind === "live");
  if (live) {
    return (
      <EmptyState icon="live" tone="accent" title="Connected. Nothing to show yet.">
        <p className="m-0">
          This session has no agents registered and no runs so far. Pass your agent to{" "}
          <code>serve()</code>, or just run it: runs are captured as soon as they start.
        </p>
        <Snippet code={SERVE_SNIPPET} label="Copy Python snippet" />
      </EmptyState>
    );
  }
  return (
    <EmptyState icon="graph" tone="accent" title="See what your agents are, and what they do">
      <p className="m-0">
        This is the pydantic-ai-viz viewer with no data loaded. Start a live session from a
        terminal, or open a recording you already have.
      </p>
      <Snippet code="uvx pydantic-ai-viz demo" label="Copy demo command" />
      <Snippet code={SERVE_SNIPPET} label="Copy Python snippet" />
      <DropZone />
      <p className="m-0 text-data text-faint">
        Unofficial community project. Not affiliated with or endorsed by Pydantic.
      </p>
    </EmptyState>
  );
}

/** Live, blueprints but no runs: shown above the blueprint. */
export function WaitingForRun() {
  const waiting = useViewer(
    (state) => state.source.kind === "live" && state.data.traceOrder.length === 0,
  );
  if (!waiting) return null;
  return (
    <div
      role="status"
      className="tone-accent flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-line border-b bg-surface px-4 py-2 text-ui"
    >
      <span className="flex items-center gap-2 font-medium">
        <span aria-hidden="true" className="pulse-ring size-1.5 rounded-full bg-accent" />
        Waiting for a run
      </span>
      <span className="text-muted">
        Run your agent (for example <code className="text-fg">await agent.run("...")</code>) and it
        appears here as it happens.
      </span>
    </div>
  );
}

/** A recording that could not be loaded: which file, where, why, and a way forward. */
export function LoadFailed({ failure }: { failure: Failure }) {
  const hasGallery = useViewer((state) => state.gallery !== null);
  const live = useViewer((state) => state.source.kind === "live");
  return (
    <div role="alert" className="h-full overflow-auto">
      <EmptyState
        icon="error"
        tone="error"
        title="This recording could not be loaded"
        actions={
          hasGallery ? (
            <ButtonLink href={formatRoute({ name: "gallery" })} icon="gallery">
              Back to the gallery
            </ButtonLink>
          ) : undefined
        }
      >
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-control border border-line bg-surface p-3 text-ui">
          <dt className="text-faint">File</dt>
          <dd className="m-0 break-all font-mono text-data text-fg">{failure.source}</dd>
          {failure.line !== null && (
            <>
              <dt className="text-faint">Line</dt>
              <dd className="m-0 font-mono text-data text-fg">{failure.line}</dd>
            </>
          )}
          <dt className="text-faint">Reason</dt>
          <dd className="m-0 text-fg">{failure.reason}</dd>
        </dl>
        <p className="m-0">
          Recordings are <code>.pviz.jsonl</code> files written by <code>session.save()</code> or{" "}
          <code>pydantic-ai-viz demo --record</code>.
        </p>
        {!live && <DropZone pickLabel="choose another file" />}
      </EmptyState>
    </div>
  );
}

/** A route that points at nothing: a mistyped link, or an id from another session. */
export function NotFound({ what, detail }: { what: string; detail: string }) {
  return (
    <EmptyState
      icon="info"
      title={`${what} not found`}
      actions={
        <ButtonLink href={formatRoute({ name: "home" })} variant="primary">
          Go to the start
        </ButtonLink>
      }
    >
      <p className="m-0">{detail}</p>
    </EmptyState>
  );
}
