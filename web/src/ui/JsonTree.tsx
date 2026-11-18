/**
 * A collapsible view of any JSON value. Everything is rendered as text nodes, never as HTML,
 * so tool results and prompts cannot inject markup.
 */
import { useState } from "react";
import type { Json } from "../wire";
import { CopyButton } from "./CopyButton";
import { Icon } from "./Icon";
import { cx } from "./tone";

/** Strings longer than this start folded. */
const LONG_STRING = 320;
/** Containers with more children than this show the first batch and a "show more" row. */
const PAGE = 100;

function summary(value: Json[] | { [k: string]: Json }): string {
  if (Array.isArray(value)) return value.length === 1 ? "1 item" : `${value.length} items`;
  const n = Object.keys(value).length;
  return n === 1 ? "1 key" : `${n} keys`;
}

function Primitive({ value }: { value: null | boolean | number | string }) {
  const [open, setOpen] = useState(false);
  if (value === null) return <span className="text-faint italic">null</span>;
  if (typeof value === "boolean") return <span className="text-agent">{String(value)}</span>;
  if (typeof value === "number") return <span className="text-model">{String(value)}</span>;
  if (value.length <= LONG_STRING || open) {
    return (
      <span className="whitespace-pre-wrap break-words text-fg [overflow-wrap:anywhere]">
        <span className="text-faint select-none">"</span>
        {value}
        <span className="text-faint select-none">"</span>
        {value.length > LONG_STRING && (
          <button type="button" onClick={() => setOpen(false)} className="ml-2 text-accent">
            Show less
          </button>
        )}
      </span>
    );
  }
  return (
    <span className="whitespace-pre-wrap break-words text-fg [overflow-wrap:anywhere]">
      <span className="text-faint select-none">"</span>
      {value.slice(0, LONG_STRING)}
      <span className="text-faint">...</span>
      <button type="button" onClick={() => setOpen(true)} className="ml-2 text-accent">
        Show all {value.length.toLocaleString("en-US")} characters
      </button>
    </span>
  );
}

function Node({
  name,
  value,
  depth,
  openDepth,
}: {
  name: string | null;
  value: Json;
  depth: number;
  openDepth: number;
}) {
  const isContainer = typeof value === "object" && value !== null;
  const [open, setOpen] = useState(depth < openDepth);
  const [limit, setLimit] = useState(PAGE);

  const key = name !== null && <span className="text-muted">{name}: </span>;

  if (!isContainer) {
    return (
      <div className="py-px pl-4">
        {key}
        <Primitive value={value} />
      </div>
    );
  }

  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((item, i) => [String(i), item])
    : Object.entries(value);
  const [openMark, closeMark] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];

  if (entries.length === 0) {
    return (
      <div className="py-px pl-4">
        {key}
        <span className="text-faint">
          {openMark}
          {closeMark}
        </span>
      </div>
    );
  }

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1 rounded-[4px] py-px text-left hover:bg-surface-2"
      >
        <Icon
          name="chevronRight"
          size={12}
          className={cx(
            "text-faint transition-transform duration-150 ease-out",
            open && "rotate-90",
          )}
        />
        <span>
          {key}
          <span className="text-faint">
            {openMark}
            {open ? "" : ` ${summary(value)} ${closeMark}`}
          </span>
        </span>
      </button>
      {open && (
        <>
          <div className="ml-[5px] border-line border-l pl-1.5">
            {entries.slice(0, limit).map(([k, v]) => (
              <Node key={k} name={k} value={v} depth={depth + 1} openDepth={openDepth} />
            ))}
            {entries.length > limit && (
              <button
                type="button"
                onClick={() => setLimit(limit + PAGE)}
                className="py-px pl-4 text-accent"
              >
                Show {Math.min(PAGE, entries.length - limit)} more of {entries.length}
              </button>
            )}
          </div>
          <div className="pl-4 text-faint">{closeMark}</div>
        </>
      )}
    </div>
  );
}

export function JsonTree({
  value,
  label = "JSON",
  openDepth = 2,
  copyable = true,
  className,
}: {
  value: Json;
  /** Names the value for the copy button: "Copy arguments". */
  label?: string;
  /** How many levels start expanded. */
  openDepth?: number;
  copyable?: boolean;
  className?: string;
}) {
  return (
    <div className={cx("group/json relative font-mono text-data", className)}>
      {copyable && (
        <CopyButton
          text={() => (typeof value === "string" ? value : JSON.stringify(value, null, 2))}
          label={`Copy ${label}`}
          compact
          className="absolute top-0 right-0 bg-surface"
        />
      )}
      <div className={copyable ? "pr-8" : undefined}>
        <Node name={null} value={value} depth={0} openDepth={openDepth} />
      </div>
    </div>
  );
}
