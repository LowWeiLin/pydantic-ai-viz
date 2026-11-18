/**
 * One polite live region for the whole app. Run status changes are announced through it, at
 * most one message per second so a burst of events does not flood a screen reader.
 */
import { useEffect, useRef, useState } from "react";
import { runs } from "../data/selectors";
import { useViewer } from "../data/store";

const MIN_GAP_MS = 1000;

type Listener = (message: string) => void;
let listener: Listener | null = null;
let pending: string | null = null;
let lastAt = 0;
let timer: ReturnType<typeof setTimeout> | null = null;

function flush(): void {
  timer = null;
  if (pending === null) return;
  lastAt = Date.now();
  listener?.(pending);
  pending = null;
}

/** Queues a message. If several arrive within a second, only the latest is spoken. */
export function announce(message: string): void {
  pending = message;
  if (timer !== null) return;
  timer = setTimeout(flush, Math.max(0, MIN_GAP_MS - (Date.now() - lastAt)));
}

/** Renders the live region and announces runs as they start and finish. Mount once. */
export function Announcer() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    listener = setMessage;
    return () => {
      listener = null;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending = null;
    };
  }, []);

  const data = useViewer((state) => state.data);
  const live = useViewer((state) => state.source.kind === "live");
  const known = useRef(new Map<string, string>());
  useEffect(() => {
    const seen = known.current;
    for (const run of runs(data)) {
      const before = seen.get(run.traceId);
      seen.set(run.traceId, run.status);
      // Only live changes are news; a loaded recording arrives already finished.
      if (!live || before === run.status) continue;
      if (run.status === "running") announce(`Run ${run.name} started`);
      else announce(`Run ${run.name} ${run.status === "ok" ? "finished" : "failed"}`);
    }
  }, [data, live]);

  return (
    <div role="status" aria-live="polite" className="sr-only">
      {message}
    </div>
  );
}
