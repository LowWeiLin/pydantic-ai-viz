/** Layout pieces shared by the screens. */
import type { ReactNode } from "react";
import { Icon, type IconName } from "../ui/Icon";
import { cx, type Tone, toneClass } from "../ui/tone";

export function ScreenHeader({
  icon,
  tone,
  kind,
  title,
  description,
  chips,
  aside,
}: {
  icon: IconName;
  tone: Tone;
  /** The small label above the title: "Agent blueprint", "Run". */
  kind: string;
  title: string;
  description?: string | null;
  chips?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start gap-x-6 gap-y-3 pr-10">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span
          className={cx(
            toneClass[tone],
            "tone-soft mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-card border",
          )}
        >
          <Icon name={icon} size={18} />
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 font-semibold text-faint text-meta uppercase tracking-[0.06em]">
            {kind}
          </p>
          <h2 className="m-0 truncate font-semibold text-page tracking-[-0.01em]">{title}</h2>
          {description && <p className="m-0 text-em text-muted">{description}</p>}
          {chips && <div className="mt-1 flex flex-wrap items-center gap-1.5">{chips}</div>}
        </div>
      </div>
      {aside}
    </header>
  );
}

export function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="m-0 flex items-baseline gap-2 font-semibold text-title">
        {title}
        {count !== undefined && <span className="font-normal text-faint text-ui">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

/** A bordered block on the surface colour. Cards are flat: a 1px border, no shadow. */
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("rounded-card border border-line bg-surface", className)}>{children}</div>
  );
}

/** Label and value pairs, e.g. settings. */
export function Facts({ items }: { items: [label: string, value: ReactNode][] }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-ui">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-faint">{label}</dt>
          <dd className="m-0 min-w-0 break-words text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A value that may be unknown: null is shown as a dash with a spoken name. */
export function orDash(value: string | number | null | undefined): ReactNode {
  if (value === null || value === undefined || value === "") {
    return (
      <span className="text-faint">
        <span aria-hidden="true">-</span>
        <span className="sr-only">not set</span>
      </span>
    );
  }
  return value;
}
