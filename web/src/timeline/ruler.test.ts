// @vitest-environment node
import { describe, expect, test } from "vitest";
import { barExtent, fraction, rulerTicks, tickLabel } from "./ruler";

describe("rulerTicks", () => {
  test("uses 1, 2 or 5 times a power of ten and never more than the limit", () => {
    expect(rulerTicks(6318, 8)).toEqual([0, 1000, 2000, 3000, 4000, 5000, 6000]);
    expect(rulerTicks(3850, 8)).toEqual([0, 500, 1000, 1500, 2000, 2500, 3000, 3500]);
    expect(rulerTicks(896, 8)).toEqual([0, 200, 400, 600, 800]);
    expect(rulerTicks(90_000, 8)).toEqual([0, 20_000, 40_000, 60_000, 80_000]);
    for (const duration of [0.4, 3, 17, 950, 12_345, 600_000]) {
      const ticks = rulerTicks(duration, 8);
      expect(ticks.length).toBeLessThanOrEqual(9);
      expect(ticks.length).toBeGreaterThan(2);
      expect(ticks.at(-1)).toBeLessThanOrEqual(duration * 1.000001);
    }
  });

  test("includes a tick that lands exactly on the end", () => {
    expect(rulerTicks(4000, 8)).toEqual([0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000]);
  });

  test("an empty or broken range has only the zero tick", () => {
    expect(rulerTicks(0)).toEqual([0]);
    expect(rulerTicks(-5)).toEqual([0]);
    expect(rulerTicks(Number.NaN)).toEqual([0]);
    expect(rulerTicks(1000, 0)).toEqual([0]);
  });

  test("sub-millisecond ranges get sub-millisecond steps", () => {
    const ticks = rulerTicks(0.3, 8);
    expect(ticks).toHaveLength(7);
    expect(ticks[1]).toBe(0.05);
    expect(ticks[6]).toBeCloseTo(0.3, 12);
  });
});

describe("tickLabel", () => {
  test("is short and uses the unit that fits", () => {
    expect(tickLabel(0)).toBe("0");
    expect(tickLabel(0.05)).toBe("0.05 ms");
    expect(tickLabel(250)).toBe("250 ms");
    expect(tickLabel(1000)).toBe("1 s");
    expect(tickLabel(1500)).toBe("1.5 s");
    expect(tickLabel(60_000)).toBe("1m");
    expect(tickLabel(90_000)).toBe("1m 30s");
  });
});

describe("fraction and barExtent", () => {
  const bounds = { start: 1000, end: 3000 };

  test("fraction clamps into the range", () => {
    expect(fraction(1000, bounds)).toBe(0);
    expect(fraction(2000, bounds)).toBe(0.5);
    expect(fraction(99_999, bounds)).toBe(1);
    expect(fraction(0, bounds)).toBe(0);
    expect(fraction(5, { start: 5, end: 5 })).toBe(0);
  });

  test("an ended span spans start to end", () => {
    expect(barExtent(1500, 2500, 9999, bounds)).toEqual({ left: 0.25, width: 0.5 });
  });

  test("a running span ends at now", () => {
    expect(barExtent(1500, null, 2000, bounds)).toEqual({ left: 0.25, width: 0.25 });
  });

  test("a span is never drawn backwards or outside the track", () => {
    expect(barExtent(2000, null, 1500, bounds)).toEqual({ left: 0.5, width: 0 });
    expect(barExtent(3500, null, 3600, bounds)).toEqual({ left: 1, width: 0 });
    expect(barExtent(500, 5000, 0, bounds)).toEqual({ left: 0, width: 1 });
  });
});
