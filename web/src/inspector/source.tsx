/**
 * How a blueprint fact is known, and where it is written: the provenance chip with its legend,
 * a source location, and a value the code only works out at run time.
 */
import { Fragment, useState } from "react";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { Icon, type IconName } from "../ui/Icon";
import { Popover } from "../ui/overlays";
import { PathText } from "../ui/PathText";
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
 * gives way from its folder, then from the end of the file name; the line is never cut. The
 * whole location is the title and what is copied.
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
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1 text-faint text-meta">
      {prefix !== "" && <span className="shrink-0">{prefix}</span>}
      <code className="flex min-w-0 text-[11px] text-muted" title={text}>
        <PathText path={location.file} suffix={`:${location.line}`} />
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

export type TemplatePart = { hole: boolean; text: string };

/**
 * A template in the syntax of `str.format`, as the backend writes it: literal text, in which a
 * doubled brace stands for one, and placeholders in braces, which may hold braces of their own
 * (`{ {"a": x}["a"]}`, `{total:{width}}`). Each placeholder keeps its braces.
 */
export function templateParts(text: string): TemplatePart[] {
  const parts: TemplatePart[] = [];
  let literal = "";
  let i = 0;
  while (i < text.length) {
    const char = text.charAt(i);
    if ((char === "{" || char === "}") && text.charAt(i + 1) === char) {
      literal += char;
      i += 2;
      continue;
    }
    if (char !== "{") {
      literal += char;
      i += 1;
      continue;
    }
    let depth = 1;
    let end = i + 1;
    while (end < text.length && depth > 0) {
      const inner = text.charAt(end);
      if (inner === "{") depth += 1;
      else if (inner === "}") depth -= 1;
      end += 1;
    }
    if (depth > 0) {
      literal += text.slice(i); // a brace that is never closed is text
      break;
    }
    if (literal) parts.push({ hole: false, text: literal });
    literal = "";
    parts.push({ hole: true, text: text.slice(i, end) });
    i = end;
  }
  if (literal) parts.push({ hole: false, text: literal });
  return parts;
}

/** A template as written: the `{...}` parts are set apart, since run time fills them in. */
export function TemplateText({ text }: { text: string }) {
  return (
    <div className="whitespace-pre-wrap break-words text-fg text-ui">
      {templateParts(text).map((part, i) =>
        part.hole ? (
          <code
            key={i}
            title="Filled in when the code runs"
            className="rounded-[3px] border border-line-strong border-dashed bg-bg px-0.5 text-accent"
          >
            {part.text}
          </code>
        ) : (
          <Fragment key={i}>{part.text}</Fragment>
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
