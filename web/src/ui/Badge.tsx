import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { cx, type Status, statusMeta, type Tone, toneClass } from "./tone";

/** A small count or flag: "3", "2 warnings". Tinted with its tone. */
export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        toneClass[tone],
        "tone-soft inline-flex h-4 min-w-4 items-center justify-center rounded-full border px-1",
        "text-meta font-semibold leading-none",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A labelled fact about something: model name, "offline", "Observed from runs". */
export function Chip({
  tone = "neutral",
  icon,
  mono = false,
  pulse = false,
  children,
  className,
}: {
  tone?: Tone;
  icon?: IconName;
  /** Monospace, for identifiers such as model and tool names. */
  mono?: boolean;
  /** The running ring. */
  pulse?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        toneClass[tone],
        "tone-soft inline-flex h-5 max-w-full items-center gap-1 rounded-control border px-1.5",
        "text-meta font-medium",
        mono && "font-mono",
        pulse && "pulse-ring",
        className,
      )}
    >
      {icon && <Icon name={icon} size={12} />}
      <span className="truncate">{children}</span>
    </span>
  );
}

/**
 * Status as an icon with its own shape. The word is always in the accessibility tree; pass
 * `showLabel` to print it as well (do so wherever there is room).
 */
export function StatusDot({
  status,
  showLabel = false,
  label,
  className,
}: {
  status: Status;
  showLabel?: boolean;
  /** Overrides the default word, e.g. "Retried twice". */
  label?: string;
  className?: string;
}) {
  const meta = statusMeta[status];
  const text = label ?? meta.label;
  return (
    <span
      className={cx(
        toneClass[meta.tone],
        "tone-text inline-flex items-center gap-1 text-meta font-medium",
        className,
      )}
    >
      <Icon
        name={meta.icon}
        size={14}
        className={status === "running" ? "animate-spin" : undefined}
      />
      <span className={showLabel ? undefined : "sr-only"}>{text}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-[4px] border border-line-strong border-b-2 bg-surface px-1 font-mono text-meta text-muted">
      {children}
    </kbd>
  );
}
