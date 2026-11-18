/**
 * Live end to end: the built viewer served by the real Python server.
 *
 *   PVIZ_LIVE_URL   address printed by `pydantic-ai-viz demo --no-open --speed 0 --port 0`.
 *                   Not set: skipped. `tests/integration/test_live_viewer.py` starts that
 *                   server, sets the variable and runs this spec; nothing here starts Python.
 *
 * It proves the pieces only the two halves together can show: the boot data the server writes
 * into the page, the event stream, a run started by this very page connecting, and "Run again".
 */
import { expect, type Page, test } from "@playwright/test";
import type { Envelope } from "../src/wire";
import { canvasNode, finalSpans, rows, trackErrors } from "./support";

const live = process.env.PVIZ_LIVE_URL;
const base = live?.endsWith("/") ? live : `${live}/`;

async function exported(page: Page): Promise<Envelope[]> {
  const response = await page.request.get(`${base}api/v1/export.jsonl`);
  expect(response.ok()).toBe(true);
  return (await response.text())
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Envelope);
}

const finishedRuns = (events: Envelope[]): number =>
  finalSpans(events).filter((span) => span.parent_id === null && span.status === "ok").length;

const runLinks = (page: Page) => page.getByRole("navigation", { name: /^Runs/ }).getByRole("link");

test.describe("live demo server", () => {
  test.skip(!live, "PVIZ_LIVE_URL is not set; run tests/integration/test_live_viewer.py");

  test("the run appears and finishes, and Run again produces a second one", async ({ page }) => {
    const errors = trackErrors(page);
    const foreign: string[] = [];
    page.on("request", (request) => {
      if (!request.url().startsWith(base)) foreign.push(request.url());
    });

    // Nothing has run yet: the demo waits for its first viewer, and that is this page.
    expect(finishedRuns(await exported(page))).toBe(0);
    await page.goto(base);
    // "Live" is the one indicator of the connection; there is no second one beside it.
    await expect(page.locator('[data-mode="live"]')).toHaveText("Live");
    await expect(page.getByText("Connected", { exact: true })).toHaveCount(0);
    await page.waitForURL(/#\/runs\//);
    const first = page.url();
    await expect(canvasNode(page, /^Output of triage: Resolution.*, ready$/)).toBeVisible({
      timeout: 20_000,
    });
    await expect(canvasNode(page, /^Tool get_order, retried once, 2 calls, OK$/)).toBeVisible();
    await expect(canvasNode(page, /^Agent refunds, 1 call, OK/)).toBeVisible();
    await expect(runLinks(page)).toHaveCount(1);
    const log = await exported(page);
    expect(finishedRuns(log)).toBe(1);
    await expect(rows(page)).toHaveCount(finalSpans(log).length);

    // The server frees the run a moment after its last span, so an early click may be refused.
    await expect(async () => {
      await page.getByRole("button", { name: "Run again" }).click();
      await expect(runLinks(page)).toHaveCount(2, { timeout: 2_000 });
    }).toPass({ timeout: 20_000 });
    // The viewer follows the new run and shows it finished too.
    await expect(page).not.toHaveURL(first);
    await expect(canvasNode(page, /^Output of triage: Resolution.*, ready$/)).toBeVisible({
      timeout: 20_000,
    });
    await expect.poll(async () => finishedRuns(await exported(page))).toBe(2);

    expect(foreign).toEqual([]);
    // A refused early click is logged by the browser itself (409); nothing else may be.
    expect(errors.filter((line) => !line.includes("409"))).toEqual([]);
  });
});
