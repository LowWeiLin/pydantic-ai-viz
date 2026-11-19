/**
 * Hash routing, so the viewer works from `file://` and behind any path prefix.
 *
 *   #/                       home (redirects, see `homeRoute`)
 *   #/gallery                recordings and the drop zone
 *   #/agents/<id>?part=<p>   agent blueprint, optionally with a picked part (see `partOf`)
 *   #/graphs/<id>?node=<id>  graph blueprint, optionally with a picked node
 *   #/runs/<trace>?span=<id> run view, optionally with a selected span
 */
import { useSyncExternalStore } from "react";
import { runs } from "../data/selectors";
import type { ViewerState } from "../data/store";

export type Route =
  | { name: "home" }
  | { name: "gallery" }
  /** `part` names a picked part of the agent, as `partOf` writes it. */
  | { name: "agent"; id: string; part?: string }
  /** `node` is the id of a picked graph node. */
  | { name: "graph"; id: string; node?: string }
  | { name: "run"; traceId: string; spanId: string | null }
  | { name: "unknown"; path: string };

const decode = (segment: string): string => {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
};

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const queryAt = raw.indexOf("?");
  const path = (queryAt === -1 ? raw : raw.slice(0, queryAt)).replace(/^\/+|\/+$/g, "");
  const query = new URLSearchParams(queryAt === -1 ? "" : raw.slice(queryAt + 1));
  if (path === "") return { name: "home" };
  const [head, id, ...rest] = path.split("/");
  if (rest.length === 0) {
    if (head === "gallery" && id === undefined) return { name: "gallery" };
    if (id) {
      if (head === "agents") {
        const part = query.get("part");
        return part ? { name: "agent", id: decode(id), part } : { name: "agent", id: decode(id) };
      }
      if (head === "graphs") {
        const node = query.get("node");
        return node ? { name: "graph", id: decode(id), node } : { name: "graph", id: decode(id) };
      }
      if (head === "runs") {
        return { name: "run", traceId: decode(id), spanId: query.get("span") || null };
      }
    }
  }
  return { name: "unknown", path: `/${path}` };
}

export function formatRoute(route: Route): string {
  switch (route.name) {
    case "home":
      return "#/";
    case "gallery":
      return "#/gallery";
    case "agent": {
      const base = `#/agents/${encodeURIComponent(route.id)}`;
      return route.part ? `${base}?part=${encodeURIComponent(route.part)}` : base;
    }
    case "graph": {
      const base = `#/graphs/${encodeURIComponent(route.id)}`;
      return route.node ? `${base}?node=${encodeURIComponent(route.node)}` : base;
    }
    case "run": {
      const base = `#/runs/${encodeURIComponent(route.traceId)}`;
      return route.spanId ? `${base}?span=${encodeURIComponent(route.spanId)}` : base;
    }
    case "unknown":
      return `#${route.path}`;
  }
}

/** Goes to a route. `replace` avoids a history entry (redirects, selection changes). */
export function navigate(route: Route, options: { replace?: boolean } = {}): void {
  const hash = formatRoute(route);
  if (window.location.hash === hash) return;
  if (options.replace) {
    // replaceState does not fire hashchange, so announce it for `useRoute`.
    window.history.replaceState(window.history.state, "", hash);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = hash;
  }
}

const subscribe = (onChange: () => void): (() => void) => {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
};

const routeCache = { hash: "", route: { name: "home" } as Route };

function currentRoute(): Route {
  const hash = window.location.hash;
  if (hash !== routeCache.hash) {
    routeCache.hash = hash;
    routeCache.route = parseHash(hash);
  }
  return routeCache.route;
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, currentRoute);
}

/**
 * Where `#/` leads: the gallery if the page has a recording index and nothing is loaded yet;
 * else the latest run; else the first blueprint; else nowhere (the empty state stays up).
 */
export function homeRoute(state: Pick<ViewerState, "data" | "gallery" | "source">): Route | null {
  const { data } = state;
  if (state.gallery && state.source.kind === "none") return { name: "gallery" };
  const latest = runs(data)[0];
  if (latest) return { name: "run", traceId: latest.traceId, spanId: null };
  const agentId = data.agentOrder[0];
  if (agentId !== undefined) return { name: "agent", id: agentId };
  const graphId = data.graphOrder[0];
  if (graphId !== undefined) return { name: "graph", id: graphId };
  return null;
}
