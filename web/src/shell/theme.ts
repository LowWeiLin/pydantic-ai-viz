/**
 * Theme: follows the system until the user toggles, then remembers the choice. `index.html`
 * applies the stored value before first paint; this module owns it afterwards.
 */
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "pviz.theme";
const listeners = new Set<() => void>();

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The explicit choice, or null while following the system. */
function chosenTheme(): Theme | null {
  const value = document.documentElement.getAttribute("data-theme");
  return value === "light" || value === "dark" ? value : null;
}

export function currentTheme(): Theme {
  return chosenTheme() ?? systemTheme();
}

export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // file:// pages and private windows may refuse storage; the theme still applies.
  }
  for (const listener of listeners) listener();
}

export function toggleTheme(): void {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  media?.addEventListener("change", onChange);
  return () => {
    listeners.delete(onChange);
    media?.removeEventListener("change", onChange);
  };
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme);
}
