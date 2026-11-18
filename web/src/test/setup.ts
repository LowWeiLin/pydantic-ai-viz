import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetViewer } from "../data/store";

afterEach(() => {
  cleanup();
  resetViewer();
  if (typeof window === "undefined") return; // Suites that opt into the node environment.
  window.location.hash = "";
  document.documentElement.removeAttribute("data-theme");
  try {
    window.localStorage.clear();
  } catch {
    // Storage is optional everywhere in the app, and in tests too.
  }
});
