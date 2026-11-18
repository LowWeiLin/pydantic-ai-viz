/**
 * The shell: top bar, sidebar, and one screen chosen by the hash route. Data arrives through
 * the store (`data/store.ts`), started once by `startBoot`.
 */
import { type ReactNode, useEffect, useState } from "react";
import { type BootPlan, planBoot, readBootSlot, startBoot } from "./boot";
import { useViewer } from "./data/store";
import { AgentScreen } from "./screens/AgentScreen";
import { GraphScreen } from "./screens/GraphScreen";
import { RunScreen } from "./screens/RunScreen";
import { Announcer } from "./shell/announce";
import { WindowDropTarget } from "./shell/DropZone";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { Gallery } from "./shell/Gallery";
import { formatRoute, homeRoute, navigate, parseHash, type Route, useRoute } from "./shell/router";
import { ShortcutsDialog } from "./shell/ShortcutsDialog";
import { Sidebar, SidebarSkeleton } from "./shell/Sidebar";
import { useShortcuts } from "./shell/shortcuts";
import {
  ConnectingSkeleton,
  LoadFailed,
  NotFound,
  ReconnectingBanner,
  Welcome,
} from "./shell/states";
import { TopBar } from "./shell/TopBar";
import { toggleTheme } from "./shell/theme";
import { TooltipProvider } from "./ui/overlays";

function Screen({ route }: { route: Route }): ReactNode {
  const phase = useViewer((state) => state.phase);
  const failure = useViewer((state) => state.failure);
  const hasHome = useViewer((state) => homeRoute(state) !== null);

  if (phase === "failed" && failure) return <LoadFailed failure={failure} />;
  if (phase === "loading") return <ConnectingSkeleton />;
  switch (route.name) {
    case "gallery":
      return <Gallery />;
    case "agent":
      return <AgentScreen id={route.id} />;
    case "graph":
      return <GraphScreen id={route.id} />;
    case "run":
      return <RunScreen traceId={route.traceId} spanId={route.spanId} />;
    case "home":
      // With something to show, the redirect below is about to replace this route.
      return hasHome ? null : (
        <div className="dot-grid h-full overflow-auto">
          <Welcome />
        </div>
      );
    case "unknown":
      return (
        <NotFound
          what="Page"
          detail={`There is no screen at "${route.path}". Links look like #/agents/a1 or #/runs/<trace id>.`}
        />
      );
  }
}

export function App({ plan }: { plan?: BootPlan }) {
  const route = useRoute();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  // Boot once. The plan is read at mount; a new boot needs a page load.
  // biome-ignore lint/correctness/useExhaustiveDependencies: boot runs once per mount by design.
  useEffect(() => startBoot(plan ?? planBoot(readBootSlot(), window.location.search)), []);

  // `#/` is a redirect: go to the best screen as soon as there is one.
  // The selector returns a string so that it is stable between store updates.
  const homeHash = useViewer((state) => {
    const home = state.phase === "ready" ? homeRoute(state) : null;
    return home ? formatRoute(home) : null;
  });
  useEffect(() => {
    if (route.name === "home" && homeHash) navigate(parseHash(homeHash), { replace: true });
  }, [route.name, homeHash]);

  useShortcuts({ toggleTheme, showShortcuts: () => setShortcutsOpen(true) });

  const phase = useViewer((state) => state.phase);
  const live = useViewer((state) => state.source.kind === "live");
  const hasData = useViewer(
    (state) =>
      state.data.agentOrder.length + state.data.graphOrder.length + state.data.traceOrder.length >
      0,
  );
  const sidebar =
    phase === "loading" && live ? (
      <SidebarSkeleton />
    ) : hasData || live ? (
      <Sidebar route={route} />
    ) : null;

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col bg-bg text-fg">
        {/* A button, not a #main link: the hash belongs to the router. */}
        <button
          type="button"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:border focus:border-line focus:bg-surface focus:px-3 focus:py-1.5"
          onClick={() => document.getElementById("main")?.focus()}
        >
          Skip to content
        </button>
        <TopBar route={route} onShowShortcuts={() => setShortcutsOpen(true)} />
        <ReconnectingBanner />
        <div className="flex min-h-0 flex-1">
          {phase !== "failed" && sidebar}
          <div id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
            <ErrorBoundary resetKey={formatRoute(route)}>
              <Screen route={route} />
            </ErrorBoundary>
          </div>
        </div>
        <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
        <WindowDropTarget />
        <Announcer />
      </div>
    </TooltipProvider>
  );
}
