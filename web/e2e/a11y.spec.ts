/**
 * Accessibility: axe over every screen in both themes (no serious or critical violation), and
 * the reduced-motion contract (nothing animates, playback still works).
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import {
  BARE,
  blueprintOnly,
  canvasNode,
  dropConnection,
  FX,
  manyBlueprints,
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
    name: "tool blueprint",
    open: async (page) => {
      await page.goto(recording("sample", "#/agents/a1"));
      await canvasNode(page, /^Tool get_order/).click();
      await expect(page.getByRole("table", { name: "Parameters of get_order" })).toBeVisible();
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
      await expect(page.getByText("A retry is not a failure")).toBeVisible();
    },
  },
  {
    name: "run view, model request",
    open: async (page) => {
      await openRun(page, "sample", TRIAGE);
      await skipToEnd(page);
      await rows(page).nth(1).click();
      await page.getByRole("tab", { name: "Input" }).click();
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
    name: "blueprint-only view, served: an agent read from source",
    open: async (page) => {
      await page.goto("/view/");
      await page.waitForURL(/#\/agents\/a1/);
      await expect(page.getByRole("region", { name: "About this view" })).toBeVisible();
      await expect(canvasNode(page, "Agent reporter")).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: expressions, warnings and the provenance key",
    open: async (page) => {
      await page.goto(blueprintOnly("#/agents/a2"));
      await page.getByRole("button", { name: /^From source/ }).click();
      await expect(page.getByRole("dialog", { name: "How a blueprint is known" })).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: a tool whose type could not be followed",
    open: async (page) => {
      await page.goto(blueprintOnly("#/agents/a4"));
      await canvasNode(page, "Tool lookup_source").click();
      await expect(page.getByRole("table", { name: "Parameters of lookup_source" })).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: a toolset and the tools it lists",
    open: async (page) => {
      await page.goto(blueprintOnly("#/agents/a2"));
      await canvasNode(page, "Toolset FunctionToolset 'wires', 2 tools").click({
        position: { x: 40, y: 12 },
      });
      await expect(
        page.getByRole("list", { name: "Tools of FunctionToolset 'wires'" }),
      ).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: a toolset that lists its tools only at run time",
    open: async (page) => {
      await page.goto(blueprintOnly("#/agents/a2"));
      await canvasNode(page, "Toolset MCPServerStdio: its tools are decided at run time").click();
      await expect(page.getByText(/lists its tools only when the code runs/)).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: an agent's capabilities, one picked on the canvas",
    open: async (page) => {
      await page.goto(`${FX}?src=recordings/blueprint/capabilities.pviz.jsonl#/agents/a1`);
      await canvasNode(page, /^Capability Skills, /).click();
      await expect(
        page.getByRole("complementary", { name: /details/ }).getByRole("list", {
          name: "Skills of Skills",
        }),
      ).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: a skill picked on the canvas",
    open: async (page) => {
      await page.goto(`${FX}?src=recordings/blueprint/capabilities.pviz.jsonl#/agents/a1`);
      await canvasNode(page, /^Skill house-style of Skills/).click();
      await expect(page.getByRole("heading", { level: 3, name: "house-style" })).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: what Pydantic AI adds by default, shown",
    open: async (page) => {
      await page.goto(`${FX}?src=recordings/blueprint/capabilities.pviz.jsonl#/agents/a1`);
      await page.getByRole("button", { name: /^Pydantic AI defaults/ }).click();
      await expect(canvasNode(page, /, a Pydantic AI default$/).first()).toBeVisible();
    },
  },
  {
    name: "run view, a toolset picked on the canvas",
    open: async (page) => {
      await openRun(page, "sample", TRIAGE);
      await skipToEnd(page);
      await canvasNode(page, "Toolset Agent tools, 3 tools").click({ position: { x: 40, y: 12 } });
      await expect(page.getByRole("list", { name: "Tools of Agent tools" })).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: a graph, with the session notices open",
    open: async (page) => {
      await page.goto(blueprintOnly("#/graphs/g1"));
      await page.getByRole("button", { name: "1 notice" }).click();
      await expect(page.getByRole("dialog", { name: "Session notices" })).toBeVisible();
    },
  },
  {
    name: "blueprint-only view: the server behind it has stopped",
    open: async (page) => {
      await page.goto("/view/");
      await page.waitForURL(/#\/agents\/a1/);
      await dropConnection(page);
      await expect(page.getByText("The server behind this page has stopped.")).toBeVisible();
    },
  },
  {
    name: "a long blueprint list, filtered",
    open: async (page) => {
      await page.goto(manyBlueprints());
      await page.getByRole("searchbox", { name: "Filter blueprints" }).fill("billing");
      await expect(page.getByRole("navigation", { name: /Blueprints/ })).toContainText("6 of 36");
    },
  },
  {
    name: "a long blueprint list, nothing matches",
    open: async (page) => {
      await page.goto(manyBlueprints());
      await page.getByRole("searchbox", { name: "Filter blueprints" }).fill("zzz");
      await expect(page.getByText('No blueprint matches "zzz".')).toBeVisible();
    },
  },
  {
    name: "live session, waiting for a run",
    open: async (page) => {
      await page.goto("/live-hold-5/");
      await expect(page.getByText("Waiting for a run")).toBeVisible();
    },
  },
  {
    name: "live session, connection lost",
    open: async (page) => {
      await page.goto("/live-hold-56/");
      await page.waitForURL(/#\/runs\//);
      await dropConnection(page);
      await expect(page.getByText("Reconnecting...")).toBeVisible();
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
  test("at 1024 px a blueprint-only view has no serious violation and no sideways scroll", async ({
    page,
  }) => {
    await page.goto(blueprintOnly("#/agents/a1"));
    // Under 1024 px the inspector is a drawer, closed until asked for.
    await page.setViewportSize({ width: 1000, height: 720 });
    await page.reload();
    await page.getByRole("button", { name: "Show blueprint details" }).click();
    await expect(page.getByRole("region", { name: "Agent blueprint" })).toBeVisible();
    await page.waitForTimeout(300);
    await expectAccessible(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflow).toBe(false);
  });

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
