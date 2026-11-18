import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetViewer } from "../data/store";
import { resetInspectorTabs } from "../inspector/parts";

// React Flow measures its container and nodes; jsdom has no layout, so give it the minimum.
if (typeof window !== "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  window.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
  class DOMMatrixStub {
    m22: number;
    constructor(transform?: string) {
      const scale = transform?.match(/scale\(([\d.]+)\)/)?.[1];
      this.m22 = scale === undefined ? 1 : Number(scale);
    }
  }
  window.DOMMatrixReadOnly ??= DOMMatrixStub as unknown as typeof DOMMatrixReadOnly;
  // The canvas pans with d3-zoom, which reads `event.view.document` on mousedown; events made
  // by user-event have no view. Tests click canvas nodes but never pan, so keep those away.
  window.addEventListener(
    "mousedown",
    (event) => {
      if (event.view === null && (event.target as Element | null)?.closest?.(".react-flow")) {
        event.stopPropagation();
      }
    },
    true,
  );
}

afterEach(() => {
  cleanup();
  resetViewer();
  resetInspectorTabs();
  if (typeof window === "undefined") return; // Suites that opt into the node environment.
  window.location.hash = "";
  document.documentElement.removeAttribute("data-theme");
  try {
    window.localStorage.clear();
  } catch {
    // Storage is optional everywhere in the app, and in tests too.
  }
});
