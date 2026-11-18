import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { cx } from "./tone";

/**
 * Copies text. `navigator.clipboard` needs a secure context, which a `file://` export is not
 * in every browser, so there is a selection-based fallback.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path.
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

type State = "idle" | "copied" | "failed";

/** A copy control that confirms in place ("Copied") and announces it. */
export function CopyButton({
  text,
  label = "Copy",
  compact = false,
  className,
}: {
  /** The text, or a function called at click time (for large values). */
  text: string | (() => string);
  /** What is being copied, e.g. "Copy JSON". */
  label?: string;
  /** Icon only; the label stays as the accessible name. */
  compact?: boolean;
  className?: string;
}) {
  const [state, setState] = useState<State>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onClick = async () => {
    const ok = await copyText(typeof text === "function" ? text() : text);
    setState(ok ? "copied" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1600);
  };

  const shown = state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={compact ? label : undefined}
      className={cx(
        "inline-flex h-6 items-center gap-1 rounded-control border border-transparent px-1.5",
        "text-meta font-medium text-muted transition-colors duration-150 ease-out",
        "hover:border-line hover:bg-surface-2 hover:text-fg",
        state === "copied" && "tone-ok tone-text",
        state === "failed" && "tone-error tone-text",
        className,
      )}
    >
      <Icon name={state === "copied" ? "check" : "copy"} size={13} />
      {!compact && <span>{shown}</span>}
      <span role="status" className="sr-only">
        {state === "idle" ? "" : shown}
      </span>
    </button>
  );
}
