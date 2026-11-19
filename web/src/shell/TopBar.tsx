import { useState } from "react";
import { type Source, useViewer } from "../data/store";
import { InlineCode } from "../inspector/source";
import { Chip } from "../ui/Badge";
import { Button, ButtonLink, IconButton } from "../ui/Button";
import { Icon, type IconName } from "../ui/Icon";
import { Popover, Tooltip } from "../ui/overlays";
import { cx, type Tone } from "../ui/tone";
import { plural } from "./format";
import { formatRoute, type Route } from "./router";
import { type ModeChip as ModeChipKind, useShellStatus } from "./status";
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

/** One or two words for what kind of session this is; the hint is the tooltip. */
const MODES: Record<ModeChipKind, { label: string; tone: Tone; icon: IconName; hint: string }> = {
  connecting: {
    label: "Connecting",
    tone: "neutral",
    icon: "running",
    hint: "Reaching the pydantic-ai-viz session behind this page.",
  },
  live: {
    label: "Live",
    tone: "accent",
    icon: "live",
    hint: "Connected to a running program. Its runs appear here as they happen.",
  },
  blueprint: {
    label: "Blueprint",
    tone: "neutral",
    icon: "blueprint",
    hint: "This view holds blueprints only: what the agents are, not what they did. Nothing in it runs.",
  },
  recording: {
    label: "Recording",
    tone: "neutral",
    icon: "file",
    hint: "Replaying a saved .pviz.jsonl recording.",
  },
  export: {
    label: "Export",
    tone: "neutral",
    icon: "file",
    hint: "A self-contained export. It works offline.",
  },
};

export function ModeChip({ chip }: { chip: ModeChipKind | null }) {
  if (!chip) return null;
  const mode = MODES[chip];
  return (
    <Tooltip content={mode.hint}>
      <span className="inline-flex" data-mode={chip}>
        <Chip tone={mode.tone} icon={mode.icon} pulse={chip === "connecting"}>
          {mode.label}
        </Chip>
      </span>
    </Tooltip>
  );
}

/** Messages about the session as a whole, e.g. files that static analysis had to skip. */
export function Notices({ notices }: { notices: readonly string[] }) {
  if (notices.length === 0) return null;
  const label = plural(notices.length, "notice");
  return (
    <Popover
      label="Session notices"
      align="start"
      wide
      trigger={
        <Button size="sm" variant="ghost" icon="info" className="border-line">
          {label}
        </Button>
      }
    >
      <p className="m-0 mb-2 font-semibold text-faint text-meta uppercase tracking-[0.06em]">
        About the session as a whole
      </p>
      <ul className="m-0 flex max-h-80 list-none flex-col overflow-y-auto p-0 text-ui">
        {notices.map((notice) => (
          <li
            key={notice}
            className="break-words border-line border-t py-1.5 text-fg first:border-t-0 first:pt-0"
          >
            <InlineCode text={notice} />
          </li>
        ))}
      </ul>
    </Popover>
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

export function ExportMenu({
  source,
  blueprintOnly,
}: {
  source: Extract<Source, { kind: "live" }>;
  /** A blueprint-only session has no captured content to warn about. */
  blueprintOnly: boolean;
}) {
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
            {blueprintOnly ? "Blueprints (.pviz.jsonl)" : "Recording (.pviz.jsonl)"}
          </ButtonLink>
        )}
        {blueprintOnly ? (
          <p className="m-0 flex gap-1.5 text-data text-muted">
            <Icon name="info" size={14} className="mt-px text-faint" />
            <span>
              Exports hold what the blueprints show: instructions, tool names and schemas. There is
              no run data in them.
            </span>
          </p>
        ) : (
          <p className="tone-retry m-0 flex gap-1.5 text-data text-muted">
            <Icon name="warning" size={14} className="tone-text mt-px" />
            <span>
              Exports contain everything captured: prompts, tool arguments and tool results. Check
              before you share one.
            </span>
          </p>
        )}
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
  const title = useViewer((state) => state.data.session?.title ?? null);
  const hasGallery = useViewer((state) => state.gallery !== null);
  const status = useShellStatus();

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
      <ModeChip chip={status.chip} />
      {status.contentOff && (
        <Tooltip content="This session was captured with include_content=False. Structure and timing are shown; prompts, arguments and results are not.">
          <span className="inline-flex">
            <Chip icon="eyeOff">Content off</Chip>
          </span>
        </Tooltip>
      )}
      <Notices notices={status.notices} />

      <div className="ml-auto flex items-center gap-1.5">
        {source.kind === "live" && source.rerunUrl && <RunAgain url={source.rerunUrl} />}
        {source.kind === "live" && (
          <ExportMenu source={source} blueprintOnly={status.about !== null} />
        )}
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
