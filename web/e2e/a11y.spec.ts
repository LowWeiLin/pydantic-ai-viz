/**
 * Accessibility: axe over every screen in both themes (no serious or critical violation), and
 * the reduced-motion contract (nothing animates, playback still works).
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import {
  BARE,
  canvasNode,
  FX,
  openRun,
  PIPELINE,
  pauseAtBoundary,
  recording,
  rows,
  runningAnimations,
  skipToEnd,
  slider,
  TRIAGE,
} from "./support";

/** Fails on serious and critical findings, and prints what and where. */
async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
    .analyze();
  const blocking = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => ({
      rule: violation.id,
      impact: violation.impact,
      help: violation.help,
      nodes: violation.nodes.map((node) => `${node.target.join(" ")} :: ${node.failureSummary}`),
    }));
  expect(blocking).toEqual([]);
}

const SCREENS: { name: string; open: (page: Page) => Promise<void> }[] = [
  {
    name: "gallery",
    open: async (page) => {
      await page.goto(FX);
      await expect(page.getByRole("heading", { name: "Recordings" })).toBeVisible();
    },
  },
  {
    name: "agent blueprint",
    open: async (page) => {
      await page.goto(recording("sample", "#/agents/a1"));
      await expect(canvasNode(page, /^Agent triage/)).toBeVisible();
    },
  },
  {
    name: "tool blueprint, parameters",
    open: async (page) => {
      await page.goto(recording("sample", "#/agents/a1"));
      await canvasNode(page, /^Tool get_order/).click();
      await page.getByRole("tab", { name: "Parameters" }).click();
    },
  },
  {
    name: "run view, finished, conversation",
    open: async (page) => {
      await openRun(page, "sample", TRIAGE);
      await skipToEnd(page);
    },
  },
  {
    name: "run view, mid-run with a retry and a streaming response",
    open: async (page) => {
      await openRun(page, "sample", TRIAGE);
      // Boundary 13: the third model request has just started; a retry is on the canvas.
      await pauseAtBoundary(page, 13);
      for (let i = 0; i < 9; i++) await page.keyboard.press("j");
      for (let i = 0; i < 3; i++) await slider(page).press("ArrowRight");
      await expect(page.getByRole("region", { name: "Response so far" })).toBeVisible();
    },
  },
  {
    name: "run view, tool call selected",
    open: async (page) => {
      await openRun(page, "sample", TRIAGE);
      await skipToEnd(page);
      await rows(page).nth(2).click();
      await page.getByRole("tab", { name: "Error / Retry" }).click();
    },
  },
  {
    name: "graph blueprint",
    open: async (page) => {
      await page.goto(recording("sample", "#/graphs/g1"));
      await expect(canvasNode(page, "Decision decision")).toBeVisible();
    },
  },
  {
    name: "graph run",
    open: async (page) => {
      await openRun(page, "sample", PIPELINE);
      await skipToEnd(page);
    },
  },
  {
    name: "failed run",
    open: async (page) => {
      await page.goto(recording("failed"));
      await skipToEnd(page);
      await page.getByRole("link", { name: /Error.*digest/ }).click();
      await skipToEnd(page);
      await page
        .getByRole("treeitem", { name: /fetch_feed/ })
        .last()
        .click();
    },
  },
  {
    name: "redacted run",
    open: async (page) => {
      await page.goto(recording("redacted"));
      await skipToEnd(page);
      await rows(page).nth(1).click();
    },
  },
  {
    name: "empty state",
    open: async (page) => {
      await page.goto(BARE);
      await expect(page.getByText("uvx pydantic-ai-viz demo")).toBeVisible();
    },
  },
  {
    name: "failed load",
    open: async (page) => {
      await page.goto(`${FX}?src=recordings/nope.pviz.jsonl`);
      await expect(page.getByRole("alert")).toBeVisible();
    },
  },
  {
    name: "shortcuts dialog",
    open: async (page) => {
      await page.goto(recording("sample", "#/agents/a1"));
      await page.getByRole("button", { name: "Keyboard shortcuts" }).click();
      await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
    },
  },
  {
    name: "live session, run in progress",
    open: async (page) => {
      await page.goto("/live-hold-56/");
      await page.waitForURL(/#\/runs\//);
      await expect(rows(page)).toHaveCount(8);
    },
  },
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe, ${scheme}`, () => {
    test.use({ colorScheme: scheme });
    for (const screen of SCREENS) {
      test(screen.name, async ({ page }) => {
        await screen.open(page);
        // Entry fades are 150 to 220 ms; axe must see settled colours.
        await page.waitForTimeout(300);
        await expectAccessible(page);
      });
    }
  });
}

test.describe("narrow window", () => {
  test.use({ viewport: { width: 1024, height: 720 } });
  test("at 1024 px the run view has no serious violation and no sideways scroll", async ({
    page,
  }) => {
    await openRun(page, "sample", TRIAGE);
    await skipToEnd(page);
    await page.waitForTimeout(300);
    await expectAccessible(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflow).toBe(false);
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("a run in progress shows no animation, and playback still works", async ({ page }) => {
    await openRun(page, "sample", TRIAGE);
    // Mid-run and paused: a model request is running and tools have been called.
    await pauseAtBoundary(page, 13);
    await expect(canvasNode(page, /^Model scripted-support of triage.*running/)).toBeVisible();
    await page.waitForTimeout(400);
    expect(await runningAnimations(page)).toBe(0);

    // Playing: time moves (rows arrive) but nothing decorative animates.
    const before = await rows(page).count();
    await page.keyboard.press("]");
    await page.keyboard.press("]");
    await page.keyboard.press("Space");
    await expect.poll(() => rows(page).count()).toBeGreaterThan(before);
    expect(await runningAnimations(page)).toBe(0);
    await expect(page.getByRole("button", { name: "Replay", exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(canvasNode(page, /^Output of triage.*, ready$/)).toBeVisible();
  });

  test("a live run in progress shows no animation", async ({ page }) => {
    await page.goto("/live-hold-56/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page)).toHaveCount(8);
    await page.waitForTimeout(400);
    expect(await runningAnimations(page)).toBe(0);
  });

  test("with motion allowed the same view does animate", async ({ browser }) => {
    // The control for the tests above: proves the measurement can see the animations.
    const context = await browser.newContext({ reducedMotion: "no-preference" });
    const page = await context.newPage();
    await page.goto("/live-hold-56/");
    await page.waitForURL(/#\/runs\//);
    await expect(rows(page)).toHaveCount(8);
    expect(await runningAnimations(page)).toBeGreaterThan(0);
    await context.close();
  });
});
