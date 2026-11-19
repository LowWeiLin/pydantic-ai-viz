/** Layout pieces shared by the screens and the inspector. */
import type { ReactNode } from "react";
import { plural } from "../shell/format";
import { Chip } from "../ui/Badge";
import { Icon, type IconName } from "../ui/Icon";
import { Tooltip } from "../ui/overlays";
import { cx, type Tone, toneClass } from "../ui/tone";

/**
 * The strip above a canvas: what this screen shows, with a few facts and actions. The right
 * padding keeps clear of the inspector toggle that `Workspace` puts in that corner.
 *
 * In a narrow window the facts wrap within their own column; the title and the actions stay
 * on the first line, so the bar grows by a line of chips, never by a line holding one button.
 * On a phone the chips take a line of their own under the title.
 */
export function ScreenBar({
  icon,
  tone,
  kind,
  title,
  chips,
  aside,
}: {
  icon: IconName;
  tone: Tone;
  /** The small label before the title: "Agent blueprint", "Run". */
  kind: string;
  title: string;
  chips?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="flex min-h-11 shrink-0 items-center gap-3 border-line border-b bg-surface py-1.5 pr-12 pl-3 max-sm:flex-wrap max-sm:gap-y-1.5">
      <span
        className={cx(
          toneClass[tone],
          "tone-soft flex size-7 shrink-0 items-center justify-center rounded-control border",
        )}
      >
        <Icon name={icon} size={16} />
      </span>
      <div className="flex min-w-0 flex-col">
        <p className="m-0 font-semibold text-[10px] text-faint uppercase leading-tight tracking-[0.08em]">
          {kind}
        </p>
        <h2 className="m-0 truncate font-semibold text-em leading-tight">{title}</h2>
      </div>
      {chips && (
        <div className="flex min-w-40 flex-1 flex-wrap items-center gap-1.5 max-sm:order-last max-sm:basis-full">
          {chips}
        </div>
      )}
      {aside && <div className="ml-auto flex shrink-0 items-center gap-2">{aside}</div>}
    </header>
  );
}

/**
 * How many warnings a blueprint has, above its canvas. The list is in the blueprint's panel:
 * the chip opens the panel there, so the count and the list are one fact in two sizes.
 */
export function WarningsChip({ count, onShow }: { count: number; onShow: () => void }) {
  if (count === 0) return null;
  const label = plural(count, "warning");
  return (
    <Tooltip content="Show them in the panel">
      <button
        type="button"
        onClick={onShow}
        aria-label={`${label}: show them in the panel`}
        className="inline-flex cursor-pointer rounded-control border-0 bg-transparent p-0"
      >
        <Chip icon="warning">{label}</Chip>
      </button>
    </Tooltip>
  );
}

/** Label and value pairs, e.g. settings. */
export function Facts({ items }: { items: [label: string, value: ReactNode][] }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(96px,auto)_1fr] gap-x-4 gap-y-1.5 text-ui">
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

/** A segmented control: one choice out of a few, as toggle buttons in a labelled group. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; icon?: IconName }[];
  onChange: (value: T) => void;
}) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: a fieldset would add a legend line; a labelled group is the toolbar pattern.
    <div
      role="group"
      aria-label={label}
      className="flex overflow-hidden rounded-control border border-line bg-surface"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            "flex h-7 items-center gap-1.5 border-0 border-line border-l px-2.5 text-ui first:border-l-0",
            "transition-colors duration-150 ease-out focus-visible:outline-offset-[-2px]",
            option.value === value
              ? "bg-surface-2 font-medium text-fg"
              : "bg-surface text-muted hover:bg-surface-2 hover:text-fg",
          )}
        >
          {option.icon && <Icon name={option.icon} size={14} />}
          {option.label}
        </button>
      ))}
    </div>
  );
}
