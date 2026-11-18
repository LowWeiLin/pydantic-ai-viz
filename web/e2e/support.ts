/** Shared helpers for the Playwright specs. */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { expect, type Locator, type Page } from "@playwright/test";
import type { Envelope, Span } from "../src/wire";

export const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const fixturesDir = path.join(webDir, "fixtures");

/** The hosted-demo mount over the committed fixtures (see `server.mjs`). */
export const FX = "/fx/";
/** A viewer with nothing next to it. */
export const BARE = "/bare/";

export const fixturePath = (name: string): string => path.join(fixturesDir, `${name}.pviz.jsonl`);

export function readRecording(file: string): Envelope[] {
  return readFileSync(file, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Envelope);
}

/** Every span of a recording in its final state, in the order they started. */
export function finalSpans(events: Envelope[]): Span[] {
  const spans = new Map<string, Span>();
  for (const event of events) {
    if (event.type === "span.started" || event.type === "span.ended") {
      spans.set(event.span.span_id, event.span);
    }
  }
  return [...spans.values()];
}

/** The first root span that matches, e.g. the run of the `triage` agent. */
export function rootSpan(events: Envelope[], match: (span: Span) => boolean): Span {
  const root = finalSpans(events).find((span) => span.parent_id === null && match(span));
  if (!root) throw new Error("the recording has no matching run");
  return root;
}

const sample = readRecording(fixturePath("sample"));
/** The sample fixture's two runs. */
export const TRIAGE = rootSpan(sample, (span) => span.name === "triage").trace_id;
export const PIPELINE = rootSpan(sample, (span) => span.kind === "graph_run").trace_id;
export const sampleSpan = (name: string): Span => {
  const span = finalSpans(sample).find((candidate) => candidate.name === name);
  if (!span) throw new Error(`the sample fixture has no span named ${name}`);
  return span;
};

/** The address that plays a fixture recording, optionally at a route. */
export const recording = (name: string, hash = "#/"): string =>
  `${FX}?src=recordings/${name}.pviz.jsonl${hash}`;

export const rows = (page: Page): Locator => page.getByRole("treeitem");
export const slider = (page: Page): Locator =>
  page.getByRole("slider", { name: "Replay position" });
export const canvasNode = (page: Page, name: string | RegExp): Locator =>
  page.locator(".react-flow").getByRole("button", { name });

/** Opens a run of a fixture recording and waits for its screen. */
export async function openRun(page: Page, name: string, traceId: string, span?: string) {
  await page.goto(recording(name, `#/runs/${traceId}${span ? `?span=${span}` : ""}`));
  await expect(slider(page)).toBeVisible();
}

/** Stops the replay at the end of the run: every span visible, nothing moving. */
export async function skipToEnd(page: Page) {
  await page.getByRole("button", { name: "Skip to end" }).click();
  await expect(page.getByRole("button", { name: "Replay", exact: true })).toBeVisible();
}

/** Pauses at the start of the run, then steps forward over `boundaries` span boundaries. */
export async function pauseAtBoundary(page: Page, boundaries: number) {
  await slider(page).press("Home");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  // Leave the slider: there the arrow keys nudge by time, not by span boundary.
  await page.locator("header h1").click();
  for (let i = 0; i < boundaries; i++) await page.keyboard.press("ArrowRight");
}

/** Records every request the page makes from now on. */
export function trackRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  return urls;
}

/** Console errors and uncaught exceptions; a spec asserts the list is empty at its end. */
export function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`uncaught: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

/**
 * Writes a single-file export the way `_export.py` does (spec 3.7): the boot token is replaced
 * with escaped JSON. Returns a `file://` address.
 */
export function writeEmbeddedExport(events: Envelope[], file: string): string {
  const viewer = path.join(webDir, "dist", "viewer.html");
  if (!existsSync(viewer)) throw new Error('dist/viewer.html not found. Run "pnpm build" first.');
  const token = `"__PVIZ_${"BOOT"}__"`;
  const boot = JSON.stringify({ mode: "embedded", events })
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replaceAll(String.fromCharCode(0x2028), "\\u2028")
    .replaceAll(String.fromCharCode(0x2029), "\\u2029");
  mkdirSync(path.dirname(file), { recursive: true });
  // A function replacement: `$` in the recording must not be read as a replace pattern.
  writeFileSync(
    file,
    readFileSync(viewer, "utf8").replace(token, () => boot),
  );
  return pathToFileURL(file).href;
}

/** Number of CSS animations and transitions that are running right now. */
export const runningAnimations = (page: Page): Promise<number> =>
  page.evaluate(
    () => document.getAnimations().filter((animation) => animation.playState === "running").length,
  );
