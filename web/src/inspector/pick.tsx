/**
 * Links between inspector panels. A panel that names another part of the blueprint (a tool of
 * a toolset, the toolset of a tool, a node an edge leads to) lets it be picked from there, and
 * the screen treats that exactly as a click on the canvas: the node is outlined, the inspector
 * shows it, and on a run its latest span is selected.
 */
import { createContext, type MouseEvent, type ReactNode, useContext } from "react";
import type { CanvasTarget } from "../canvas/targets";
import { cx } from "../ui/tone";

/** Picks a part; null picks the subject of the screen itself (a graph has no target of its own). */
export type PickTarget = (target: CanvasTarget | null) => void;

/** Provided by the screen around its inspector. Without it, names are shown as plain text. */
export const InspectorPick = createContext<PickTarget | null>(null);

/**
 * After a pick the panel is replaced and the link that was used is gone: focus goes to the new
 * panel's title, so the keyboard stays in the inspector rather than falling back to the page.
 */
function focusNewTitle(event: MouseEvent<HTMLElement>): void {
  const panel = event.currentTarget.closest("aside");
  requestAnimationFrame(() => panel?.querySelector<HTMLElement>("h3[tabindex]")?.focus());
}

/**
 * The name of another part, as a link-styled button that picks it. The name is the button's
 * accessible name: where it stands (a list of a toolset's tools, "of agent ...") says what it is.
 */
export function PickLink({
  target,
  className,
  children,
}: {
  target: CanvasTarget | null;
  className?: string;
  children: ReactNode;
}) {
  const pick = useContext(InspectorPick);
  if (!pick) return <span className={className}>{children}</span>;
  return (
    <button
      type="button"
      onClick={(event) => {
        focusNewTitle(event);
        pick(target);
      }}
      className={cx(
        "inline cursor-pointer rounded-[3px] border-0 bg-transparent p-0 text-left underline decoration-line-strong underline-offset-2 hover:decoration-current",
        className,
      )}
    >
      {children}
    </button>
  );
}

/**
 * A row in a list of parts (the tools of a toolset): the whole row picks its part. Without a
 * screen to pick on it is a plain row.
 */
export function PickRow({
  target,
  className,
  children,
}: {
  target: CanvasTarget;
  className?: string;
  children: ReactNode;
}) {
  const pick = useContext(InspectorPick);
  const base = "flex w-full min-w-0 flex-col gap-0.5 rounded-control px-2 py-1.5 text-left";
  if (!pick) return <div className={cx(base, className)}>{children}</div>;
  return (
    <button
      type="button"
      onClick={(event) => {
        focusNewTitle(event);
        pick(target);
      }}
      className={cx(
        base,
        "cursor-pointer border-0 bg-transparent transition-colors duration-150 ease-out hover:bg-surface-2",
        className,
      )}
    >
      {children}
    </button>
  );
}
