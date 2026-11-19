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
import { canvasNode, finalSpans, LIVE_WORDS, rows, trackErrors } from "./support";

const live = process.env.PVIZ_LIVE_URL;
const base = live?.endsWith("/") ? live : `${live}/`;

async function exported(page: Page, server = base): Promise<Envelope[]> {
  const response = await page.request.get(`${server}api/v1/export.jsonl`);
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

/**
 * Static analysis end to end: the built viewer served by the real `view` command.
 *
 *   PVIZ_VIEW_URL   address printed by `pydantic-ai-viz view <target> --no-open --port 0`,
 *                   without `--import`. Not set: skipped.
 *
 * The fixture the other specs use is written by hand from the wire contract. Here the
 * blueprints come out of the analyser, and what the page must show is read from the session's
 * own export, so the spec holds for any target.
 */
const view = process.env.PVIZ_VIEW_URL;
const viewBase = view?.endsWith("/") ? view : `${view}/`;

const escaped = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test.describe("real view server", () => {
  test.skip(!view, "PVIZ_VIEW_URL is not set; start `pydantic-ai-viz view` and pass its address");

  test("what was read from source is on screen, and nothing claims to be live", async ({
    page,
  }) => {
    const errors = trackErrors(page);
    const foreign: string[] = [];
    page.on("request", (request) => {
      if (!request.url().startsWith(viewBase)) foreign.push(request.url());
    });

    const log = await exported(page, viewBase);
    const session = log[0];
    if (session?.type !== "session.started") throw new Error("the export has no session.started");
    expect(session.mode).toBe("blueprint");
    const agents = log.flatMap((event) => (event.type === "blueprint.agent" ? [event.agent] : []));
    const graphs = log.flatMap((event) => (event.type === "blueprint.graph" ? [event.graph] : []));
    expect(agents.length + graphs.length).toBeGreaterThan(0);
    expect([...agents, ...graphs].map((blueprint) => blueprint.source)).toEqual(
      [...agents, ...graphs].map(() => "code"),
    );
    // A session without runs has only blueprints in it.
    expect(log.filter((event) => event.type.startsWith("span."))).toEqual([]);

    await page.goto(viewBase);
    await expect(page.locator("[data-mode]")).toHaveText("Blueprint");
    await expect(page.locator("header h1")).toHaveText(session.title);
    const sidebar = page.getByRole("complementary", { name: "Blueprints" });
    await expect(
      sidebar.getByRole("navigation", { name: /^Blueprints/ }).getByRole("link"),
    ).toHaveCount(agents.length + graphs.length);
    await expect(sidebar.getByRole("region", { name: "About this view" })).toContainText(
      "The code was parsed and never run",
    );
    await expect(page.locator("#root")).not.toContainText(LIVE_WORDS, { useInnerText: true });

    const inspector = page.getByRole("complementary", { name: "Blueprint details" });
    for (const agent of agents) {
      await page.goto(`${viewBase}#/agents/${agent.id}`);
      await expect(inspector.getByRole("heading", { level: 3 })).toHaveText(agent.label);
      await expect(inspector.getByRole("button", { name: /^From source/ })).toBeVisible();
      if (agent.location) {
        await expect(inspector).toContainText(`${agent.location.file}:${agent.location.line}`);
      }
      for (const tool of agent.tools) {
        await expect(canvasNode(page, new RegExp(`^Tool ${escaped(tool.name)}$`))).toBeVisible();
      }
      // Each warning of the analyser is listed, with its backticks turned into code.
      for (const warning of agent.warnings) {
        await expect(inspector).toContainText(warning.replace(/`/g, ""));
      }
      // Each toolset with tools opens on a panel that lists them. The subject's own boxes come
      // first on the canvas; a delegate drawn below it may have a toolset of the same name.
      for (const toolset of agent.toolsets) {
        const count = agent.tools.filter((tool) => tool.toolset === toolset.id).length;
        if (count === 0) continue;
        const name = `Toolset ${toolset.label}, ${count} tool${count === 1 ? "" : "s"}`;
        await canvasNode(page, name).first().click({ position: { x: 40, y: 12 } });
        await expect(
          inspector.getByRole("list", { name: `Tools of ${toolset.label}` }).getByRole("button"),
        ).toHaveCount(count);
      }
    }
    for (const graph of graphs) {
      await page.goto(`${viewBase}#/graphs/${graph.id}`);
      await expect(inspector.getByRole("heading", { level: 3 })).toHaveText(graph.label);
      await expect(inspector.getByRole("button", { name: /^From source/ })).toBeVisible();
      await expect(inspector.locator('[data-section="nodes"] li')).toHaveCount(graph.nodes.length);
    }

    expect(foreign).toEqual([]);
    expect(errors).toEqual([]);
  });
});
