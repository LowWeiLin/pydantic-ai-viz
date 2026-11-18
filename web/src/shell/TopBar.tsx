import { useState } from "react";
import type { Connection } from "../data/liveSource";
import { modeLabel, type Source, useViewer } from "../data/store";
import { Chip } from "../ui/Badge";
import { Button, ButtonLink, IconButton } from "../ui/Button";
import { Icon, type IconName } from "../ui/Icon";
import { Popover, Tooltip } from "../ui/overlays";
import { cx, type Tone, toneClass } from "../ui/tone";
import { formatRoute, type Route } from "./router";
import { toggleTheme, useTheme } from "./theme";

/** The mark: two nodes joined by a run. Also the favicon. */
export function Logo() {
  return (
    <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" className="shrink-0">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path
        d="M10 10.5h5.5a4 4 0 0 1 4 4v3a4 4 0 0 0 4 4"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <circle cx="9.5" cy="10.5" r="3" fill="var(--accent-contrast)" />
      <circle cx="23" cy="21.5" r="3" fill="var(--accent-contrast)" />
    </svg>
  );
}

const MODES: Record<string, { tone: Tone; icon: IconName; hint: string }> = {
  Live: { tone: "accent", icon: "live", hint: "Connected to a running pydantic-ai-viz session." },
  Recording: { tone: "neutral", icon: "file", hint: "Replaying a saved .pviz.jsonl recording." },
  Export: { tone: "neutral", icon: "file", hint: "A self-contained export. It works offline." },
};

export function ModeChip({ source }: { source: Source }) {
  const label = modeLabel(source);
  if (!label) return null;
  const mode = MODES[label];
  if (!mode) return null;
  return (
    <Tooltip content={mode.hint}>
      <span className="inline-flex">
        <Chip tone={mode.tone} icon={mode.icon}>
          {label}
        </Chip>
      </span>
    </Tooltip>
  );
}

const CONNECTION: Record<Connection, { text: string; tone: Tone }> = {
  connecting: { text: "Connecting", tone: "neutral" },
  open: { text: "Connected", tone: "ok" },
  reconnecting: { text: "Reconnecting", tone: "retry" },
};

export function ConnectionStatus({ connection }: { connection: Connection | null }) {
  if (!connection) return null;
  const { text, tone } = CONNECTION[connection];
  return (
    <span className={cx(toneClass[tone], "inline-flex items-center gap-1.5 text-meta text-muted")}>
      <span
        aria-hidden="true"
        className={cx("size-1.5 rounded-full bg-(--tone)", connection !== "open" && "pulse-ring")}
      />
      {text}
    </span>
  );
}

type RerunState = "idle" | "sending" | "busy" | "failed";

/** Demo mode only: asks the server to run the scenario again. */
export function RunAgain({ url }: { url: string }) {
  const [state, setState] = useState<RerunState>("idle");
  const rerun = async () => {
    setState("sending");
    try {
      const response = await fetch(url, { method: "POST" });
      // 409 means a run is still in flight; anything else unexpected is a failure.
      setState(response.status === 202 ? "idle" : response.status === 409 ? "busy" : "failed");
    } catch {
      setState("failed");
    }
  };
  const note =
    state === "busy" ? "A run is still going" : state === "failed" ? "Could not start a run" : "";
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="status"
        className={cx("text-meta", state === "failed" ? "text-error" : "text-muted")}
      >
        {note}
      </span>
      <Button icon="play" onClick={rerun} disabled={state === "sending"}>
        Run again
      </Button>
    </span>
  );
}

export function ExportMenu({ source }: { source: Extract<Source, { kind: "live" }> }) {
  if (!source.exportHtmlUrl && !source.exportJsonlUrl) return null;
  return (
    <Popover label="Export" trigger={<Button icon="download">Export</Button>}>
      <div className="flex flex-col gap-2">
        {source.exportHtmlUrl && (
          <ButtonLink href={source.exportHtmlUrl} download icon="file" className="justify-start">
            Single-file HTML
          </ButtonLink>
        )}
        {source.exportJsonlUrl && (
          <ButtonLink href={source.exportJsonlUrl} download icon="span" className="justify-start">
            Recording (.pviz.jsonl)
          </ButtonLink>
        )}
        <p className="tone-retry m-0 flex gap-1.5 text-data text-muted">
          <Icon name="warning" size={14} className="tone-text mt-px" />
          <span>
            Exports contain everything captured: prompts, tool arguments and tool results. Check
            before you share one.
          </span>
        </p>
      </div>
    </Popover>
  );
}

export function ThemeToggle() {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Tooltip content={`Switch to ${next} theme (t)`}>
      <IconButton
        icon={theme === "dark" ? "sun" : "moon"}
        label={`Switch to ${next} theme`}
        onClick={toggleTheme}
      />
    </Tooltip>
  );
}

export function TopBar({ route, onShowShortcuts }: { route: Route; onShowShortcuts: () => void }) {
  const source = useViewer((state) => state.source);
  const connection = useViewer((state) => state.connection);
  const title = useViewer((state) => state.data.session?.title ?? null);
  const hasGallery = useViewer((state) => state.gallery !== null);
  const offContent = useViewer((state) => state.data.session?.include_content === false);

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-line border-b bg-surface px-3">
      <a
        href={formatRoute({ name: "home" })}
        className="flex items-center gap-2 text-fg no-underline"
        aria-label="pydantic-ai-viz home"
      >
        <Logo />
        <span className="font-semibold text-em tracking-[-0.01em] max-sm:hidden">
          pydantic-ai-viz
        </span>
      </a>
      {title && (
        <>
          <span aria-hidden="true" className="h-4 w-px bg-line-strong" />
          <h1 className="m-0 min-w-0 truncate font-medium text-em" title={title}>
            {title}
          </h1>
        </>
      )}
      <ModeChip source={source} />
      {offContent && (
        <Tooltip content="This session was captured with include_content=False. Structure and timing are shown; prompts, arguments and results are not.">
          <span className="inline-flex">
            <Chip icon="eyeOff">Content off</Chip>
          </span>
        </Tooltip>
      )}
      <ConnectionStatus connection={connection} />

      <div className="ml-auto flex items-center gap-1.5">
        {source.kind === "live" && source.rerunUrl && <RunAgain url={source.rerunUrl} />}
        {source.kind === "live" && <ExportMenu source={source} />}
        {hasGallery && (
          <ButtonLink
            href={formatRoute({ name: "gallery" })}
            variant="ghost"
            icon="gallery"
            aria-current={route.name === "gallery" ? "page" : undefined}
          >
            Gallery
          </ButtonLink>
        )}
        <ThemeToggle />
        <Tooltip content="Keyboard shortcuts (?)">
          <IconButton icon="keyboard" label="Keyboard shortcuts" onClick={onShowShortcuts} />
        </Tooltip>
      </div>
    </header>
  );
}
