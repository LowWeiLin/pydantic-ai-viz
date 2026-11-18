// The server the Playwright specs run against. It serves the BUILT viewer (dist/viewer.html)
// the way the three real hosts do, with no Python involved:
//
//   /fx/            hosted demo over web/fixtures (a recordings/index.json exists: gallery)
//   /shots/         hosted demo over PVIZ_RECORDINGS_DIR, or web/fixtures when it is not set
//   /bare/          a viewer with nothing next to it (no index: the welcome screen)
//   /live/          a live session: boot slot filled like the Python server does, and an SSE
//                   stream that plays the sample fixture's first run in compressed time
//   /live-hold-N/   the same, but the stream stops (and stays open) after N events
//   /view/          what `pydantic-ai-viz view` serves: the same boot slot, and a stream that
//                   sends the blueprint-only fixture and then stays open with nothing to add
//
// Usage: node e2e/server.mjs   (port from PVIZ_E2E_PORT, default 4319)
import { randomUUID } from "node:crypto";
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const viewerFile = path.join(webDir, "dist", "viewer.html");
const fixturesDir = path.join(webDir, "fixtures");
const recordingsDir = process.env.PVIZ_RECORDINGS_DIR
  ? path.resolve(process.env.PVIZ_RECORDINGS_DIR)
  : fixturesDir;
const port = Number(process.env.PVIZ_E2E_PORT ?? 4319);

// Assembled at run time, like copy-viewer.mjs, so the token is not spelled out in the repo twice.
const BOOT_TOKEN = `"__PVIZ_${"BOOT"}__"`;

if (!existsSync(viewerFile)) {
  console.error(`e2e server: ${viewerFile} not found. Run "pnpm build" first.`);
  process.exit(1);
}

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json",
  ".jsonl": "application/x-ndjson",
};

function send(res, status, body, type = "application/json") {
  res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(body);
}

function sendFile(res, file) {
  if (!existsSync(file) || !statSync(file).isFile()) return send(res, 404, '{"error":"not found"}');
  res.writeHead(200, {
    "Content-Type": TYPES[path.extname(file)] ?? "application/octet-stream",
    "Cache-Control": "no-store",
  });
  createReadStream(file).pipe(res);
}

/** A recordings directory without an index still gets a gallery: one card per file. */
function sendRecording(res, dir, name) {
  const file = path.join(dir, path.basename(name));
  if (name !== "index.json" || existsSync(file)) return sendFile(res, file);
  const recordings = readdirSync(dir)
    .filter((entry) => entry.endsWith(".pviz.jsonl"))
    .sort()
    .map((entry) => {
      const id = entry.replace(/\.pviz\.jsonl$/, "");
      return { id, title: id, description: `Recording ${entry}`, src: entry };
    });
  send(res, 200, JSON.stringify({ recordings }));
}

// ---------- the live mock ----------

const readEvents = (...file) =>
  readFileSync(path.join(fixturesDir, ...file), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

const sample = readEvents("sample.pviz.jsonl");
/** A blueprint-only session: all of it is "header", and no run ever follows. */
const blueprints = readEvents("blueprint", "newsroom.pviz.jsonl");
const firstTrace = sample.find((event) => event.type === "span.started").span.trace_id;
/** Session start, the blueprints, and the first run: what a demo session emits. */
const script = sample.filter((event) => {
  const trace = event.span?.trace_id ?? event.trace_id;
  return trace === undefined || trace === firstTrace;
});
const firstSpan = script.findIndex((event) => event.type === "span.started");
const header = script.slice(0, firstSpan);
const run = script.slice(firstSpan);

/** The same run again under fresh ids, `shift` milliseconds later. */
function again(events, n) {
  const hex = n.toString(16);
  const id = (value) => (typeof value === "string" ? hex + value.slice(hex.length) : value);
  const shift = n * 20_000;
  return events.map((event) => {
    const copy = structuredClone(event);
    copy.ts += shift;
    if (copy.span) {
      const span = copy.span;
      span.trace_id = id(span.trace_id);
      span.span_id = id(span.span_id);
      span.parent_id = id(span.parent_id);
      span.start_ms += shift;
      if (span.end_ms !== null) span.end_ms += shift;
    } else if (copy.type === "stream.delta") {
      copy.trace_id = id(copy.trace_id);
      copy.span_id = id(copy.span_id);
    }
    return copy;
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sessions = new Map();

function liveSession(holdAt, header, run) {
  const log = [];
  const clients = new Set();
  const state = { busy: false, started: false, runs: 0, down: false };
  const emit = (event) => {
    const envelope = { ...event, seq: log.length };
    log.push(envelope);
    for (const client of clients) writeEvent(client, envelope);
  };
  const play = async (events) => {
    state.busy = true;
    let previous = events[0]?.ts ?? 0;
    for (const event of events) {
      if (holdAt !== null && log.length >= holdAt) return; // Held: the run never finishes.
      // A quarter of real time, and never a long silence: the specs should not wait on sleeps.
      if (holdAt === null) await sleep(Math.min(120, (event.ts - previous) / 4));
      previous = event.ts;
      emit(event);
    }
    state.busy = false;
  };
  return {
    connect(res, lastId) {
      if (state.down) return send(res, 503, '{"error":"stopped"}');
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
      res.write("retry: 1000\n\n");
      for (const envelope of log) if (envelope.seq > lastId) writeEvent(res, envelope);
      clients.add(res);
      res.on("close", () => clients.delete(res));
      if (!state.started) {
        // Like `pydantic-ai-viz demo`: the scenario starts when the first client connects.
        state.started = true;
        for (const event of header) emit(event);
        if (run.length > 0) void play(run);
      }
    },
    /** What a stopped server looks like: open streams end and new ones are refused. */
    drop(res) {
      state.down = true;
      for (const client of clients) client.destroy();
      send(res, 200, '{"dropped":true}');
    },
    rerun(res) {
      if (state.busy) return send(res, 409, '{"accepted":false,"reason":"running"}');
      state.runs += 1;
      void play(again(run, state.runs));
      send(res, 202, '{"accepted":true}');
    },
  };
}

function writeEvent(res, envelope) {
  res.write(`id: ${envelope.seq}\ndata: ${JSON.stringify(envelope)}\n\n`);
}

function sendLiveViewer(res, holdAt, blueprintOnly = false) {
  const key = randomUUID();
  sessions.set(
    key,
    blueprintOnly ? liveSession(null, blueprints, []) : liveSession(holdAt, header, run),
  );
  const boot = JSON.stringify({
    mode: "live",
    events_url: `api/v1/events?s=${key}`,
    export_html_url: "api/v1/export.html",
    export_jsonl_url: "api/v1/export.jsonl",
    // Only the demo can run again; `view` has nothing to run.
    rerun_url: blueprintOnly ? null : `api/v1/rerun?s=${key}`,
  });
  send(res, 200, readFileSync(viewerFile, "utf8").replace(BOOT_TOKEN, boot), TYPES[".html"]);
}

// ---------- routing ----------

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const live = /^\/(?:view|live(?:-hold-(\d+))?)\/(.*)$/.exec(url.pathname);
  if (live) {
    const session = sessions.get(url.searchParams.get("s") ?? "");
    if (live[2] === "") {
      return sendLiveViewer(
        res,
        live[1] ? Number(live[1]) : null,
        url.pathname.startsWith("/view/"),
      );
    }
    if (live[2] === "api/v1/events" && session) {
      const lastId = Number(req.headers["last-event-id"] ?? -1);
      return session.connect(res, Number.isFinite(lastId) ? lastId : -1);
    }
    if (live[2] === "api/v1/rerun" && session && req.method === "POST") return session.rerun(res);
    // Test hook, not part of the real server.
    if (live[2] === "api/v1/drop" && session && req.method === "POST") return session.drop(res);
    return send(res, 404, '{"error":"not found"}');
  }
  const mount = /^\/(fx|shots|bare)\/(.*)$/.exec(url.pathname);
  if (!mount) return send(res, 404, '{"error":"not found"}');
  const [, name, rest] = mount;
  if (rest === "") return sendFile(res, viewerFile);
  const recording = /^recordings\/([^/]+)$/.exec(rest);
  if (recording && name !== "bare") {
    return sendRecording(res, name === "shots" ? recordingsDir : fixturesDir, recording[1]);
  }
  // The blueprint-only fixtures, which are not recordings and are in no index.
  const blueprint = /^recordings\/blueprint\/([^/]+)$/.exec(rest);
  if (blueprint && name === "fx") {
    return sendFile(res, path.join(fixturesDir, "blueprint", path.basename(blueprint[1])));
  }
  send(res, 404, '{"error":"not found"}');
});

server.listen(port, "127.0.0.1", () => {
  console.log(`e2e server: http://127.0.0.1:${port}/ (recordings from ${recordingsDir})`);
});
