/**
 * The three-slot layout every data screen uses:
 *
 *   +--------------------------+------------+
 *   | main (canvas)            | inspector  |   inspector: resizable, collapsible; a drawer
 *   |                          |            |   over the canvas in a window under 1024 px
 *   +--------------------------+------------+
 *   | dock (timeline)                       |   dock: run view only
 *   +---------------------------------------+
 *
 * A screen passes what it has; a missing slot takes no space.
 */
import { type KeyboardEvent, type PointerEvent, type ReactNode, useRef, useState } from "react";
import { IconButton } from "../ui/Button";
import { Tooltip } from "../ui/overlays";

const MIN_WIDTH = 280;
const MAX_WIDTH = 720;
/** Narrower in a small window, where the canvas needs the room more. */
const defaultWidth = (): number => (window.innerWidth < 1280 ? 300 : 360);
const WIDTH_KEY = "pviz.inspector.width";
const COLLAPSED_KEY = "pviz.inspector.collapsed";

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not remembered on file:// or in private windows; the layout still works.
  }
}

const clampWidth = (width: number): number => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width));

export function Workspace({
  main,
  inspector,
  dock,
  inspectorLabel = "Inspector",
}: {
  main: ReactNode;
  inspector?: ReactNode;
  dock?: ReactNode;
  inspectorLabel?: string;
}) {
  const [width, setWidth] = useState(() => {
    const stored = Number(readStored(WIDTH_KEY));
    return stored > 0 ? clampWidth(stored) : defaultWidth();
  });
  // Below 1024 px the inspector is a drawer over the canvas, closed until asked for.
  const [collapsed, setCollapsed] = useState(
    () => (readStored(COLLAPSED_KEY) ?? (window.innerWidth < 1024 ? "1" : "0")) === "1",
  );
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  const resize = (next: number): void => {
    const clamped = clampWidth(next);
    setWidth(clamped);
    store(WIDTH_KEY, String(clamped));
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0) return;
    drag.current = { startX: event.clientX, startWidth: width };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>): void => {
    if (!drag.current) return;
    // The inspector is on the right: dragging left makes it wider.
    resize(drag.current.startWidth + (drag.current.startX - event.clientX));
  };
  const onPointerUp = (): void => {
    drag.current = null;
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const step = event.shiftKey ? 64 : 16;
    if (event.key === "ArrowLeft") resize(width + step);
    else if (event.key === "ArrowRight") resize(width - step);
    else if (event.key === "Home") resize(MAX_WIDTH);
    else if (event.key === "End") resize(MIN_WIDTH);
    else return;
    event.preventDefault();
  };

  const toggle = (): void => {
    setCollapsed(!collapsed);
    store(COLLAPSED_KEY, collapsed ? "0" : "1");
  };

  const showInspector = inspector !== undefined && !collapsed;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* Below 1024px the inspector slides over the main area instead of squeezing it. */}
      <div className="relative flex min-h-0 flex-1">
        <main className="relative min-h-0 min-w-0 flex-1 overflow-auto">
          {main}
          {inspector !== undefined && (
            <div className="absolute top-2 right-2">
              <Tooltip
                content={collapsed ? "Show the inspector" : "Hide the inspector"}
                side="left"
              >
                <IconButton
                  icon="panelRight"
                  label={`${collapsed ? "Show" : "Hide"} ${inspectorLabel.toLowerCase()}`}
                  aria-expanded={!collapsed}
                  onClick={toggle}
                  className="border-line bg-surface"
                />
              </Tooltip>
            </div>
          )}
        </main>
        {showInspector && (
          <>
            {/* biome-ignore lint/a11y/useSemanticElements: a focusable splitter is the window-splitter ARIA pattern; <hr> cannot take focus or a value. */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label={`Resize ${inspectorLabel.toLowerCase()}`}
              aria-valuenow={width}
              aria-valuemin={MIN_WIDTH}
              aria-valuemax={MAX_WIDTH}
              tabIndex={0}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onKeyDown={onKeyDown}
              className="-mx-[3px] z-10 w-[7px] shrink-0 cursor-col-resize touch-none bg-line bg-clip-content px-[3px] transition-colors duration-150 ease-out hover:bg-accent focus-visible:bg-accent max-lg:hidden"
            />
            <aside
              aria-label={inspectorLabel}
              style={{ width }}
              className="flex min-h-0 shrink-0 animate-fade-in flex-col overflow-hidden bg-surface max-lg:absolute max-lg:top-11 max-lg:right-0 max-lg:bottom-0 max-lg:z-20 max-lg:w-[min(360px,90%)]! max-lg:border-line max-lg:border-t max-lg:border-l max-lg:shadow-float"
            >
              {inspector}
            </aside>
          </>
        )}
      </div>
      {dock !== undefined && (
        <section
          aria-label="Timeline"
          className="flex h-[31vh] max-h-[420px] min-h-40 shrink-0 flex-col border-line border-t bg-surface"
        >
          {dock}
        </section>
      )}
    </div>
  );
}
