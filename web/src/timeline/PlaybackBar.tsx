/**
 * Playback controls: play and pause, step by span boundary, the clock, speed, and the button
 * that returns to the end (a recording) or to now (a live session). The scrubber itself is the
 * ruler of the timeline below (`Timeline`), so its position lines up with the waterfall.
 */
import type { ReactNode } from "react";
import { type Bounds, goLive, type Playback, SPEEDS, seek, togglePlay } from "../data/playback";
import { useViewer } from "../data/store";
import { formatDuration } from "../shell/format";
import { Button, IconButton } from "../ui/Button";
import { Tooltip } from "../ui/overlays";
import { cx } from "../ui/tone";

export function PlaybackBar({
  playback,
  bounds,
  live,
  onStep,
  children,
}: {
  playback: Playback;
  bounds: Bounds;
  /** The source is a live session: the last button pins to now instead of skipping to the end. */
  live: boolean;
  onStep: (direction: 1 | -1) => void;
  /** A short note shown in the bar, e.g. that the recording stops before the run ends. */
  children?: ReactNode;
}) {
  const update = useViewer((state) => state.updatePlayback);
  const pinned = playback.mode === "live";
  const playing = playback.mode === "playing";
  const total = Math.max(0, bounds.end - bounds.start);
  const elapsed = pinned ? total : Math.min(total, Math.max(0, playback.t - bounds.start));
  const atEnd = !pinned && !playing && playback.t >= bounds.end;
  // A live view is "playing" in the sense that matters to the user: it moves by itself.
  const moving = playing || pinned;

  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-line border-b px-2">
      <Button
        variant="primary"
        icon={moving ? "pause" : "play"}
        className="w-[84px]"
        onClick={() => update((p) => togglePlay(p, bounds))}
      >
        {moving ? "Pause" : atEnd ? "Replay" : "Play"}
      </Button>
      <div className="flex items-center">
        <Tooltip content="Previous span boundary (Left)" side="top">
          <IconButton icon="stepBack" label="Step back" onClick={() => onStep(-1)} />
        </Tooltip>
        <Tooltip content="Next span boundary (Right)" side="top">
          <IconButton
            icon="stepForward"
            label="Step forward"
            disabled={pinned || atEnd}
            onClick={() => onStep(1)}
          />
        </Tooltip>
      </div>
      <p className="m-0 whitespace-nowrap font-mono text-data">
        <span className="sr-only">Replay time: </span>
        <span className="text-fg">{formatDuration(elapsed)}</span>
        <span className="text-faint"> / {formatDuration(total)}</span>
      </p>
      {children && <div className="min-w-0 truncate text-muted text-ui">{children}</div>}

      <div className="ml-auto flex items-center gap-2">
        {/* biome-ignore lint/a11y/useSemanticElements: a fieldset would add a legend line; a labelled group is the toolbar pattern. */}
        <div
          role="group"
          aria-label="Playback speed"
          className="flex overflow-hidden rounded-control border border-line max-md:hidden"
        >
          {SPEEDS.map((speed) => (
            <button
              key={speed}
              type="button"
              aria-pressed={playback.speed === speed}
              aria-label={`${speed} times speed`}
              onClick={() => update((p) => ({ ...p, speed }))}
              className={cx(
                "h-6 min-w-8 border-0 border-line border-l px-1.5 font-mono text-meta first:border-l-0",
                "transition-colors duration-150 ease-out focus-visible:outline-offset-[-2px]",
                playback.speed === speed
                  ? "bg-accent font-semibold text-accent-fg"
                  : "bg-surface text-muted hover:bg-surface-2 hover:text-fg",
              )}
            >
              {speed}x
            </button>
          ))}
        </div>
        {live ? (
          <Tooltip content="Follow the session as it happens (l)" side="top">
            <Button
              size="sm"
              icon="live"
              aria-pressed={pinned}
              className={cx(pinned && "tone-accent tone-soft")}
              onClick={() => update(goLive)}
            >
              Live
            </Button>
          </Tooltip>
        ) : (
          <Tooltip content="Jump to the end (l)" side="top">
            <Button
              size="sm"
              icon="skipEnd"
              disabled={atEnd}
              onClick={() => update((p) => seek(p, bounds.end, bounds))}
            >
              Skip to end
            </Button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
