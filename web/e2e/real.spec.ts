/**
 * The built viewer over REAL demo recordings: what `scripts/build_demo_site.py` writes and the
 * hosted demo serves. The fixtures are written by hand from the wire contract; these recordings
 * come out of the Python backend, so this is where the two halves are proven to agree.
 *
 *   PVIZ_RECORDINGS_DIR   directory with support, research and pipeline `.pviz.jsonl` and their
 *                         `index.json`. Not set: every test here is skipped.
 *
 * Expected numbers are computed from the recording itself, never typed in, so a change to a demo
 * script does not break the spec unless the viewer shows something the recording does not say.
 */
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import type { Envelope, Span } from "../src/wire";
import {
  canvasNode,
  finalSpans,
  readRecording,
  rootSpan,
  rows,
  skipToEnd,
  slider,
  trackErrors,
  trackRequests,
} from "./support";

const dir = process.env.PVIZ_RECORDINGS_DIR;
const MOUNT = "/shots/";

type Recording = { name: string; events: Envelope[]; spans: Span[]; root: Span };

function load(name: string): Recording {
  const events = readRecording(path.join(path.resolve(dir ?? ""), `${name}.pviz.jsonl`));
  return { name, events, spans: finalSpans(events), root: rootSpan(events, () => true) };
}

const src = (recording: Recording, hash: string): string =>
  `${MOUNT}?src=recordings/${recording.name}.pviz.jsonl${hash}`;

async function openRun(page: Page, recording: Recording, span?: string) {
  const query = span ? `?span=${span}` : "";
  await page.goto(src(recording, `#/runs/${recording.root.trace_id}${query}`));
  await expect(slider(page)).toBeVisible();
}

/** The totals the top bar must show: summed over model requests, never over agent runs. */
function totals(recording: Recording): string {
  let input = 0;
  let output = 0;
  for (const span of recording.spans) {
    if (span.kind === "model_request" && span.data.usage) {
      input += span.data.usage.input_tokens;
      output += span.data.usage.output_tokens;
    }
  }
  const count = (n: number) => n.toLocaleString("en-US");
  return `${count(input)} in / ${count(output)} out`;
}

const subject = (page: Page, name: string) =>
  page.getByRole("group", { name: "Canvas subject" }).getByRole("button", { name });

test.describe("real demo recordings", () => {
  test.skip(!dir, "PVIZ_RECORDINGS_DIR is not set; build it with scripts/build_demo_site.py");

  test("the gallery lists the three demos and plays one, asking for nothing else", async ({
    page,
    baseURL,
  }) => {
    const errors = trackErrors(page);
    const requests = trackRequests(page);
    await page.goto(MOUNT);
    await expect(page.getByRole("heading", { name: "Recordings" })).toBeVisible();
    for (const title of ["Support triage", "Research team", "Article pipeline"]) {
      await expect(page.getByRole("link", { name: `Play ${title}` })).toBeVisible();
    }
    await page.getByRole("link", { name: "Play Research team" }).click();
    await page.waitForURL(/\?src=recordings%2Fresearch\.pviz\.jsonl#\/runs\//);
    await expect(page.getByRole("heading", { name: "Research team" })).toBeVisible();
    // It replays by itself. A recording made with no delays is over before it can be seen
    // playing, so either state is right; the spans show up in both.
    await expect(page.getByRole("button", { name: /^(Pause|Replay)$/ })).toBeVisible();
    await expect.poll(() => rows(page).count()).toBeGreaterThan(0);
    await page.waitForLoadState("networkidle");
    expect([...new Set(requests)].sort()).toEqual(
      [
        `${baseURL}${MOUNT}`,
        `${baseURL}${MOUNT}?src=recordings%2Fresearch.pviz.jsonl`,
        `${baseURL}${MOUNT}recordings/index.json`,
        `${baseURL}${MOUNT}recordings/research.pviz.jsonl`,
      ].sort(),
    );
    expect(errors).toEqual([]);
  });

  test("support: a retry, three parallel tools, a delegation and structured output", async ({
    page,
  }) => {
    const errors = trackErrors(page);
    const recording = load("support");
    await openRun(page, recording);
    await skipToEnd(page);
    await expect(rows(page)).toHaveCount(recording.spans.length);
    await expect(canvasNode(page, /^Tool get_order, retried once, 2 calls, OK$/)).toBeVisible();
    await expect(canvasNode(page, /^Tool recent_tickets, 1 call, OK$/)).toBeVisible();
    await expect(canvasNode(page, /^Tool search_kb, 1 call, OK$/)).toBeVisible();
    await expect(canvasNode(page, /^Agent refunds, 1 call, OK/)).toBeVisible();
    await expect(canvasNode(page, /^Output of triage: Resolution.*, ready$/)).toBeVisible();
    await expect(page.locator("main")).toContainText(totals(recording));
    await expect(page.locator("main")).toContainText("1 retry");

    // The retried call: amber, not red, and the inspector explains what happened.
    const retried = recording.spans.find(
      (span) => span.kind === "tool_call" && span.data.retry !== null,
    );
    await page.locator(`[role="treeitem"][data-span="${retried?.span_id}"]`).click();
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await inspector.getByRole("tab", { name: "Error / Retry" }).click();
    await expect(inspector).toContainText("1 of 2 retries");
    await expect(inspector).toContainText("ToolRetryError");

    // The structured output of the run, as data.
    await rows(page).first().click();
    await inspector.getByRole("tab", { name: "Output" }).click();
    await expect(inspector).toContainText("refund_eur");
    expect(errors).toEqual([]);
  });

  test("support: a response streams from the recorded deltas", async ({ page }) => {
    const recording = load("support");
    const request = recording.spans.find(
      (span) => span.kind === "model_request" && span.parent_id === recording.root.span_id,
    );
    if (!request) throw new Error("the support recording has no model request");
    const deltas = recording.events.filter(
      (event) => event.type === "stream.delta" && event.span_id === request.span_id,
    );
    expect(deltas.length).toBeGreaterThan(10);
    const text = deltas
      .filter((event) => event.type === "stream.delta" && event.part_kind === "text")
      .map((event) => (event.type === "stream.delta" ? event.delta : ""))
      .join("");
    await openRun(page, recording, request.span_id);
    await slider(page).press("Home");

    // Put the clock in the middle of the request's stream by clicking the ruler there.
    const middle = deltas[Math.floor(deltas.length / 2)];
    const end = recording.root.end_ms ?? recording.root.start_ms;
    const fraction =
      ((middle?.ts ?? 0) - recording.root.start_ms) / (end - recording.root.start_ms);
    const box = await slider(page).boundingBox();
    if (!box) throw new Error("the ruler has no box");
    await page.mouse.click(box.x + box.width * fraction, box.y + 8);

    const inspector = page.getByRole("complementary", { name: "Span details" });
    const streaming = inspector.getByRole("region", { name: "Response so far" });
    await expect(streaming).toContainText("streaming");
    await expect(streaming).toContainText(text.slice(0, 12));
    await skipToEnd(page);
    await expect(streaming).toBeHidden();
    await expect(inspector).toContainText(text.trim());
  });

  test("research: one sub-agent run three times in parallel, then a writer", async ({ page }) => {
    const errors = trackErrors(page);
    const recording = load("research");
    await openRun(page, recording);
    await skipToEnd(page);
    await expect(rows(page)).toHaveCount(recording.spans.length);
    await expect(canvasNode(page, /^Tool search, 3 calls, OK$/)).toBeVisible();
    await expect(canvasNode(page, /^Agent searcher, 3 calls, OK/)).toBeVisible();
    await expect(canvasNode(page, /^Agent writer, 1 call, OK/)).toBeVisible();
    // Sub-agent tokens are counted once: the total is the sum over model requests.
    await expect(page.locator("main")).toContainText(totals(recording));
    await subject(page, "searcher").click();
    await expect(canvasNode(page, /^Tool search_library, 3 calls, OK$/)).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("pipeline: a graph run with a loop and agent runs inside its steps", async ({ page }) => {
    const errors = trackErrors(page);
    const recording = load("pipeline");
    expect(recording.root.kind).toBe("graph_run");
    await openRun(page, recording);
    await skipToEnd(page);
    await expect(rows(page)).toHaveCount(recording.spans.length);
    await expect(canvasNode(page, "Step Write draft, visited twice, done")).toBeVisible();
    await expect(canvasNode(page, "Step Review, visited twice, done")).toBeVisible();
    await expect(canvasNode(page, "Step Publish, visited once, done")).toBeVisible();
    await expect(page.locator("main")).toContainText(totals(recording));

    // The agent run sits under the graph node that started it.
    const write = recording.spans.find(
      (span) => span.kind === "graph_node" && span.name === "write",
    );
    const writer = recording.spans.find((span) => span.parent_id === write?.span_id);
    expect(writer?.kind).toBe("agent_run");
    const nodeRow = page.locator(`[role="treeitem"][data-span="${write?.span_id}"]`);
    const agentRow = page.locator(`[role="treeitem"][data-span="${writer?.span_id}"]`);
    await expect(nodeRow).toHaveAttribute("aria-level", "2");
    await expect(agentRow).toHaveAttribute("aria-level", "3");

    await subject(page, "writer").click();
    await expect(canvasNode(page, /^Agent writer, 2 calls, OK/)).toBeVisible();
    await subject(page, "critic").click();
    await expect(canvasNode(page, /^Tool check_draft, 2 calls, OK$/)).toBeVisible();
    expect(errors).toEqual([]);
  });
});
