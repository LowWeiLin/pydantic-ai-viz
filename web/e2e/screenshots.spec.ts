/**
 * The docs screenshots (spec section 7): thirteen PNGs at 1440x900, device scale 2.
 *
 *   PVIZ_RECORDINGS_DIR   directory holding the real demo recordings (support, research, pipeline,
 *                         capabilities and operations `.pviz.jsonl`, and `source.pviz.jsonl`, the demos read from
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
import type { AgentBlueprint, Envelope, Span } from "../src/wire";
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
const operations = () => load("operations", "approvals");
/** The tool that needs approval: the demo's, or the approvals fixture's. */
const approvedTool = real ? "restart_service" : "delete_file";

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

/**
 * The capabilities demo's agent, its house-style skill picked: the real recording, or the
 * blueprint-only fixture, which the server keeps apart.
 */
function capabilities(): string {
  const recording: Recording = real
    ? load("capabilities")
    : {
        file: "blueprint/capabilities.pviz.jsonl",
        events: readRecording(path.join(fixturesDir, "blueprint", "capabilities.pviz.jsonl")),
        spans: [],
      };
  const agent = blueprint(recording, "briefing");
  const index = agent.capabilities.findIndex((capability) => capability.name === "Skills");
  const hash = `#/agents/${agent.id}?part=skill%3A${index}%3Ahouse-style`;
  return real ? src(recording, hash) : `/fx/?src=recordings/${recording.file}${hash}`;
}

/**
 * The on-call agent of the operations demo, its Hooks capability picked: the real recording, or
 * the approvals fixture's agent, which has a tool that needs approval but no hooks.
 */
function operationsAgent(recording: Recording): string {
  const agent = blueprint(recording, real ? "oncall" : "ops");
  const index = agent.capabilities.findIndex((capability) => capability.name === "Hooks");
  return src(recording, `#/agents/${agent.id}${index < 0 ? "" : `?part=capability%3A${index}`}`);
}

const agentRun = (recording: Recording): Span =>
  rootSpan(recording.events, (span) => span.kind === "agent_run");
const graphRun = (recording: Recording): Span =>
  rootSpan(recording.events, (span) => span.kind === "graph_run");

/** The blueprint for an agent name (the last one emitted wins, as in the viewer). */
function blueprint(recording: Recording, agentName: string | null): AgentBlueprint {
  let found: AgentBlueprint | null = null;
  for (const event of recording.events) {
    if (event.type === "blueprint.agent" && event.agent.name === agentName) found = event.agent;
  }
  if (!found) throw new Error(`${recording.file} has no blueprint for agent ${agentName}`);
  return found;
}

const blueprintId = (recording: Recording, agentName: string | null): string =>
  blueprint(recording, agentName).id;

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

  test.describe(`capabilities, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    // An agent built from capabilities: grouped by kind under its card, the toolsets they give
    // linked to them, the skills under the capability that offers them, and the picked skill's
    // instructions in the inspector.
    test(`capabilities-${scheme}.png`, async ({ page }) => {
      await page.goto(capabilities());
      await expect(canvasNode(page, /^Skill house-style of Skills/)).toBeVisible();
      const inspector = page.getByRole("complementary", { name: /details/ });
      await expect(inspector.getByRole("heading", { name: "house-style" })).toBeVisible();
      await shoot(page, `capabilities-${scheme}.png`);
    });
  });

  test.describe(`operations, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    // An agent that delegates through SubAgents, is watched by hooks and has a tool that needs
    // approval: the delegation edge, the hook rows and the "needs approval" tag on one canvas.
    test(`operations-${scheme}.png`, async ({ page }) => {
      await page.goto(operationsAgent(operations()));
      await expect(
        canvasNode(page, new RegExp(`^Tool ${approvedTool}, needs approval`)),
      ).toBeVisible();
      if (real) {
        await expect(
          canvasNode(page, /^Hook before_tool_execute of Hooks: guard_restart/),
        ).toBeVisible();
        await expect(canvasNode(page, /^Agent log_reader/)).toBeVisible();
      }
      await shoot(page, `operations-${scheme}.png`);
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

  test("approvals.png", async ({ page }) => {
    // A run that ended waiting for a human to approve its restarts, and the link to the run
    // that resumes it.
    const recording = operations();
    const waited = recording.spans.find(
      (span) => span.kind === "agent_run" && span.data.deferred.length > 0,
    );
    if (!waited) throw new Error(`${recording.file} has no run that waits for approval`);
    await openRun(page, recording, waited.trace_id);
    await skipToEnd(page);
    await expect(
      canvasNode(page, new RegExp(`^Tool ${approvedTool}, needs approval, [0-9]+ calls?, waiting`)),
    ).toBeVisible();
    await shoot(page, "approvals.png");
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
