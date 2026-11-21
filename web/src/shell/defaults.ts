/**
 * Whether the canvas and the inspector show what Pydantic AI adds to an agent by default. Hidden
 * until the viewer asks for it, and remembered per viewer like the theme, never in the link.
 */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "pviz.defaults";
const listeners = new Set<() => void>();
/** The choice when storage refuses to keep it (file:// pages, private windows). */
let unstored: boolean | null = null;

export function currentShowDefaults(): boolean {
  if (unstored !== null) return unstored;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "shown";
  } catch {
    return false;
  }
}

export function setShowDefaults(shown: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, shown ? "shown" : "hidden");
    unstored = null;
  } catch {
    unstored = shown;
  }
  for (const listener of listeners) listener();
}

export function toggleShowDefaults(): void {
  setShowDefaults(!currentShowDefaults());
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

export function useShowDefaults(): boolean {
  return useSyncExternalStore(subscribe, currentShowDefaults);
}
