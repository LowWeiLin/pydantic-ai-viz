import type { ReactNode } from "react";
import { CopyButton } from "./CopyButton";
import { Icon, type IconName } from "./Icon";
import { cx, type Tone, toneClass } from "./tone";

/**
 * The one layout for "nothing here yet" and "this went wrong": what happened, why, and what
 * to do next. Never a blank panel.
 */
export function EmptyState({
  icon,
  tone = "neutral",
  title,
  children,
  actions,
  className,
}: {
  icon: IconName;
  tone?: Tone;
  title: string;
  /** One or two sentences, and any snippets. */
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        "mx-auto flex w-full max-w-[520px] flex-col items-start gap-3 px-6 py-10",
        className,
      )}
    >
      <span
        className={cx(
          toneClass[tone],
          "tone-soft flex size-9 items-center justify-center rounded-card border",
        )}
      >
        <Icon name={icon} size={18} />
      </span>
      <h2 className="m-0 font-semibold text-page tracking-[-0.01em]">{title}</h2>
      {children && <div className="flex w-full flex-col gap-3 text-em text-muted">{children}</div>}
      {actions && <div className="mt-1 flex flex-wrap items-center gap-2">{actions}</div>}
    </section>
  );
}

/** A copyable command or code snippet, shown as text. */
export function Snippet({ code, label }: { code: string; label: string }) {
  return (
    <div className="flex items-start justify-between gap-2 rounded-control border border-line bg-surface py-1.5 pr-1.5 pl-3">
      <pre className="m-0 min-w-0 flex-1 overflow-x-auto whitespace-pre py-0.5 text-data text-fg">
        <code>{code}</code>
      </pre>
      <CopyButton text={code} label={label} compact />
    </div>
  );
}

/** Shown wherever content would be when the session was captured with include_content=False. */
export function Redacted({ what = "Content" }: { what?: string }) {
  return (
    <p className="m-0 flex items-center gap-1.5 text-data text-faint">
      <Icon name="eyeOff" size={14} />
      <span>
        {what} not captured. Content capture is off (<code>include_content=False</code>).
      </span>
    </p>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cx("skeleton", className)} />;
}
