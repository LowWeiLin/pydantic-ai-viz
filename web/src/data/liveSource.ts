/**
 * Live mode: one EventSource on the server's SSE stream. Envelopes are queued and handed over
 * once per animation frame, so a burst of token deltas costs one render, not hundreds.
 *
 * The browser reconnects by itself and sends `Last-Event-ID`. If the stream is closed for good
 * (the server answered with an error), a fresh EventSource is opened after a short wait; the
 * server then replays the log and the reducer drops what it has already seen.
 */
import type { Envelope } from "../wire";

export type Connection = "connecting" | "open" | "reconnecting";

export type LiveHandlers = {
  onEvents: (envelopes: Envelope[]) => void;
  onConnection: (state: Connection) => void;
};

/** How long a batch may wait when no frame fires (a background tab). */
const MAX_BATCH_DELAY_MS = 250;
const REOPEN_DELAY_MS = 2000;

/** Connects and returns a function that disconnects. */
export function startLiveSource(url: string, handlers: LiveHandlers): () => void {
  let source: EventSource | null = null;
  let stopped = false;
  let queue: Envelope[] = [];
  let frame: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let reopen: ReturnType<typeof setTimeout> | null = null;

  const flush = (): void => {
    if (frame !== null) cancelAnimationFrame(frame);
    if (timer !== null) clearTimeout(timer);
    frame = null;
    timer = null;
    if (stopped || queue.length === 0) return;
    const batch = queue;
    queue = [];
    handlers.onEvents(batch);
  };

  const schedule = (): void => {
    if (frame !== null || timer !== null) return;
    frame = requestAnimationFrame(flush);
    timer = setTimeout(flush, MAX_BATCH_DELAY_MS);
  };

  const connect = (): void => {
    if (stopped) return;
    const es = new EventSource(url);
    source = es;
    es.onopen = () => handlers.onConnection("open");
    es.onmessage = (message: MessageEvent<string>) => {
      try {
        queue.push(JSON.parse(message.data) as Envelope);
      } catch {
        return; // A malformed frame is skipped; the reducer validates the rest.
      }
      schedule();
    };
    es.onerror = () => {
      if (stopped) return;
      handlers.onConnection("reconnecting");
      if (es.readyState === EventSource.CLOSED) {
        es.close();
        reopen = setTimeout(connect, REOPEN_DELAY_MS);
      }
    };
  };

  handlers.onConnection("connecting");
  connect();

  return () => {
    stopped = true;
    source?.close();
    if (frame !== null) cancelAnimationFrame(frame);
    if (timer !== null) clearTimeout(timer);
    if (reopen !== null) clearTimeout(reopen);
    queue = [];
  };
}
