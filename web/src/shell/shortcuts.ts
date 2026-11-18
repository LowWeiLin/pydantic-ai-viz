/**
 * Keyboard shortcuts. `SHORTCUTS` is the single list: the dialog renders it, and components
 * bind handlers to its ids with `useShortcuts`. Shortcuts never fire while the user is typing
 * or holding a modifier, so browser and OS shortcuts keep working.
 */
import { useEffect, useRef } from "react";

export const SHORTCUTS = [
  { id: "nextSpan", keys: ["j"], label: "Next span in the timeline", group: "Timeline" },
  { id: "prevSpan", keys: ["k"], label: "Previous span in the timeline", group: "Timeline" },
  { id: "clearSelection", keys: ["Esc"], label: "Clear selection", group: "Timeline" },
  { id: "togglePlay", keys: ["Space"], label: "Play or pause", group: "Playback" },
  {
    id: "stepBack",
    keys: ["Left"],
    label: "Step to the previous span boundary",
    group: "Playback",
  },
  {
    id: "stepForward",
    keys: ["Right"],
    label: "Step to the next span boundary",
    group: "Playback",
  },
  { id: "slower", keys: ["["], label: "Slower", group: "Playback" },
  { id: "faster", keys: ["]"], label: "Faster", group: "Playback" },
  { id: "goLive", keys: ["l"], label: "Jump to live (the end)", group: "Playback" },
  { id: "fitCanvas", keys: ["f"], label: "Fit the canvas", group: "View" },
  { id: "toggleTheme", keys: ["t"], label: "Toggle light and dark", group: "View" },
  { id: "showShortcuts", keys: ["?"], label: "Show this list", group: "View" },
] as const;

export type ShortcutId = (typeof SHORTCUTS)[number]["id"];

/** `KeyboardEvent.key` for each shortcut. */
const KEY_TO_ID: Record<string, ShortcutId> = {
  j: "nextSpan",
  k: "prevSpan",
  Escape: "clearSelection",
  " ": "togglePlay",
  ArrowLeft: "stepBack",
  ArrowRight: "stepForward",
  "[": "slower",
  "]": "faster",
  l: "goLive",
  f: "fitCanvas",
  t: "toggleTheme",
  "?": "showShortcuts",
};

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

/** Keys that already mean something on a focused control: they are left to the control. */
function controlOwnsKey(target: EventTarget | null, key: string): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const interactive = target.closest(
    "button, a[href], summary, [role='tab'], [role='slider'], [role='treeitem'], [role='separator']",
  );
  if (!interactive) return false;
  if (key === " ") return true;
  if (key === "ArrowLeft" || key === "ArrowRight") {
    const role = interactive.getAttribute("role");
    return role === "tab" || role === "slider" || role === "treeitem" || role === "separator";
  }
  return false;
}

export type ShortcutHandlers = Partial<Record<ShortcutId, () => void>>;

/**
 * Binds handlers while the component is mounted. Several components may bind different ids,
 * but each id should be bound in one place: the first listener to handle a key wins.
 */
export function useShortcuts(handlers: ShortcutHandlers, enabled = true): void {
  const latest = useRef(handlers);
  latest.current = handlers;

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTyping(event.target)) return;
      // An open dialog or popover owns the keyboard (Radix closes it on Escape itself).
      if (document.querySelector("[role='dialog']")) return;
      const id = KEY_TO_ID[event.key];
      if (!id || controlOwnsKey(event.target, event.key)) return;
      const handler = latest.current[id];
      if (!handler) return;
      event.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
