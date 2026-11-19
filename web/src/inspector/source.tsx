/**
 * How a blueprint fact is known, and where it is written: the provenance chip with its legend,
 * a source location, and a value the code only works out at run time.
 */
import { Fragment, useState } from "react";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { Icon, type IconName } from "../ui/Icon";
import { Popover } from "../ui/overlays";
import { cx } from "../ui/tone";
import type { Provenance, SourceLocation } from "../wire";

const PROVENANCE: Record<Provenance, { label: string; icon: IconName; meaning: string }> = {
  code: {
    label: "From source",
    icon: "code",
    meaning: "Read from the source code. The code was parsed, not run.",
  },
  object: {
    label: "From a live object",
    icon: "object",
    meaning: "Read from the Python object in a running program.",
  },
  observed: {
    label: "Observed in runs",
    icon: "eye",
    meaning: "Pieced together from what runs did. Only what happened is known.",
  },
};

const ORDER: Provenance[] = ["code", "object", "observed"];

/** How this blueprint (or tool) was obtained. Opens the legend of all three ways. */
export function ProvenanceChip({ source }: { source: Provenance }) {
  // An unknown value from a newer backend is shown as it is, without a legend entry.
  const known = PROVENANCE[source] as (typeof PROVENANCE)[Provenance] | undefined;
  if (!known) return <Chip>{source}</Chip>;
  return (
    <Popover
      label="How a blueprint is known"
      align="start"
      trigger={
        <button
          type="button"
          aria-label={`${known.label}. How a blueprint is known`}
          className="inline-flex cursor-pointer rounded-control border-0 bg-transparent p-0"
        >
          <Chip icon={known.icon}>{known.label}</Chip>
        </button>
      }
    >
      <p className="m-0 mb-2 font-semibold text-faint text-meta uppercase tracking-[0.06em]">
        How a blueprint is known
      </p>
      <dl className="m-0 flex flex-col gap-2 text-ui">
        {ORDER.map((key) => {
          const entry = PROVENANCE[key];
          const current = key === source;
          return (
            <div key={key} data-current={current || undefined}>
              <dt className={cx("flex items-center gap-2 text-fg", current && "font-semibold")}>
                <Icon
                  name={entry.icon}
                  size={14}
                  className={current ? "text-accent" : "text-faint"}
                />
                <span>
                  {entry.label}
                  {current && <span className="font-normal text-muted"> (this one)</span>}
                </span>
              </dt>
              <dd className="m-0 pl-[22px] text-muted">{entry.meaning}</dd>
            </div>
          );
        })}
      </dl>
    </Popover>
  );
}

/** "desk.py:18", as an editor's go-to-file box takes it. */
export const formatLocation = (location: SourceLocation): string =>
  `${location.file}:${location.line}`;

/**
 * Where something is defined in the analysed source, with a button that copies it. A long path
 * gives way from its folder: the file name and the line are what an editor needs, so they are
 * the last to be cut. The whole location is the title and what is copied.
 */
export function SourceRef({
  location,
  prefix = "Defined at",
}: {
  location: SourceLocation | null | undefined;
  /** The words before the location; pass "" for a bare location. */
  prefix?: string;
}) {
  if (!location) return null;
  const text = formatLocation(location);
  const cut = location.file.lastIndexOf("/") + 1;
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1 text-faint text-meta">
      {prefix !== "" && <span className="shrink-0">{prefix}</span>}
      <code className="flex min-w-0 text-[11px] text-muted" title={text}>
        {cut > 0 && <span className="min-w-0 truncate">{location.file.slice(0, cut)}</span>}
        <span className="max-w-full shrink-0 truncate">{text.slice(cut)}</span>
      </code>
      <CopyButton text={text} label={`Copy ${text}`} compact className="h-5 shrink-0 px-1" />
    </span>
  );
}

const LONG_EXPRESSION = 240;

/**
 * Source text that stands in for a value the code works out at run time: set as code, with a
 * dashed edge, and labelled, so that it is not mistaken for the value itself.
 */
export function Expression({ code, hint = true }: { code: string; hint?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = code.length > LONG_EXPRESSION;
  return (
    <span className="flex min-w-0 flex-col items-start gap-1">
      <code className="max-w-full whitespace-pre-wrap break-words rounded-control border border-line-strong border-dashed bg-bg px-1.5 py-0.5 text-fg">
        {long && !open ? `${code.slice(0, LONG_EXPRESSION)}...` : code}
      </code>
      {long && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="border-0 bg-transparent p-0 font-medium text-accent text-data"
        >
          {open ? "Show less" : "Show the whole expression"}
        </button>
      )}
      {hint && (
        <span className="flex items-center gap-1 text-faint text-meta">
          <Icon name="code" size={12} />
          Decided at run time
        </span>
      )}
    </span>
  );
}

/** An f-string as written: the `{...}` parts are set apart, since run time fills them in. */
export function TemplateText({ text }: { text: string }) {
  const parts = text.split(/(\{[^{}\n]*\})/g);
  return (
    <div className="whitespace-pre-wrap break-words text-fg text-ui">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code
            key={i}
            className="rounded-[3px] border border-line-strong border-dashed bg-bg px-0.5 text-accent"
          >
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </div>
  );
}

/** A sentence in which `backticks` mark code, as the backend writes its warnings. */
export function InlineCode({ text }: { text: string }) {
  const parts = text.split(/`([^`]+)`/g);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="break-words text-fg">
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
