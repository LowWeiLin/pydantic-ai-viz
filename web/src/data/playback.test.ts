import { describe, expect, test } from "vitest";
import {
  type Bounds,
  changeSpeed,
  effectiveTime,
  goLive,
  livePlayback,
  type Playback,
  pause,
  play,
  playFromStart,
  progress,
  SPEEDS,
  seek,
  step,
  tick,
  togglePlay,
} from "./playback";

const bounds: Bounds = { start: 1000, end: 2000 };
const at = (t: number, mode: Playback["mode"] = "paused", speed = 1): Playback => ({
  mode,
  t,
  speed,
});

describe("playback", () => {
  test("live projects at infinity; other modes at t", () => {
    expect(effectiveTime(livePlayback)).toBe(Number.POSITIVE_INFINITY);
    expect(effectiveTime(at(1500))).toBe(1500);
  });

  test("tick advances by elapsed time times speed", () => {
    expect(tick(at(1000, "playing", 2), 100, bounds)).toEqual(at(1200, "playing", 2));
  });

  test("tick stops and pauses at the end", () => {
    expect(tick(at(1950, "playing", 4), 100, bounds)).toEqual(at(2000, "paused", 4));
  });

  test("tick leaves paused and live clocks alone", () => {
    const paused = at(1500);
    expect(tick(paused, 100, bounds)).toBe(paused);
    expect(tick(livePlayback, 100, bounds)).toBe(livePlayback);
  });

  test("play resumes, and restarts from the end or from live", () => {
    expect(play(at(1500), bounds)).toEqual(at(1500, "playing"));
    expect(play(at(2000), bounds)).toEqual(at(1000, "playing"));
    expect(play(livePlayback, bounds)).toEqual(at(1000, "playing"));
    expect(playFromStart(at(1700, "paused", 2), bounds)).toEqual(at(1000, "playing", 2));
  });

  test("pause from live freezes at the end of the range", () => {
    expect(pause(livePlayback, bounds)).toEqual(at(2000));
    expect(togglePlay(at(1500, "playing"), bounds)).toEqual(at(1500));
    expect(togglePlay(at(1500), bounds).mode).toBe("playing");
  });

  test("seek clamps into the range and pauses", () => {
    expect(seek(at(1500, "playing"), 500, bounds)).toEqual(at(1000));
    expect(seek(livePlayback, 99999, bounds)).toEqual(at(2000));
  });

  test("goLive re-pins and keeps the speed", () => {
    expect(goLive(at(1500, "paused", 4))).toMatchObject({ mode: "live", speed: 4 });
    expect(goLive(livePlayback)).toBe(livePlayback);
  });

  test("changeSpeed walks the speed list and stops at its ends", () => {
    expect(changeSpeed(at(0, "paused", 1), 1).speed).toBe(2);
    expect(changeSpeed(at(0, "paused", 1), -1).speed).toBe(0.5);
    const slowest = at(0, "paused", SPEEDS[0]);
    expect(changeSpeed(slowest, -1)).toBe(slowest);
    const fastest = at(0, "paused", 8);
    expect(changeSpeed(fastest, 1)).toBe(fastest);
    expect(changeSpeed(at(0, "paused", 3), 1).speed).toBe(4);
  });

  test("step moves to the neighbouring span boundary", () => {
    const stops = [1000, 1200, 1600, 2000];
    expect(step(at(1200), stops, 1, bounds).t).toBe(1600);
    expect(step(at(1300), stops, -1, bounds).t).toBe(1200);
    expect(step(at(1000), stops, -1, bounds).t).toBe(1000);
    expect(step(at(2000), stops, 1, bounds).t).toBe(2000);
    expect(step(livePlayback, stops, -1, bounds)).toEqual(at(1600));
  });

  test("progress is the clock's position in the range", () => {
    expect(progress(at(1250), bounds)).toBe(0.25);
    expect(progress(livePlayback, bounds)).toBe(1);
    expect(progress(at(5), { start: 5, end: 5 })).toBe(1);
  });
});
