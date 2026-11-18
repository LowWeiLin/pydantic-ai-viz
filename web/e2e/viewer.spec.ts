/**
 * End-to-end behaviour of the built viewer over the committed fixtures: the things jsdom
 * cannot prove (real layout, pointer input, the canvas, navigation, the network).
 */
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import {
  BARE,
  canvasNode,
  FX,
  fixturePath,
  openRun,
  PIPELINE,
  pauseAtBoundary,
  readRecording,
  recording,
  rows,
  sampleSpan,
  skipToEnd,
  slider,
  TRIAGE,
  trackErrors,
  trackRequests,
  writeEmbeddedExport,
} from "./support";

const TRIAGE_SPANS = 14;
/** Spans started in the first 56 events of the live mock (`/live-hold-56/`). */
const HELD_SPANS = 8;

const runLink = (page: Page, name: string) =>
  page.getByRole("navigation", { name: /^Runs/ }).getByRole("link", { name });

const theme = (page: Page): Promise<string> =>
  page.evaluate(() => document.documentElement.getAttribute("data-theme") ?? "system");
const background = (page: Page): Promise<string> =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

/** Drops a file on the page the way a browser does: dragenter, dragover, drop with real files. */
async function dropFile(page: Page, name: string, text: string) {
  const transfer = await page.evaluateHandle(
    ([fileName, content]) => {
      const data = new DataTransfer();
      data.items.add(new File([content], fileName, { type: "application/x-ndjson" }));
      return data;
    },
    [name, text] as const,
  );
  for (const type of ["dragenter", "dragover", "drop"]) {
    await page.dispatchEvent("body", type, { dataTransfer: transfer });
    if (type === "dragover") {
      await expect(page.getByText("Drop to open the recording")).toBeVisible();
    }
  }
}

test.describe("gallery and loading", () => {
  test("the gallery opens a recording, which replays from the start", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto(FX);
    await expect(page.getByRole("heading", { name: "Recordings" })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Play / })).toHaveCount(3);

    await page.getByRole("link", { name: "Play Support triage and a graph pipeline" }).click();
    await page.waitForURL(/\?src=recordings%2Fsample\.pviz\.jsonl#\/runs\//);
    await expect(page.getByText("Recording", { exact: true })).toBeVisible();
    // Auto-play: the button offers Pause, and rows keep arriving.
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
    const before = await rows(page).count();
    await expect.poll(() => rows(page).count()).toBeGreaterThan(before);

    // The gallery stays one click away and marks what is loaded.
    await page.getByRole("link", { name: "Gallery" }).click();
    await expect(page.getByText("loaded", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("a dropped file loads", async ({ page }) => {
    await page.goto(BARE);
    await expect(
      page.getByRole("heading", { name: "See what your agents are, and what they do" }),
    ).toBeVisible();
    await dropFile(page, "failed.pviz.jsonl", readFileSync(fixturePath("failed"), "utf8"));
    await page.waitForURL(/#\/runs\//);
    await expect(page.getByRole("heading", { name: "Nightly digest" })).toBeVisible();
    await expect(page.getByText("Drop to open the recording")).toBeHidden();
  });

  test("a file picked from disk loads, and replaces what was open", async ({ page }) => {
    await page.goto(recording("redacted"));
    await expect(page.getByRole("heading", { name: "Claims intake" })).toBeVisible();
    await page.getByRole("link", { name: "Gallery" }).click();
    await page.getByLabel("Recording file").setInputFiles(fixturePath("sample"));
    await page.waitForURL(/#\/runs\//);
    await expect(page.getByRole("heading", { name: "Sample session" })).toBeVisible();
    await expect(page.getByRole("link", { name: /triage/ }).first()).toBeVisible();
  });

  test("a broken recording says which file, which line and why", async ({ page }) => {
    await page.goto(BARE);
    const good = readFileSync(fixturePath("redacted"), "utf8").split("\n");
    const text = [...good.slice(0, 3), '{"seq": 3, "ts": ', ...good.slice(3)].join("\n");
    await page.getByLabel("Recording file").setInputFiles({
      name: "broken.pviz.jsonl",
      mimeType: "text/plain",
      buffer: Buffer.from(text),
    });
    const alert = page.getByRole("alert");
    await expect(alert.getByRole("heading")).toHaveText("This recording could not be loaded");
    await expect(alert.getByText("broken.pviz.jsonl")).toBeVisible();
    await expect(alert.locator("dt", { hasText: "Line" }).locator("+ dd")).toHaveText("4");
    // The way forward is on the same screen.
    await alert.getByLabel("Recording file").setInputFiles(fixturePath("redacted"));
    await expect(page.getByRole("heading", { name: "Claims intake" })).toBeVisible();
  });

  test("a recording that is not there fails with its address", async ({ page }) => {
    await page.goto(`${FX}?src=recordings/nope.pviz.jsonl`);
    const alert = page.getByRole("alert");
    await expect(alert.getByText("nope.pviz.jsonl")).toBeVisible();
    await expect(alert.getByRole("link", { name: "Back to the gallery" })).toBeVisible();
  });
});

test.describe("replay", () => {
  test("playback advances and node states change", async ({ page }) => {
    const errors = trackErrors(page);
    await openRun(page, "sample", TRIAGE);
    await pauseAtBoundary(page, 0);
    // At the start nothing has been called and the output is not ready.
    await expect(canvasNode(page, /^Tool get_order, not used yet$/)).toBeVisible();
    await expect(canvasNode(page, /^Output of triage.*not ready yet$/)).toBeVisible();
    await expect(rows(page)).toHaveCount(1);

    await page.keyboard.press("]");
    await page.keyboard.press("]");
    await page.keyboard.press("]");
    await expect(page.getByRole("button", { name: "8x speed" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.getByRole("button", { name: "Play", exact: true }).click();
    // The states arrive in run order: the retry, the recovery, the delegation, the output.
    await expect(canvasNode(page, /^Tool get_order, retried once/)).toBeVisible();
    await expect(canvasNode(page, /^Tool get_order, retried once, 2 calls, OK/)).toBeVisible();
    await expect(canvasNode(page, /^Agent refunds, 1 call/)).toBeVisible();
    await expect(canvasNode(page, /^Output of triage.*, ready$/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Replay", exact: true })).toBeVisible();
    await expect(rows(page)).toHaveCount(TRIAGE_SPANS);
    expect(errors).toEqual([]);
  });

  test("the span list follows the newest row until it is scrolled away from", async ({
    page,
  }, testInfo) => {
    // The sample's first run with its first tool call repeated: one new row every quarter of a
    // second for fifteen seconds, so there are always more rows to come while the test looks.
    const sample = readRecording(fixturePath("sample"));
    const first = sample.findIndex((event) => event.type === "span.started");
    const tool = sampleSpan("get_customer");
    const root = sample[first];
    if (root?.type !== "span.started") throw new Error("the sample fixture changed shape");
    const events = sample.slice(0, first + 1);
    for (let call = 0; call < 60; call++) {
      const span = {
        ...tool,
        span_id: (call + 1).toString(16).padStart(16, "0"),
        start_ms: root.span.start_ms + 100 + call * 250,
        end_ms: root.span.start_ms + 200 + call * 250,
      };
      const started = { ...span, end_ms: null, status: "running" as const };
      events.push({ seq: events.length, ts: span.start_ms, type: "span.started", span: started });
      events.push({ seq: events.length, ts: span.end_ms, type: "span.ended", span });
    }
    const file = writeEmbeddedExport(events, testInfo.outputPath("many-rows.html"));
    await page.goto(`${file}#/runs/${root.span.trace_id}`);
    const list = page.getByRole("tree").locator("xpath=ancestor::div[2]");
    const position = () =>
      list.evaluate((el) => ({
        top: el.scrollTop,
        fromBottom: el.scrollHeight - el.scrollTop - el.clientHeight,
      }));
    // More rows than fit: while the replay runs, the newest one is in view.
    await expect.poll(() => rows(page).count()).toBeGreaterThanOrEqual(10);
    expect((await position()).top).toBeGreaterThan(0);
    expect((await position()).fromBottom).toBeLessThan(2);

    // Scroll up to read the first rows: new spans must not pull the list back down.
    await list.hover();
    await page.mouse.wheel(0, -2000);
    // Well away from the bottom. Not exactly at the top: a row that lands while the wheel is
    // still turning shifts the end point by its height.
    await expect.poll(async () => (await position()).fromBottom).toBeGreaterThan(100);
    const { top } = await position();
    const before = await rows(page).count();
    await expect.poll(() => rows(page).count()).toBeGreaterThan(before);
    expect((await position()).top).toBe(top);
  });

  test("scrubbing back hides later spans", async ({ page }) => {
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    await expect(rows(page)).toHaveCount(TRIAGE_SPANS);
    await canvasNode(page, /^Tool get_order, retried once/).click();
    await expect(page).toHaveURL(/\?span=/);
    await expect(page.getByRole("treeitem", { selected: true })).toContainText("get_order");

    // Drag the ruler from the end back to a fifth of the run.
    const box = await slider(page).boundingBox();
    if (!box) throw new Error("the ruler has no box");
    await page.mouse.move(box.x + box.width - 2, box.y + 8);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.2, box.y + 8, { steps: 8 });
    await page.mouse.up();

    const visible = await rows(page).count();
    expect(visible).toBeGreaterThan(0);
    expect(visible).toBeLessThan(TRIAGE_SPANS);
    // The selected span has not started at that time: the inspector says so, and can recover.
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await expect(inspector.getByRole("heading", { name: "Not started yet" })).toBeVisible();
    await expect(canvasNode(page, /^Output of triage.*not ready yet$/)).toBeVisible();
    await expect(slider(page)).toHaveAttribute("aria-valuetext", /^1\.\d+ s of 6\.\d+ s$/);
  });

  test("a streaming response is assembled from deltas, then replaced by the result", async ({
    page,
  }) => {
    await openRun(page, "sample", TRIAGE);
    await pauseAtBoundary(page, 1);
    // One boundary in: the first model request has started and nothing has streamed yet.
    await page.keyboard.press("j");
    await page.keyboard.press("j");
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await expect(inspector.getByRole("heading", { name: "scripted-support" })).toBeVisible();
    await expect(inspector.getByText("Running", { exact: true })).toBeVisible();
    // Nudge the clock into the stream: each press moves 2% of the run.
    for (let i = 0; i < 4; i++) await slider(page).press("ArrowRight");
    const streaming = inspector.getByRole("region", { name: "Response so far" });
    await expect(streaming).toContainText("streaming");
    await expect(streaming).toContainText("I'll pull up");
    await expect(streaming).not.toContainText("get_order");
    await skipToEnd(page);
    await expect(streaming).toBeHidden();
    await expect(inspector.getByRole("tab", { name: "Response" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(inspector).toContainText("get_order");
  });

  test("a long streaming response stays on its newest text until it is scrolled away from", async ({
    page,
  }, testInfo) => {
    // The sample run, cut off inside its first model request, which then streams 750 lines over
    // fifteen seconds: far more than the panel holds, and still coming while the test looks.
    const sample = readRecording(fixturePath("sample"));
    const start = sample.findIndex(
      (event) => event.type === "span.started" && event.span.kind === "model_request",
    );
    const request = sample[start];
    if (request?.type !== "span.started") throw new Error("the sample fixture changed shape");
    const events = sample.slice(0, start + 1);
    for (let line = 1; line <= 750; line++) {
      events.push({
        seq: events.length,
        ts: request.ts + line * 20,
        type: "stream.delta",
        trace_id: request.span.trace_id,
        span_id: request.span.span_id,
        part_index: 0,
        part_kind: "text",
        tool_name: null,
        delta: `Line ${line} of a long answer.\n`,
      });
    }
    const file = writeEmbeddedExport(events, testInfo.outputPath("long-stream.html"));
    await page.goto(`${file}#/runs/${request.span.trace_id}`);
    await page.getByRole("treeitem", { name: /scripted-support/ }).click();

    const inspector = page.getByRole("complementary", { name: "Span details" });
    const panel = inspector.getByRole("tabpanel");
    const position = () =>
      panel.evaluate((el) => ({
        top: el.scrollTop,
        hidden: el.scrollHeight - el.clientHeight,
        fromBottom: el.scrollHeight - el.scrollTop - el.clientHeight,
      }));
    // The text outgrows the panel, and the panel stays on the line being written.
    await expect.poll(async () => (await position()).hidden).toBeGreaterThan(300);
    expect((await position()).fromBottom).toBeLessThan(40);
    await expect(inspector.getByText("streaming")).toBeVisible();

    // Scrolling up to read the beginning lets go: the text keeps growing, the position stays.
    await panel.hover();
    await page.mouse.wheel(0, -20_000);
    // Well away from the bottom. Not exactly at the top: a line that lands while the wheel is
    // still turning shifts the end point by its height.
    await expect.poll(async () => (await position()).fromBottom).toBeGreaterThan(300);
    const { top, hidden } = await position();
    await expect.poll(async () => (await position()).hidden).toBeGreaterThan(hidden + 100);
    expect((await position()).top).toBe(top);
  });

  test("a graph run: subject switcher and visit counts", async ({ page }) => {
    await openRun(page, "sample", PIPELINE);
    await skipToEnd(page);
    await expect(canvasNode(page, "Step Write draft, visited twice, done")).toBeVisible();
    await page
      .getByRole("group", { name: "Canvas subject" })
      .getByRole("button", { name: "writer" })
      .click();
    await expect(canvasNode(page, /^Agent writer, 2 calls, OK/)).toBeVisible();
  });

  test("a failed run shows the error, and a recording cut short says so", async ({ page }) => {
    const failed = readRecording(fixturePath("failed"));
    const roots = failed.flatMap((event) =>
      event.type === "span.started" && event.span.parent_id === null ? [event.span] : [],
    );
    const [errored, unfinished] = roots;
    if (!errored || !unfinished) throw new Error("the failed fixture changed shape");

    await openRun(page, "failed", errored.trace_id);
    await skipToEnd(page);
    await expect(page.locator("main").getByText("Error", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("2 errors", { exact: true })).toBeVisible();
    await page.getByRole("treeitem", { name: /GET feeds\.example\.test/ }).click();
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await expect(inspector).toContainText("ConnectTimeout");

    await openRun(page, "failed", unfinished.trace_id);
    await skipToEnd(page);
    await expect(
      page.getByText("The recording ends here, before this run finished."),
    ).toBeVisible();
  });

  test("a very long error message leaves the tabs and their content in view", async ({
    page,
  }, testInfo) => {
    // Validation and retry errors run to thousands of characters. The header must not grow
    // until the tabs are pushed out of a panel that does not scroll.
    await page.setViewportSize({ width: 1280, height: 800 });
    const events = readRecording(fixturePath("failed"));
    const long = `1 validation error for Digest\n${"items.0.url: Input should be a valid URL. ".repeat(60)}`;
    const failed = events.flatMap((event) =>
      event.type === "span.ended" && event.span.error && event.span.kind !== "other"
        ? [event.span]
        : [],
    );
    for (const span of failed) if (span.error) span.error.message = long;
    const run = failed.find((span) => span.kind === "agent_run");
    const tool = failed.find((span) => span.kind === "tool_call");
    if (!run || !tool) throw new Error("the failed fixture changed shape");

    const file = writeEmbeddedExport(events, testInfo.outputPath("long-error.html"));
    const inspector = page.getByRole("complementary", { name: "Span details" });
    for (const span of [run, tool]) {
      await page.goto(`${file}#/runs/${span.trace_id}?span=${span.span_id}`);
      await page.reload(); // a change of hash alone does not open the run afresh
      await expect(inspector.getByRole("heading", { name: span.name })).toBeVisible();
      const panel = await inspector.boundingBox();
      const tabs = await inspector.getByRole("tablist").boundingBox();
      const content = await inspector.getByRole("tabpanel").boundingBox();
      if (!panel || !tabs || !content) throw new Error("the inspector is not laid out");
      expect(tabs.y + tabs.height).toBeLessThan(panel.y + panel.height);
      expect(content.height).toBeGreaterThan(150);
      // The whole message is still there to read.
      await expect(inspector.getByText("items.0.url").first()).toBeVisible();
    }
  });

  test("a redacted session says content capture is off", async ({ page }) => {
    await page.goto(recording("redacted"));
    await skipToEnd(page);
    await expect(page.getByText("Content off", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Content capture is off (include_content=False)").first(),
    ).toBeVisible();
  });
});

test.describe("navigation", () => {
  test("a deep link restores the selection", async ({ page }) => {
    const span = sampleSpan("issue_refund");
    await openRun(page, "sample", span.trace_id, span.span_id);
    // A run opened on a span does not replay from the start: the linked span, which begins
    // seconds into the run, is there at once, and the run is held at its end.
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await expect(inspector.getByRole("heading", { name: "issue_refund" })).toBeVisible({
      timeout: 1_500,
    });
    await expect(page.getByRole("button", { name: "Replay", exact: true })).toBeVisible();
    await expect(page.getByRole("treeitem", { selected: true })).toContainText("issue_refund");
    // The canvas highlights the tool on the sub-agent, and a reload keeps all of it.
    await expect(page.locator(".pviz-selected")).toHaveCount(1);
    await page.reload();
    await skipToEnd(page);
    await expect(page.getByRole("treeitem", { selected: true })).toContainText("issue_refund");
  });

  test("keyboard navigation", async ({ page }) => {
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    await page.locator("header h1").click();

    await test.step("j, k and Escape move and clear the selection", async () => {
      await page.keyboard.press("j");
      await page.keyboard.press("j");
      await expect(page).toHaveURL(/\?span=/);
      await expect(rows(page).nth(1)).toHaveAttribute("aria-selected", "true");
      await page.keyboard.press("k");
      await expect(rows(page).nth(0)).toHaveAttribute("aria-selected", "true");
      await page.keyboard.press("Escape");
      await expect(page).not.toHaveURL(/\?span=/);
    });

    await test.step("Left and Right step over span boundaries", async () => {
      await page.keyboard.press("ArrowLeft");
      await page.keyboard.press("ArrowLeft");
      await page.keyboard.press("ArrowLeft");
      const stepped = await rows(page).count();
      expect(stepped).toBeLessThan(TRIAGE_SPANS);
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("l");
      await expect(rows(page)).toHaveCount(TRIAGE_SPANS);
    });

    await test.step("[ and ] change the speed; Space plays and pauses", async () => {
      await page.keyboard.press("]");
      await expect(page.getByRole("button", { name: "2x speed" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await page.keyboard.press("[");
      await page.keyboard.press("[");
      await expect(page.getByRole("button", { name: "0.5x speed" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await page.keyboard.press("Space");
      await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
      await page.keyboard.press("Space");
      await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
      await page.keyboard.press("l");
    });

    await test.step("f fits the canvas after a zoom", async () => {
      const viewport = page.locator(".react-flow__viewport");
      const fitted = await viewport.getAttribute("style");
      await page.getByRole("button", { name: "Zoom in" }).click();
      await expect(viewport).not.toHaveAttribute("style", fitted ?? "");
      await page.locator("header h1").click();
      await page.keyboard.press("f");
      await expect(viewport).toHaveAttribute("style", fitted ?? "");
    });

    await test.step("? opens the shortcut list and Escape closes it", async () => {
      await page.keyboard.press("?");
      const dialog = page.getByRole("dialog", { name: "Keyboard shortcuts" });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByText("Fit the canvas")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    });

    await test.step("the span tree is one tab stop with arrow keys", async () => {
      await rows(page).first().focus();
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(rows(page).nth(1)).toHaveAttribute("aria-selected", "true");
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      await expect(rows(page).last()).toHaveAttribute("aria-selected", "true");
    });
  });

  test("the theme toggle works, by button and by key, and is remembered", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(recording("sample", "#/agents/a1"));
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    expect(await theme(page)).toBe("system");
    const light = await background(page);

    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    expect(await theme(page)).toBe("dark");
    await expect.poll(() => background(page)).not.toBe(light);
    await page.reload();
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    expect(await theme(page)).toBe("dark");

    await page.locator("header h1").click();
    await page.keyboard.press("t");
    expect(await theme(page)).toBe("light");
    await expect.poll(() => background(page)).toBe(light);
  });

  test("blueprint screens: agent and tool tabs, graph node, Copy Mermaid", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(recording("sample", "#/agents/a1"));
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    for (const tab of ["Overview", "Instructions", "Output"]) {
      await inspector.getByRole("tab", { name: tab }).click();
      await expect(inspector.getByRole("tabpanel")).toBeVisible();
    }
    await canvasNode(page, /^Tool get_order/).click();
    for (const tab of ["Overview", "Parameters", "Returns"]) {
      await inspector.getByRole("tab", { name: tab }).click();
      await expect(inspector.getByRole("tabpanel")).toBeVisible();
    }
    await inspector.getByRole("tab", { name: "Parameters" }).click();
    await expect(inspector.getByRole("tabpanel")).toContainText("order_id");

    await page.getByRole("link", { name: /pipeline.*6 nodes/ }).click();
    await canvasNode(page, "Decision decision").click();
    await expect(page.getByText("Leads to")).toBeVisible();
    await page.getByRole("button", { name: "Copy Mermaid" }).click();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("stateDiagram-v2");
  });

  test("every span kind has its inspector tabs", async ({ page }) => {
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    const inspector = page.getByRole("complementary", { name: "Span details" });
    const expectTabs = async (names: string[]) => {
      await expect(inspector.getByRole("tab")).toHaveText(names);
      for (const name of names) {
        await inspector.getByRole("tab", { name, exact: true }).click();
        await expect(inspector.getByRole("tabpanel")).toBeVisible();
      }
    };
    await rows(page).nth(0).click();
    await expectTabs(["Conversation", "Output", "Usage", "Raw"]);
    await rows(page).nth(1).click();
    await expectTabs(["Response", "Input messages", "Instructions", "Usage", "Raw"]);
    await rows(page).nth(2).click();
    await expectTabs(["Arguments", "Result", "Error / Retry", "Raw"]);

    await openRun(page, "sample", PIPELINE);
    await skipToEnd(page);
    await rows(page).nth(1).click();
    await expectTabs(["Inputs", "Output", "Raw"]);
  });

  test("an address that points at nothing explains itself", async ({ page }) => {
    await page.goto(recording("sample", "#/runs/00000000000000000000000000000000"));
    await expect(page.getByRole("heading", { name: "Run not found" })).toBeVisible();
    await page.getByRole("link", { name: "Go to the start" }).click();
    await page.waitForURL(/#\/(runs|gallery)/);
  });
});

test.describe("network", () => {
  // Acceptance 7: the page asks for its data source and nothing else. The CSP would block a
  // third-party request anyway; this fails on any request, blocked or not.
  test("a hosted recording: the page, the index and the recording", async ({ page, baseURL }) => {
    const requests = trackRequests(page);
    await page.goto(recording("sample"));
    await skipToEnd(page);
    await rows(page).nth(1).click();
    await runLink(page, "triage").click();
    await page.getByRole("button", { name: "Switch to" }).click();
    await page.waitForLoadState("networkidle");
    expect(requests.sort()).toEqual(
      [
        `${baseURL}${FX}?src=recordings/sample.pviz.jsonl`,
        `${baseURL}${FX}recordings/index.json`,
        `${baseURL}${FX}recordings/sample.pviz.jsonl`,
      ].sort(),
    );
  });

  test("the gallery and the bare viewer: the page and the index", async ({ page, baseURL }) => {
    const requests = trackRequests(page);
    await page.goto(FX);
    await expect(page.getByRole("heading", { name: "Recordings" })).toBeVisible();
    await page.goto(BARE);
    await expect(page.getByText("uvx pydantic-ai-viz demo")).toBeVisible();
    await page.waitForTimeout(500); // Long enough for a stray request to show up.
    expect(requests).toEqual([
      `${baseURL}${FX}`,
      `${baseURL}${FX}recordings/index.json`,
      `${baseURL}${BARE}`,
      `${baseURL}${BARE}recordings/index.json`,
    ]);
  });

  test("an embedded export opened from file:// asks for nothing at all", async ({
    page,
  }, testInfo) => {
    const errors = trackErrors(page);
    const url = writeEmbeddedExport(
      readRecording(fixturePath("sample")),
      testInfo.outputPath("export.html"),
    );
    const requests = trackRequests(page);
    await page.goto(url);
    await page.waitForURL(/#\/runs\//);
    await expect(page.getByText("Export", { exact: true })).toBeVisible();
    await skipToEnd(page);
    await runLink(page, "triage").click();
    await skipToEnd(page);
    await canvasNode(page, /^Tool search_kb/).click();
    await expect(page.getByRole("treeitem", { selected: true })).toContainText("search_kb");
    // file:// refuses storage in some browsers; the theme must still switch.
    await page.getByRole("button", { name: /^Switch to (dark|light) theme$/ }).click();
    await page.waitForLoadState("networkidle");
    expect(requests).toEqual([url]);
    expect(errors).toEqual([]);
  });

  test("an export keeps markup in tool results as text", async ({ page }, testInfo) => {
    const events = readRecording(fixturePath("sample"));
    const hostile = `</script><img src=x onerror="window.__pwned = 1"><!-- & ${String.fromCharCode(0x2028)}`;
    const span = sampleSpan("get_customer");
    for (const event of events) {
      if (event.type === "span.ended" && event.span.kind === "tool_call") {
        if (event.span.span_id === span.span_id) event.span.data.result = hostile;
      }
    }
    await page.goto(writeEmbeddedExport(events, testInfo.outputPath("hostile.html")));
    await page.waitForURL(/#\/runs\//);
    await page.goto(`${page.url().split("#")[0]}#/runs/${span.trace_id}?span=${span.span_id}`);
    await skipToEnd(page);
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await inspector.getByRole("tab", { name: "Result" }).click();
    await expect(inspector.getByRole("tabpanel")).toContainText(
      '<img src=x onerror="window.__pwned',
    );
    expect(await page.evaluate(() => "__pwned" in window)).toBe(false);
    await expect(page.locator("img")).toHaveCount(0);
  });
});

test.describe("live", () => {
  test("a live session shows the run as it happens, then follows a second run", async ({
    page,
  }) => {
    const errors = trackErrors(page);
    await page.goto("/live/");
    await expect(page.getByText("Live", { exact: true }).first()).toBeVisible();
    await page.waitForURL(/#\/runs\//);
    const first = page.url();
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await expect(page.locator("main").getByText("Running", { exact: true }).first()).toBeVisible();
    // While it runs the server refuses a second run and the viewer says why.
    await page.getByRole("button", { name: "Run again" }).click();
    await expect(page.getByText("A run is still going")).toBeVisible();
    // The browser logs the refused request (409) itself; that one line is expected.
    expect(errors.splice(0)).toEqual([expect.stringContaining("409")]);

    await expect(canvasNode(page, /^Output of triage.*, ready$/)).toBeVisible({ timeout: 20_000 });
    await expect(rows(page)).toHaveCount(TRIAGE_SPANS);
    await page.getByRole("button", { name: "Run again" }).click();
    await expect(page).not.toHaveURL(first, { timeout: 10_000 });
    await expect(page.getByRole("navigation", { name: "Runs" }).getByRole("link")).toHaveCount(2);
    expect(errors).toEqual([]);
  });

  test("pausing a live run holds the view; Live returns to now", async ({ page }) => {
    await page.goto("/live-hold-56/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page)).toHaveCount(HELD_SPANS);
    await expect(page.getByRole("button", { name: "Live" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await page.getByRole("button", { name: "Step back" }).click();
    await page.getByRole("button", { name: "Step back" }).click();
    expect(await rows(page).count()).toBeLessThan(HELD_SPANS);
    await page.getByRole("button", { name: "Live" }).click();
    await expect(rows(page)).toHaveCount(HELD_SPANS);
  });

  test("the clock of a live run keeps counting between events", async ({ page }) => {
    // The mock sends 40 events and then holds the stream open: what a slow tool or a model
    // request that is not streamed looks like to the viewer. The run must not look hung.
    await page.goto("/live-hold-40/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page).first()).toBeVisible();
    const length = async () => Number(await slider(page).getAttribute("aria-valuemax"));
    const first = await length();
    await expect.poll(length).toBeGreaterThan(first + 500);
    const sidebarRun = page.getByRole("navigation", { name: "Runs" }).getByRole("link").first();
    const shown = await sidebarRun.textContent();
    await expect(sidebarRun).not.toHaveText(shown ?? "");

    // Pausing holds the moment it was pressed. It does not fall back to the last event.
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    const held = await length();
    expect(held).toBeGreaterThan(first + 500);
    await page.waitForTimeout(300);
    expect(await length()).toBe(held);
    await page.getByRole("button", { name: "Live" }).click();
    await expect.poll(length).toBeGreaterThan(held);
  });

  test("a paused live run that is played again catches up and is live again", async ({ page }) => {
    await page.goto("/live-hold-56/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page)).toHaveCount(HELD_SPANS);
    const liveButton = page.getByRole("button", { name: "Live" });
    await page.locator("header h1").click();
    await page.keyboard.press("Space");
    await expect(page.getByRole("button", { name: "Replay", exact: true })).toBeVisible();
    await expect(liveButton).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("Space");
    // The clock reaches the latest event received. The run is still going, so the view must
    // not stop there as a recording does: it is pinned to now again and follows what comes.
    await expect(liveButton).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
    await expect(rows(page)).toHaveCount(HELD_SPANS);
  });

  test("a lost connection shows a banner and keeps what was received", async ({ page }) => {
    await page.goto("/live-hold-56/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page)).toHaveCount(HELD_SPANS);
    // Stop the server side of this session: the stream ends and reconnecting is refused.
    const key = await page.evaluate(() => {
      const boot = JSON.parse(document.getElementById("pviz-boot")?.textContent ?? "{}");
      return new URL(boot.events_url, "http://localhost").searchParams.get("s");
    });
    expect((await page.request.post(`/live-hold-56/api/v1/drop?s=${key}`)).ok()).toBe(true);
    await expect(page.getByText("Reconnecting...")).toBeVisible({ timeout: 15_000 });
    await expect(rows(page)).toHaveCount(HELD_SPANS);
  });
});
