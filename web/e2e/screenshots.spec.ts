/**
 * The docs screenshots (spec section 7): eight PNGs at 1440x900, device scale 2.
 *
 *   PVIZ_RECORDINGS_DIR   directory holding the real demo recordings (support, research and
 *                         pipeline `.pviz.jsonl`, and `source.pviz.jsonl`, the demos read from
 *                         source), as written by `scripts/build_demo_site.py`.
 *                         Not set: the committed fixtures stand in, so the spec always runs.
 *   PVIZ_SCREENSHOTS_DIR  where to write. Default: `docs/assets/screenshots` when the real
 *                         recordings are given, else `web/test-results/screenshots` (ignored
 *                         by git), so a plain `pnpm e2e` never touches the docs images.
 *
 * Every shot is taken with the clock stopped at a computed position, so the images are the
 * same on every run.
 */
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import type { Envelope, Span } from "../src/wire";
import {
  canvasNode,
  finalSpans,
  fixturesDir,
  pauseAtBoundary,
  readRecording,
  rootSpan,
  rows,
  skipToEnd,
  slider,
  webDir,
} from "./support";

const real = Boolean(process.env.PVIZ_RECORDINGS_DIR);
const recordingsDir = real ? path.resolve(process.env.PVIZ_RECORDINGS_DIR ?? "") : fixturesDir;
const outDir = process.env.PVIZ_SCREENSHOTS_DIR
  ? path.resolve(process.env.PVIZ_SCREENSHOTS_DIR)
  : real
    ? path.resolve(webDir, "..", "docs", "assets", "screenshots")
    : path.join(webDir, "test-results", "screenshots");

type Recording = { file: string; events: Envelope[]; spans: Span[] };

/** The first of `names` that exists in the recordings directory. */
function load(...names: string[]): Recording {
  for (const name of names) {
    const file = path.join(recordingsDir, `${name}.pviz.jsonl`);
    if (existsSync(file)) {
      const events = readRecording(file);
      return { file: `${name}.pviz.jsonl`, events, spans: finalSpans(events) };
    }
  }
  throw new Error(`${recordingsDir} has none of: ${names.join(", ")} (.pviz.jsonl)`);
}

/** The real demo recording, or the fixture that shows the same thing. */
const support = () => load("support", "sample");
const research = () => load("research", "sample");
const pipeline = () => load("pipeline", "sample");

/**
 * The demos' source code as `pydantic-ai-viz view` reads it (`source.pviz.jsonl`, written by
 * `scripts/build_demo_site.py`), or the blueprint-only fixture, which the server keeps apart.
 */
function source(): { url: string; hash: string } {
  if (real) {
    const recording = load("source");
    // A tool that runs another agent: its schema from the signature and docstring, where it
    // is defined, and the delegation edge that reading its body found.
    const agent = blueprintId(recording, "triage");
    return { url: src(recording, ""), hash: `#/agents/${agent}?part=tool%3Aescalate_refund` };
  }
  return { url: "/fx/?src=recordings/blueprint/newsroom.pviz.jsonl", hash: "#/agents/a1" };
}

const agentRun = (recording: Recording): Span =>
  rootSpan(recording.events, (span) => span.kind === "agent_run");
const graphRun = (recording: Recording): Span =>
  rootSpan(recording.events, (span) => span.kind === "graph_run");

/** Id of the blueprint for an agent name (the last one emitted wins, as in the viewer). */
function blueprintId(recording: Recording, agentName: string | null): string {
  let id: string | null = null;
  for (const event of recording.events) {
    if (event.type === "blueprint.agent" && event.agent.name === agentName) id = event.agent.id;
  }
  if (!id) throw new Error(`${recording.file} has no blueprint for agent ${agentName}`);
  return id;
}

/**
 * How many "step forward" presses from the start reach the busiest moment of a run: the span
 * boundary with the most spans running at once, a streaming model request breaking ties.
 * Of equally busy moments the last one wins: at the first, the parallel spans have only just
 * started and their bars have no length yet. Boundaries are the run's distinct span start and
 * end times.
 */
function busiestBoundary(recording: Recording, traceId: string): number {
  const spans = recording.spans.filter((span) => span.trace_id === traceId);
  const times = new Set<number>();
  for (const span of spans) {
    times.add(span.start_ms);
    if (span.end_ms !== null) times.add(span.end_ms);
  }
  const boundaries = [...times].sort((a, b) => a - b);
  let best = 0;
  let bestScore = -1;
  boundaries.forEach((t, index) => {
    const running = spans.filter(
      (span) => span.start_ms <= t && (span.end_ms === null || span.end_ms > t),
    );
    const score = running.length * 2 + (running.some((s) => s.kind === "model_request") ? 1 : 0);
    if (score >= bestScore) {
      best = index;
      bestScore = score;
    }
  });
  return best;
}

const src = (recording: Recording, hash: string): string =>
  `/shots/?src=recordings/${recording.file}${hash}`;

async function openRun(page: Page, recording: Recording, traceId: string) {
  await page.goto(src(recording, `#/runs/${traceId}`));
  await expect(slider(page)).toBeVisible();
}

/** Lets entry fades and the canvas fit finish, parks the pointer, then writes the PNG. */
async function shoot(page: Page, name: string) {
  mkdirSync(outDir, { recursive: true });
  await page.mouse.move(0, 0);
  await page.locator("header h1").click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(outDir, name), animations: "disabled" });
}

test.describe.configure({ mode: "parallel" });

for (const scheme of ["dark", "light"] as const) {
  test.describe(`hero, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    // The whole story in one frame: a finished run on the blueprint, the waterfall under it,
    // and the retried tool call open in the inspector.
    test(`hero-${scheme}.png`, async ({ page }) => {
      const recording = support();
      const run = agentRun(recording);
      await openRun(page, recording, run.trace_id);
      await skipToEnd(page);
      // The retried attempt itself, not the call that succeeded after it (which is what a
      // click on the tool node selects).
      const retried = recording.spans.find(
        (span) => span.trace_id === run.trace_id && span.kind === "tool_call" && span.data.retry,
      );
      if (!retried) throw new Error(`${recording.file} has no retried tool call`);
      await page.locator(`[role="treeitem"][data-span="${retried.span_id}"]`).click();
      await expect(canvasNode(page, /^Tool .*retried/).first()).toBeVisible();
      // The retry is the first section of the call's details.
      const inspector = page.getByRole("complementary", { name: "Span details" });
      await expect(inspector.getByRole("tab", { name: "Details" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(inspector).toContainText("A retry is not a failure");
      await shoot(page, `hero-${scheme}.png`);
    });
  });

  test.describe(`view, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    // What `view` makes of a project: its files in the sidebar, an agent read from source with
    // its tools and delegate, and the inspector saying where each part is defined.
    test(`view-${scheme}.png`, async ({ page }) => {
      const { url, hash } = source();
      await page.goto(`${url}${hash}`);
      await expect(page.getByText("Read from source code")).toBeVisible();
      await expect(canvasNode(page, /^Agent /).first()).toBeVisible();
      const inspector = page.getByRole("complementary", { name: /details/ });
      await expect(inspector.getByText("From source")).toBeVisible();
      await shoot(page, `view-${scheme}.png`);
    });
  });
}

test.describe("docs images", () => {
  test.use({ colorScheme: "dark" });

  test("blueprint.png", async ({ page }) => {
    const recording = support();
    const run = agentRun(recording);
    await page.goto(src(recording, `#/agents/${blueprintId(recording, run.agent_name)}`));
    await expect(canvasNode(page, /^Agent /).first()).toBeVisible();
    await shoot(page, "blueprint.png");
  });

  test("run-timeline.png", async ({ page }) => {
    // Mid-run, paused where the most is happening at once.
    const recording = research();
    const run = agentRun(recording);
    await openRun(page, recording, run.trace_id);
    await pauseAtBoundary(page, busiestBoundary(recording, run.trace_id));
    await expect(rows(page).first()).toBeVisible();
    await shoot(page, "run-timeline.png");
  });

  test("inspector.png", async ({ page }) => {
    // The conversation of a finished run: messages, tool cards and the structured output.
    const recording = support();
    const run = agentRun(recording);
    await openRun(page, recording, run.trace_id);
    await skipToEnd(page);
    await rows(page).first().click();
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await expect(inspector.getByRole("tab", { name: "Conversation" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await shoot(page, "inspector.png");
  });

  test("graph.png", async ({ page }) => {
    const recording = pipeline();
    const run = graphRun(recording);
    await openRun(page, recording, run.trace_id);
    await skipToEnd(page);
    await expect(canvasNode(page, /^Step .*visited twice/).first()).toBeVisible();
    await shoot(page, "graph.png");
  });
});
