/**
 * End-to-end behaviour of the built viewer over the committed fixtures: the things jsdom
 * cannot prove (real layout, pointer input, the canvas, navigation, the network).
 */
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import {
  BARE,
  blueprintOnly,
  canvasNode,
  dropConnection,
  FX,
  fixturePath,
  LIVE_WORDS,
  manyBlueprints,
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

  test("in a narrow window the run's facts wrap beside its title, not under it", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1024, height: 720 });
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    const bar = page.locator("main header").first();
    const [title, copy, retry] = await Promise.all([
      bar.getByRole("heading", { level: 2 }).boundingBox(),
      bar.getByRole("button", { name: "Copy Mermaid" }).boundingBox(),
      bar.getByText("1 retry").boundingBox(),
    ]);
    // The facts take more than one line here...
    expect(retry?.y ?? 0).toBeGreaterThan((title?.y ?? 0) + (title?.height ?? 0));
    // ...and the action stays level with the title, right of the facts, not on a line of its own.
    expect(copy?.y ?? Number.NaN).toBeLessThan((title?.y ?? 0) + (title?.height ?? 0));
    expect(copy?.x ?? 0).toBeGreaterThan((retry?.x ?? 0) + (retry?.width ?? 0));
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
      // Opened from its button, it hands the focus back to that button when it closes.
      const button = page.getByRole("button", { name: "Keyboard shortcuts" });
      await button.click();
      await expect(dialog).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await expect(button).toBeFocused();
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

    await test.step("playback keys work with the focus on a clicked row", async () => {
      await rows(page).nth(2).click();
      await expect(rows(page).nth(2)).toBeFocused();
      for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowLeft");
      expect(await rows(page).count()).toBeLessThan(TRIAGE_SPANS);
      await page.keyboard.press("Space");
      await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
      await page.keyboard.press("Space");
      await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
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

  test("blueprint screens: stacked sections, graph node, Copy Mermaid", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const copied = () => page.evaluate(() => navigator.clipboard.readText());
    await page.goto(recording("sample", "#/agents/a1"));
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    // An agent is one scroll of sections; there is nothing to switch between.
    await expect(inspector.getByRole("tablist")).toHaveCount(0);
    const sections = inspector.getByRole("region", { name: "Agent blueprint" });
    await expect(sections.getByRole("heading", { level: 4 })).toHaveText([
      /^Model/,
      /^Instructions/,
      /^Toolsets/,
      /^Output/,
      /^Delegates to/,
      /^Settings/,
      /^Capabilities/,
      /^Mermaid/,
    ]);
    await page.getByRole("button", { name: "Copy Mermaid" }).click();
    // The clipboard of the platform may hold its own line endings.
    expect(await copied()).toMatch(/^flowchart LR\r?\n {2}agent1\["triage<br\/>model: /);

    await canvasNode(page, /^Tool get_order/).click();
    await expect(inspector.getByRole("tablist")).toHaveCount(0);
    await expect(
      inspector.getByRole("region", { name: "Tool" }).getByRole("heading", { level: 4 }),
    ).toHaveText(["Parameters", "Returns", "Definition"]);
    await expect(inspector.getByRole("table", { name: "Parameters of get_order" })).toContainText(
      "order_id",
    );

    await page
      .getByRole("navigation", { name: /^Blueprints/ })
      .getByRole("link", { name: "pipeline" })
      .click();
    await canvasNode(page, "Decision decision").click();
    await expect(page.getByText("Leads to")).toBeVisible();
    await page.getByRole("button", { name: "Copy Mermaid" }).click();
    expect(await copied()).toContain("stateDiagram-v2");
  });

  test("a section heading stays in view while its section is read", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 620 });
    await page.goto(recording("sample", "#/agents/a1"));
    const sections = page.getByRole("region", { name: "Agent blueprint" });
    const top = async (name: RegExp) =>
      (await sections.getByRole("heading", { level: 4, name }).boundingBox())?.y ?? Number.NaN;
    const panelTop = (await sections.boundingBox())?.y ?? Number.NaN;
    // How far a heading is from the top of the panel. A stuck heading can sit a fraction of a
    // pixel off it (the scroll position is rounded, the panel's top is not), so allow 1.5 px.
    const offset = async (name: RegExp) => Math.abs((await top(name)) - panelTop);
    const NEAR = 1.5;

    // Clicking the output node scrolls the panel to the output section...
    await canvasNode(page, /^Output of triage/).click();
    await expect.poll(() => offset(/^Output/)).toBeLessThan(NEAR);
    // ...and its heading holds that place while the schema under it scrolls by.
    await sections.evaluate((element) => element.scrollBy(0, 90));
    expect(await offset(/^Output/)).toBeLessThan(NEAR);
    await expect(sections.getByRole("table", { name: "Output of triage" })).toBeVisible();

    // The panel scrolls from the keyboard: it is one tab stop.
    await sections.focus();
    await page.keyboard.press("Home");
    await expect.poll(() => offset(/^Model/)).toBeLessThan(NEAR);
    await page.keyboard.press("End");
    await expect(sections.getByRole("heading", { level: 4, name: /^Mermaid/ })).toBeInViewport();
  });

  test("the tab strip has no scrollbar of its own", async ({ page }) => {
    // The strip scrolls sideways when it must. It once scrolled vertically too, by one pixel:
    // each tab reached below the strip to cover its border, and a scrollbar showed at its right.
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    await rows(page).nth(1).click();
    const strip = page.getByRole("complementary", { name: "Span details" }).getByRole("tablist");
    const overflow = () =>
      strip.evaluate((element) => ({
        vertical: element.scrollHeight - element.clientHeight,
        horizontal: element.scrollWidth - element.clientWidth,
      }));
    expect(await overflow()).toEqual({ vertical: 0, horizontal: 0 });
    // In the narrowest inspector the tabs may run off to the side, and only to the side.
    const splitter = page.getByRole("separator", { name: "Resize span details" });
    await splitter.focus();
    await page.keyboard.press("End");
    expect((await overflow()).vertical).toBe(0);
    // The hairline under the strip is still drawn, and the active tab's underline covers it.
    const lines = await strip.evaluate((element) => {
      const active = element.querySelector('[data-state="active"]');
      return {
        strip: getComputedStyle(element).boxShadow,
        bottom: element.getBoundingClientRect().bottom,
        tabBottom: active?.getBoundingClientRect().bottom,
        underline: active ? getComputedStyle(active).borderBottomWidth : null,
      };
    });
    expect(lines.strip).toContain("inset");
    expect(lines.tabBottom).toBe(lines.bottom);
    expect(lines.underline).toBe("2px");
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
    /** The headings of the sections stacked in the tab that is open. */
    const expectSections = (names: string[]) =>
      expect(inspector.getByRole("tabpanel").getByRole("heading", { level: 4 })).toHaveText(names);

    // A tab for what is long or different in kind; sections for what is read together.
    await rows(page).nth(0).click();
    await expectTabs(["Conversation", "Output", "Raw"]);
    await inspector.getByRole("tab", { name: "Output" }).click();
    await expectSections(["Output", "Usage"]);
    await rows(page).nth(1).click();
    await expectTabs(["Response", "Input", "Raw"]);
    await inspector.getByRole("tab", { name: "Response" }).click();
    await expectSections(["Request", "Response"]);
    await inspector.getByRole("tab", { name: "Input" }).click();
    await expectSections(["Instructions", "Input messages"]);
    await rows(page).nth(2).click();
    await expectTabs(["Details", "Raw"]);
    await inspector.getByRole("tab", { name: "Details" }).click();
    await expectSections(["Retry", "Arguments", "Result"]);
    // A call that went well has no error section to show.
    await rows(page).nth(3).click();
    await expectSections(["Arguments", "Result"]);

    await openRun(page, "sample", PIPELINE);
    await skipToEnd(page);
    await rows(page).nth(1).click();
    await expectTabs(["Details", "Raw"]);
    await inspector.getByRole("tab", { name: "Details" }).click();
    await expectSections(["Inputs", "Output"]);
  });

  test("the tab that was open stays open from one span to the next", async ({ page }) => {
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    const inspector = page.getByRole("complementary", { name: "Span details" });
    await rows(page).nth(2).click();
    await inspector.getByRole("tab", { name: "Raw" }).click();
    await page.keyboard.press("j");
    await expect(rows(page).nth(3)).toHaveAttribute("aria-selected", "true");
    await expect(inspector.getByRole("tab", { name: "Raw" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Arrow keys move between the tabs, as in any tab list.
    await inspector.getByRole("tab", { name: "Raw" }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(inspector.getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
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
    // "Live" says the page is connected. Nothing else repeats it.
    await expect(page.locator('[data-mode="live"]')).toHaveText("Live");
    await page.waitForURL(/#\/runs\//);
    const first = page.url();
    await expect(page.getByText("Connected", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Waiting for a run")).toHaveCount(0);
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
    await expect(page.locator('[data-mode="live"]')).toBeVisible();
    // Stop the server side of this session: the stream ends and reconnecting is refused.
    await dropConnection(page);
    await expect(page.getByText("Reconnecting...")).toBeVisible({ timeout: 15_000 });
    await expect(rows(page)).toHaveCount(HELD_SPANS);
    // The banner is the one place that says it; nothing still claims to be live.
    await expect(page.locator("[data-mode]")).toHaveCount(0);
    // Exporting asks the server, so it waits for the server to answer again.
    await expect(page.getByRole("button", { name: "Export" })).toBeDisabled();
  });

  test("a live session with no run yet waits in one place", async ({ page }) => {
    await page.goto("/live-hold-5/");
    await expect(page.getByText("Waiting for a run")).toBeVisible();
    await expect(page.locator('[data-mode="live"]')).toHaveText("Live");
    // It sits under the blueprint's bar, which keeps the inspector toggle at its end.
    const [bar, strip, toggle] = await Promise.all([
      page.locator("main header").first().boundingBox(),
      page.getByRole("status").filter({ hasText: "Waiting for a run" }).boundingBox(),
      page.getByRole("button", { name: /blueprint details$/ }).boundingBox(),
    ]);
    expect(strip?.y).toBeGreaterThanOrEqual((bar?.y ?? 0) + (bar?.height ?? 0) - 1);
    expect(toggle?.y).toBeGreaterThanOrEqual(bar?.y ?? Number.NaN);
    expect((toggle?.y ?? 0) + (toggle?.height ?? 0)).toBeLessThanOrEqual(
      (bar?.y ?? 0) + (bar?.height ?? 0),
    );
    // The empty run list does not say it a second time.
    await expect(page.getByRole("navigation", { name: /^Runs/ })).toHaveText("Runs0");
    await expect(page.getByText(/No runs/)).toHaveCount(0);
  });
});

test.describe("blueprint-only view", () => {
  test("served by `view`: it says what it is and never claims to be live", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto("/view/");
    await page.waitForURL(/#\/agents\/a1/);
    await expect(page.locator("[data-mode]")).toHaveText("Blueprint");
    await expect(page.locator("header h1")).toHaveText("newsroom");
    const sidebar = page.getByRole("complementary", { name: "Blueprints" });
    await expect(sidebar.getByRole("navigation", { name: /^Runs/ })).toHaveCount(0);
    const about = sidebar.getByRole("region", { name: "About this view" });
    await expect(about).toContainText("Read from source code");
    await expect(about).toContainText("The code was parsed and never run");
    await expect(page.locator("#root")).not.toContainText(LIVE_WORDS, { useInnerText: true });

    // The notices of the analysis are a click away.
    await page.getByRole("button", { name: "1 notice" }).click();
    await expect(page.getByRole("dialog", { name: "Session notices" })).toContainText(
      "newsroom/legacy.py was skipped",
    );
    await page.keyboard.press("Escape");

    // Ctrl+C on the command: the page loses nothing, and says only that.
    await dropConnection(page);
    await expect(page.getByText("The server behind this page has stopped.")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator("[data-mode]")).toHaveText("Blueprint");
    await expect(page.locator("#root")).not.toContainText(LIVE_WORDS, { useInnerText: true });
    await expect(page.getByRole("button", { name: "Export" })).toBeDisabled();
    await canvasNode(page, "Tool publish").click();
    await expect(page.getByRole("table", { name: "Parameters of publish" })).toBeVisible();
    // The browser reports the refused reconnects itself; the page must add nothing to that.
    expect(errors.filter((line) => !/503|ERR_|EventSource/.test(line))).toEqual([]);
  });

  test("exported with `view --export`: the same view from one file, offline", async ({
    page,
  }, testInfo) => {
    const errors = trackErrors(page);
    const url = writeEmbeddedExport(
      readRecording(fixturePath("blueprint/newsroom")),
      testInfo.outputPath("blueprint.html"),
    );
    const requests = trackRequests(page);
    await page.goto(url);
    await page.waitForURL(/#\/agents\/a1/);
    await expect(page.locator("[data-mode]")).toHaveText("Blueprint");
    await expect(page.getByRole("region", { name: "About this view" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Export" })).toHaveCount(0);
    await expect(page.locator("#root")).not.toContainText(LIVE_WORDS, { useInnerText: true });
    await page.waitForLoadState("networkidle");
    expect(requests).toEqual([url]);
    expect(errors).toEqual([]);
  });

  test("what the source says, and what it leaves to run time", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(blueprintOnly("#/agents/a2"));
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });

    // The model name is read from the environment: the card names the variable, never the
    // default, and the panel labels the default as one.
    const card = page.locator(".react-flow__node-agent", { hasText: "reporter" }).first();
    await expect(card).toContainText("env NEWSROOM_MODEL");
    await expect(card).not.toContainText("gpt-5.2-mini");
    await expect(card).toContainText("at run time");
    await expect(inspector.getByText(/Only if NEWSROOM_MODEL is not set/)).toBeVisible();

    // Where it is defined can be copied, as a go-to-file box takes it.
    await inspector.getByRole("button", { name: "Copy newsroom/reporters.py:31" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      "newsroom/reporters.py:31",
    );

    // How it is known: one chip, with the key to all three ways behind it.
    await inspector.getByRole("button", { name: /^From source/ }).click();
    const key = page.getByRole("dialog", { name: "How a blueprint is known" });
    await expect(key.getByRole("term")).toHaveText([
      /^From source/,
      "From a live object",
      "Observed in runs",
    ]);
    await page.keyboard.press("Escape");

    // Warnings are counted above the canvas and listed in the panel, in no alarming colour.
    await expect(page.locator("main").getByText("3 warnings")).toBeVisible();
    const warnings = inspector.locator('[data-section="warnings"]');
    await expect(warnings.getByRole("listitem")).toHaveCount(3);
    // The count leads to the list: from a tool's panel, and from the end of the agent's own.
    const count = page.locator("main").getByRole("button", { name: /^3 warnings/ });
    await canvasNode(page, "Tool fetch_article").click();
    await expect(warnings).toHaveCount(0);
    await count.click();
    const panel = inspector.getByRole("region", { name: "Agent blueprint" });
    await panel.evaluate((el) => el.scrollTo(0, el.scrollHeight));
    await expect(warnings).not.toBeInViewport();
    await count.click();
    await expect(warnings).toBeInViewport();
    expect(
      await warnings.evaluate(
        (section, top) => Math.abs(section.getBoundingClientRect().top - top),
        await panel.evaluate((el) => el.getBoundingClientRect().top),
      ),
    ).toBeLessThan(2);
    const colours = await warnings.evaluate((section) => {
      const style = getComputedStyle(document.documentElement);
      const alarming = [style.getPropertyValue("--error"), style.getPropertyValue("--retry")];
      const probe = document.createElement("span");
      document.body.append(probe);
      const resolved = alarming.map((value) => {
        probe.style.color = value;
        return getComputedStyle(probe).color;
      });
      probe.remove();
      const used = [...section.querySelectorAll("*")].map((el) => getComputedStyle(el).color);
      return { resolved, used };
    });
    for (const colour of colours.resolved) expect(colours.used).not.toContain(colour);
  });

  test("a delegation that crosses files says where it lands", async ({ page }) => {
    await page.goto(blueprintOnly("#/agents/a1"));
    const labels = page.locator(".react-flow__edgelabel-renderer span");
    await expect(labels).toHaveText(["in code · reporters.py", "in code · checks.py", "in code"]);
    // Only the line style that is on this canvas is in the key.
    await expect(page.getByText("delegation found in code")).toBeVisible();
    await expect(page.getByText("delegation seen in a run")).toHaveCount(0);
    // The delegate's own panel is one click away, and names its file.
    await canvasNode(page, "Tool check_facts").click();
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    await expect(inspector.getByText("checks.py:19")).toBeVisible();
    await inspector.getByRole("link", { name: "fact_checker" }).click();
    await expect(page).toHaveURL(/#\/agents\/a4$/);
  });

  test("a toolset lists its tools, and each one opens as it does on the canvas", async ({
    page,
  }) => {
    await page.goto(blueprintOnly("#/agents/a2"));
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    // The box's header picks the toolset; its middle is the tools'.
    await canvasNode(page, "Toolset FunctionToolset 'wires', 2 tools").click({
      position: { x: 40, y: 12 },
    });
    await expect(inspector.getByRole("heading", { level: 3 })).toHaveText(
      "FunctionToolset 'wires'",
    );
    const list = inspector.getByRole("list", { name: "Tools of FunctionToolset 'wires'" });
    await expect(list.getByRole("button")).toHaveText([
      /^search_wires\s*3 parameters\s*Search the wire services\.$/,
      /^fetch_article\s*1 parameter\s*Fetch the full text of a wire article\.$/,
    ]);
    await expect(page).toHaveURL(/#\/agents\/a2\?part=toolset%3Ats\d+$/);
    await list.getByRole("button", { name: /^fetch_article/ }).click();
    await expect(inspector.getByRole("heading", { level: 3 })).toHaveText("fetch_article");
    await expect(inspector.getByRole("heading", { level: 3 })).toBeFocused();
    await expect(page.locator(".react-flow .pviz-selected")).toHaveCount(1);
    await expect(page.locator(".react-flow .pviz-selected")).toContainText("fetch_article");
    // The pick is a link, as a click on the canvas is: opened again, it shows the same tool.
    await expect(page).toHaveURL(/#\/agents\/a2\?part=tool%3Afetch_article$/);
    await page.reload();
    await expect(inspector.getByRole("heading", { level: 3 })).toHaveText("fetch_article");
    await expect(page.locator(".react-flow .pviz-selected")).toContainText("fetch_article");
    // And back up, by keyboard: the toolset, then the agent.
    await inspector.getByRole("button", { name: "FunctionToolset 'wires'", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(inspector.getByRole("heading", { level: 3 })).toHaveText(
      "FunctionToolset 'wires'",
    );
    await expect(inspector.getByRole("heading", { level: 3 })).toBeFocused();
    await inspector.getByRole("button", { name: "reporter", exact: true }).click();
    await expect(inspector.getByRole("heading", { level: 3 })).toHaveText("reporter");
    await expect(page).toHaveURL(/#\/agents\/a2$/);
  });

  test("the canvas carries no attribution of its own", async ({ page }) => {
    await page.goto(blueprintOnly("#/agents/a2"));
    await expect(canvasNode(page, "Agent reporter")).toBeVisible();
    await expect(page.locator(".react-flow__attribution")).toHaveCount(0);
    await expect(page.locator(".react-flow")).not.toContainText("React Flow");
  });

  test("a long list of blueprints is grouped by file and can be filtered", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 720 });
    await page.goto(manyBlueprints());
    const list = page.getByRole("navigation", { name: /^Blueprints/ });
    await expect(list.getByRole("link")).toHaveCount(36);
    await expect(list.getByRole("heading", { level: 3 })).toHaveCount(12);

    // The list scrolls inside the sidebar; the page never does, and the note under it stays.
    const scroller = list.locator("ul").first();
    expect(await scroller.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(720);
    await expect(page.getByRole("region", { name: "About this view" })).toBeInViewport();
    // A file's name stays at the top of the list while its agents scroll under it.
    await scroller.evaluate((el) => el.scrollBy(0, 60));
    const firstFile = list.getByRole("heading", { level: 3 }).first();
    const [listBox, fileBox] = [await scroller.boundingBox(), await firstFile.boundingBox()];
    expect(fileBox?.y).toBeCloseTo(listBox?.y ?? Number.NaN, 0);

    const filter = list.getByRole("searchbox", { name: "Filter blueprints" });
    await filter.fill("risk");
    await expect(list.getByRole("link")).toHaveCount(6);
    await expect(list).toContainText("6 of 36");
    await filter.fill("workers.py");
    await expect(list.getByRole("link")).toHaveCount(18);
    await filter.fill("risk_router");
    await list.getByRole("link", { name: "risk_router" }).click();
    await expect(page.locator("main h2")).toHaveText("risk_router");
    await expect(list.getByRole("link", { name: "risk_router" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Typing in the box never triggers a shortcut ("t" would switch the theme).
    await filter.fill("");
    await filter.pressSequentially("writer");
    expect(await theme(page)).toBe("system");
    await expect(list.getByRole("link")).toHaveCount(6);
    await filter.press("Escape");
    await expect(list.getByRole("link")).toHaveCount(36);
  });

  test("a deep source path keeps its file and line in view", async ({ page }, testInfo) => {
    // A scan of a real repository gives paths like these; the fixture's are short.
    const deep = readFileSync(fixturePath("blueprint/newsroom"), "utf8")
      .replaceAll(
        '"file":"newsroom/reporters.py"',
        '"file":"newsroom/reporters_and_the_overnight_wire_desk.py"',
      )
      .replaceAll('"file":"newsroom/', '"file":"examples/pydantic_ai_examples/services/newsroom/');
    const events = deep
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line));
    await page.setViewportSize({ width: 1024, height: 720 });
    await page.goto(
      `${writeEmbeddedExport(events, testInfo.outputPath("deep-paths.html"))}#/agents/a2`,
    );
    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    const panel = inspector.getByRole("region", { name: "Agent blueprint" });
    await expect(inspector.locator("code[title]").first()).toBeVisible();
    // Nothing in the panel is pushed past its right edge...
    expect(await panel.evaluate((el) => el.scrollWidth - el.clientWidth)).toBe(0);

    // ...and every path, in the panel and in the sidebar, gives way from its folder first. How
    // much of a part is cut is measured on its text, which scrollWidth rounds: an ellipsis
    // shows for any overflow, even a fraction of a pixel.
    const paths = await page.evaluate(() =>
      [...document.querySelectorAll("aside code[title] > span, nav h3[title] > span")]
        .filter((root) => root.children.length > 0)
        .map((root) => {
          const measure = (part: Element | null | undefined) => {
            if (!part) return null;
            const range = document.createRange();
            range.selectNodeContents(part);
            const box = part.getBoundingClientRect();
            const edge = root.closest("aside, nav")?.getBoundingClientRect().right ?? 0;
            // Where the last character is drawn: for a folder, its closing slash.
            const text = document.createTreeWalker(part, NodeFilter.SHOW_TEXT).nextNode();
            const last = document.createRange();
            last.setStart(text ?? part, Math.max(0, (text?.textContent?.length ?? 1) - 1));
            last.setEnd(text ?? part, text?.textContent?.length ?? 0);
            const end = last.getBoundingClientRect();
            return {
              text: part.textContent ?? "",
              width: box.width,
              // A folder's floor: two characters, the ellipsis and the last slash.
              floor: Number.parseFloat(getComputedStyle(part).minWidth) || 0,
              endShown: end.left >= box.left - 0.5 && end.right <= box.right + 0.5,
              lastWidth: end.width,
              cut: range.getBoundingClientRect().width - box.width,
              inside: box.right <= edge + 0.5,
            };
          };
          const tail = root.lastElementChild;
          return {
            title: root.parentElement?.getAttribute("title") ?? "",
            folder: root.children.length > 1 ? measure(root.firstElementChild) : null,
            name: measure(tail?.children[0]),
            line: measure(tail?.children[1]),
          };
        }),
    );
    expect(paths.length).toBeGreaterThan(6);
    for (const { title, folder, name, line } of paths) {
      expect(title).toBe(`${folder?.text ?? ""}${name?.text}${line?.text ?? ""}`);
      // A folder is cut from its start: its last slash always shows, never a lone first letter
      // that would read as part of the name ("pmedical")...
      if (folder) expect(folder.width).toBeGreaterThanOrEqual(folder.floor - 0.01);
      if (folder) expect(folder.endShown).toBe(true);
      // A cut folder keeps room for more than its slash, so the ellipsis shows and the path never
      // reads as starting at the root ("/medical.py").
      if (folder && folder.cut > 0.01) expect(folder.width).toBeGreaterThan(1.5 * folder.lastWidth);
      // ...and a name is cut only once the folder is down to that floor.
      if (folder && folder.width > folder.floor + 0.01) expect(name?.cut).toBeLessThan(0.01);
      expect(name?.width).toBeGreaterThan(48);
      // The line is never cut.
      if (line) expect(line.inside).toBe(true);
      if (line) expect(line.cut).toBeLessThan(0.01);
    }
    // The long name is cut in the panel's header and in the sidebar, with its line kept.
    const long = paths.filter(
      ({ name }) => name?.text === "reporters_and_the_overnight_wire_desk.py",
    );
    expect(long.map(({ line }) => line?.text ?? "heading")).toEqual(
      expect.arrayContaining([":31", "heading"]),
    );
    for (const path of long) {
      expect(path.name?.cut).toBeGreaterThan(1);
      expect(path.folder?.floor).toBeGreaterThan(4);
      expect(path.folder?.width).toBeCloseTo(path.folder?.floor ?? Number.NaN, 1);
    }
  });

  test("a file can be dropped on a blueprint-only view, and replaces it", async ({ page }) => {
    await page.goto(blueprintOnly());
    await page.waitForURL(/#\/agents\/a1/);
    await dropFile(page, "sample.pviz.jsonl", readFileSync(fixturePath("sample"), "utf8"));
    await expect(page.locator("[data-mode]")).toHaveText("Recording");
    await expect(page.getByRole("region", { name: "About this view" })).toHaveCount(0);
    await expect(runLink(page, "triage")).toBeVisible();
  });
});
