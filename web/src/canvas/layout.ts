/**
 * Canvas layout: pure functions from a canvas model to boxes and edges. The agent canvas is a
 * staircase of agent rows computed here; dagre ranks a graph's nodes left to right. Every node
 * has a fixed size that follows from its content, so the result is deterministic and needs no
 * DOM measurement.
 *
 * Layout depends on structure only (which nodes and edges exist), never on run activity:
 * `agentLayoutKey` and `graphLayoutKey` say when it has to be computed again. What Pydantic AI
 * adds by default is part of the structure only with `showDefaults`.
 */
import dagre from "@dagrejs/dagre";
import { LIBRARY_TOOL_NAMES, libraryToolsets, shownCapabilities } from "../data/model";
import type { CanvasAgent, CanvasModel } from "../data/selectors";
import type { AgentBlueprint, Capability, GraphBlueprint } from "../wire";
import {
  agentNodeId,
  capabilityNodeId,
  graphNodeId,
  outputNodeId,
  skillNodeId,
  toolNodeId,
  toolsetNodeId,
} from "./targets";

export const SIZE = {
  agentWidth: 240,
  agentHeight: 104,
  /** Extra height for a two-line description. */
  agentDescription: 40,
  groupWidth: 224,
  groupHeader: 30,
  groupPadding: 8,
  rowWidth: 208,
  rowHeight: 34,
  rowGap: 6,
  outputWidth: 224,
  outputHeight: 58,
  /** A skill's row, under its capability's and set in from it. */
  skillHeight: 28,
  skillInset: 16,
} as const;

/** A capability lists this many of its skills on the canvas at most; its panel lists them all. */
export const MAX_SKILL_ROWS = 8;
/** How far under every block in its way a delegation arc runs. */
export const ARC_CLEARANCE = 16;

const RANK_GAP = 64;
const NODE_GAP = 24;
const GRAPH_RANK_GAP = 40;
/** Between the blocks of one agent's column, and between one agent's row and the next. */
const COLUMN_GAP = 12;
const CLUSTER_GAP = 44;
/** Where a delegation edge enters a sub-agent's card, from the card's left edge. */
export const DELEGATION_INLET = 212;
/**
 * How far right of its parent a sub-agent's card starts: far enough that the lane down to its
 * inlet runs 20 px clear of the parent's column.
 */
const INDENT = SIZE.agentWidth + RANK_GAP + SIZE.groupWidth + 20 - DELEGATION_INLET;
/**
 * Lanes to an agent's second and later sub-agents run through the gap between the cards and
 * the columns of the rows above: this far right of the card, and this far apart.
 */
const LANE_OFFSET = 14;
const LANE_STEP = 10;

export type AgentNodeKind =
  | "agent"
  | "toolset"
  | "tool"
  | "placeholder"
  | "output"
  | "capabilities"
  | "capability"
  | "skill";

export type LayoutNode = {
  id: string;
  kind: AgentNodeKind;
  agentId: string;
  /**
   * Toolset id for `toolset` and `placeholder`, tool name for `tool`, the index into
   * `blueprint.capabilities` for `capability`, "<index>:<name>" for `skill`, else "".
   */
  ref: string;
  /** Top-left corner; relative to the parent when `parentId` is set. */
  x: number;
  y: number;
  width: number;
  height: number;
  parentId: string | null;
};

export type LayoutEdge = {
  id: string;
  /**
   * `flow`: agent to tool; `output`: agent to output; `delegation`: tool to sub-agent;
   * `gives`: capability to the toolset it gives the agent.
   */
  kind: "flow" | "output" | "delegation" | "gives";
  source: string;
  target: string;
  /** The agent the edge leaves from. */
  agentId: string;
  /** Tool name for `flow` and `delegation` edges, toolset id for `gives`. */
  ref: string;
  /** For delegation edges: the agent delegated to. */
  toAgentId: string | null;
  /**
   * Delegation only: the target is not a row under this agent (a cycle, or an agent already
   * placed under another one), so the edge is drawn as an arc instead of a lane.
   */
  back: boolean;
  /**
   * Delegation that is not `back`: the x of the vertical lane the edge runs down. When it is
   * not the x where the edge enters the card, the edge jogs left just above the card.
   */
  lane: number | null;
  /**
   * A `back` delegation: the lowest bottom of the blocks between its ends, which the arc runs
   * under. Null for every other edge.
   */
  floor: number | null;
};

export type AgentLayout = { nodes: LayoutNode[]; edges: LayoutEdge[] };

/** The id of the group that collects tools seen in a run but not listed by the blueprint. */
export const SEEN_GROUP = "~seen";

export const placeholderNodeId = (agentId: string, toolsetId: string): string =>
  `placeholder:${agentId}:${toolsetId}`;

type Group = { toolsetId: string; tools: string[]; placeholder: boolean };

/**
 * The rows of each toolset box. Tools a run used but the blueprint does not list are shown in
 * the opaque toolset when there is exactly one (that is where they came from), otherwise in a
 * group of their own. Empty toolsets that are not opaque are left out.
 *
 * Without `showDefaults` the toolsets Pydantic AI gives on its own are left out, and so is a
 * tool it adds that a run used (`load_capability`).
 */
export function toolGroups(agent: CanvasAgent, showDefaults = false): Group[] {
  const hidden = showDefaults ? new Set<string>() : libraryToolsets(agent.blueprint);
  const visible = agent.toolsets.filter((group) => !hidden.has(group.toolset.id));
  const opaque = visible.filter((group) => group.toolset.opaque);
  const home = opaque.length === 1 ? opaque[0]?.toolset.id : undefined;
  const unlisted = agent.unlisted
    .map((tool) => tool.name)
    .filter((name) => showDefaults || !LIBRARY_TOOL_NAMES.has(name));
  const groups: Group[] = [];
  for (const { toolset, tools } of visible) {
    const names = tools.map((entry) => entry.tool.name);
    if (toolset.id === home) names.push(...unlisted);
    if (names.length > 0) groups.push({ toolsetId: toolset.id, tools: names, placeholder: false });
    else if (toolset.opaque) groups.push({ toolsetId: toolset.id, tools: [], placeholder: true });
  }
  if (home === undefined && unlisted.length > 0) {
    groups.push({ toolsetId: SEEN_GROUP, tools: unlisted, placeholder: false });
  }
  return groups;
}

const agentHeight = (agent: CanvasAgent): number =>
  SIZE.agentHeight + (agent.blueprint.description ? SIZE.agentDescription : 0);

/** A box with a header over rows of these heights. */
const boxHeight = (heights: readonly number[]): number =>
  SIZE.groupHeader +
  heights.reduce((sum, height) => sum + height, 0) +
  (heights.length - 1) * SIZE.rowGap +
  SIZE.groupPadding;

const groupHeight = (rows: number): number =>
  boxHeight(Array.from({ length: rows }, () => SIZE.rowHeight));

/** A row of the box of capabilities under an agent's card. */
export type CapabilityRow =
  | { kind: "capability"; index: number; capability: Capability }
  /** One of the skills of the capability at `index`, right under its row. */
  | { kind: "skill"; index: number; name: string };

/** The rows of the box of capabilities: each capability drawn, the skills it offers under it. */
export function capabilityRows(agent: AgentBlueprint, showDefaults = false): CapabilityRow[] {
  return shownCapabilities(agent, showDefaults).flatMap(({ index, capability }) => [
    { kind: "capability" as const, index, capability },
    ...capability.skills
      .slice(0, MAX_SKILL_ROWS)
      .map((skill) => ({ kind: "skill" as const, index, name: skill.name })),
  ]);
}

const rowHeightOf = (row: CapabilityRow): number =>
  row.kind === "skill" ? SIZE.skillHeight : SIZE.rowHeight;

/** Changes exactly when `layoutAgentCanvas` would return something different. */
export function agentLayoutKey(model: CanvasModel, showDefaults = false): string {
  return JSON.stringify([
    showDefaults,
    model.agents.map((agent) => [
      agent.blueprint.id,
      agent.blueprint.description ? 1 : 0,
      toolGroups(agent, showDefaults).map((group) => [group.toolsetId, group.tools]),
      capabilityRows(agent.blueprint, showDefaults).map((row) =>
        row.kind === "skill"
          ? [row.index, row.name]
          : [row.index, row.capability.category, row.capability.toolsets],
      ),
    ]),
    model.delegations.map((d) => [d.fromAgentId, d.toolName, d.toAgentId]),
  ]);
}

/**
 * Which edges close a cycle, by index: those that lead back to a node still being explored in
 * a depth-first walk from `roots` (then from every other node, in order). Leaving them out, or
 * turning them round, makes the rest acyclic, and unlike a generic heuristic it keeps the
 * reading order the roots imply: a loop back to an earlier step is the edge that is "back".
 */
export function backEdges(
  nodes: readonly string[],
  edges: readonly (readonly [source: string, target: string])[],
  roots: readonly string[],
): Set<number> {
  const out = new Map<string, number[]>();
  for (const [index, [source]] of edges.entries()) {
    const list = out.get(source);
    if (list) list.push(index);
    else out.set(source, [index]);
  }
  const back = new Set<number>();
  /** Absent: not seen; false: on the current path; true: finished. */
  const done = new Map<string, boolean>();
  for (const root of [...roots, ...nodes]) {
    if (done.has(root)) continue;
    done.set(root, false);
    const stack = [{ id: root, next: 0 }];
    for (let top = stack.at(-1); top; top = stack.at(-1)) {
      const index = out.get(top.id)?.[top.next++];
      if (index === undefined) {
        done.set(top.id, true);
        stack.pop();
        continue;
      }
      const target = edges[index]?.[1];
      if (target === undefined) continue;
      const state = done.get(target);
      if (state === false) back.add(index);
      else if (state === undefined) {
        done.set(target, false);
        stack.push({ id: target, next: 0 });
      }
    }
  }
  return back;
}

function newGraph() {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({
    rankdir: "LR",
    nodesep: NODE_GAP,
    // A chain of steps is wide and the canvas is not: just enough room for an arrow.
    ranksep: GRAPH_RANK_GAP,
    marginx: 0,
    marginy: 0,
  });
  g.setDefaultEdgeLabel(() => ({}));
  return g;
}

/**
 * The height of an agent's row: its card with the box of its capabilities underneath, or its
 * column (toolsets, then its output), whichever is taller.
 */
function clusterHeight(agent: CanvasAgent, showDefaults: boolean): number {
  const groups = toolGroups(agent, showDefaults);
  const column =
    groups.reduce(
      (sum, group) => sum + groupHeight(group.placeholder ? 1 : group.tools.length) + COLUMN_GAP,
      0,
    ) + SIZE.outputHeight;
  const rows = capabilityRows(agent.blueprint, showDefaults);
  const card =
    agentHeight(agent) + (rows.length > 0 ? COLUMN_GAP + boxHeight(rows.map(rowHeightOf)) : 0);
  return Math.max(card, column);
}

type Box = { x: number; y: number; width: number; height: number };

/**
 * How low an arc between two boxes has to run: under the lower one, and under every block in
 * its way that reaches down past where it would run, so that it never passes behind a card
 * between its ends.
 */
function arcFloor(a: Box, b: Box, blocks: readonly Box[]): number {
  const left = Math.min(a.x, b.x);
  const right = Math.max(a.x + a.width, b.x + b.width);
  let floor = Math.max(a.y + a.height, b.y + b.height);
  for (let moved = true; moved; ) {
    moved = false;
    for (const block of blocks) {
      const bottom = block.y + block.height;
      const across = block.x < right && block.x + block.width > left;
      if (across && block.y < floor + ARC_CLEARANCE && bottom > floor) {
        floor = bottom;
        moved = true;
      }
    }
  }
  return floor;
}

/**
 * Lays the delegation closure out as a staircase, with no ranking pass:
 *
 *   [agent] -> [toolsets, output]
 *   [capabilities]    |  (from the delegating tool, down a lane beside the column)
 *                     v
 *              [sub-agent] -> [toolsets, output]
 *
 * Each agent is a row: its card, the capabilities it lists in a box under the card, then one
 * column with its toolsets and its output. An agent it
 * delegates to goes in a row underneath, one step to the right. A side-by-side chain would be
 * twice as wide, and the canvas is wider than it is tall only until the timeline dock opens.
 *
 * The rows are a depth-first walk from the subject, so each agent appears once, under the
 * first agent that reaches it, and all sub-agents of one agent share an indent. A delegation to an agent that is already placed somewhere else
 * (a cycle, or a second path) is marked `back` and drawn as an arc, under every block between
 * its ends.
 */
export function layoutAgentCanvas(model: CanvasModel, showDefaults = false): AgentLayout {
  const nodes: LayoutNode[] = [];
  const edges: LayoutEdge[] = [];
  const agents = new Map(model.agents.map((agent) => [agent.blueprint.id, agent]));
  const shown = model.delegations.filter(
    (d) => agents.has(d.fromAgentId) && agents.has(d.toAgentId),
  );

  /** The agent each agent sits under; the subject and unreachable agents have none. */
  const parent = new Map<string, string | null>();
  /** Lane x of the delegation edges into each placed sub-agent. */
  const lanes = new Map<string, number>();
  /**
   * The node an arc leaves an agent from or comes into it at, from below: its card, or the box
   * of capabilities under the card, which an arc to the card would cross.
   */
  const bottom = new Map<string, string>();
  let cursor = 0;

  const place = (agent: CanvasAgent, x: number, under: string | null): void => {
    const agentId = agent.blueprint.id;
    parent.set(agentId, under);
    const top = cursor;
    const card = agentNodeId(agentId);
    bottom.set(agentId, card);
    nodes.push({
      id: card,
      kind: "agent",
      agentId,
      ref: "",
      x,
      y: top,
      width: SIZE.agentWidth,
      height: agentHeight(agent),
      parentId: null,
    });

    const columnX = x + SIZE.agentWidth + RANK_GAP;
    let y = top;
    const groups = toolGroups(agent, showDefaults);
    for (const group of groups) {
      const box = toolsetNodeId(agentId, group.toolsetId);
      const rows = group.placeholder ? 1 : group.tools.length;
      nodes.push({
        id: box,
        kind: "toolset",
        agentId,
        ref: group.toolsetId,
        x: columnX,
        y,
        width: SIZE.groupWidth,
        height: groupHeight(rows),
        parentId: null,
      });
      y += groupHeight(rows) + COLUMN_GAP;
      const row = (index: number) => ({
        x: (SIZE.groupWidth - SIZE.rowWidth) / 2,
        y: SIZE.groupHeader + index * (SIZE.rowHeight + SIZE.rowGap),
        width: SIZE.rowWidth,
        height: SIZE.rowHeight,
        parentId: box,
      });
      if (group.placeholder) {
        nodes.push({
          id: placeholderNodeId(agentId, group.toolsetId),
          kind: "placeholder",
          agentId,
          ref: group.toolsetId,
          ...row(0),
        });
      }
      for (const [index, name] of group.tools.entries()) {
        const id = toolNodeId(agentId, name);
        nodes.push({ id, kind: "tool", agentId, ref: name, ...row(index) });
        edges.push({
          id: `flow:${agentId}:${name}`,
          kind: "flow",
          source: card,
          target: id,
          agentId,
          ref: name,
          toAgentId: null,
          back: false,
          lane: null,
          floor: null,
        });
      }
    }

    const output = outputNodeId(agentId);
    nodes.push({
      id: output,
      kind: "output",
      agentId,
      ref: "",
      x: columnX,
      y,
      width: SIZE.outputWidth,
      height: SIZE.outputHeight,
      parentId: null,
    });
    edges.push({
      id: `output:${agentId}`,
      kind: "output",
      source: card,
      target: output,
      agentId,
      ref: "",
      toAgentId: null,
      back: false,
      lane: null,
      floor: null,
    });

    // Capabilities go in a box under the card, which the column never reaches into, each with
    // the skills it offers in rows under it. A dashed edge leads from a capability to each
    // toolset it gives the agent that is drawn.
    const rows = capabilityRows(agent.blueprint, showDefaults);
    if (rows.length > 0) {
      const box = `capabilities:${agentId}`;
      bottom.set(agentId, box);
      const drawn = new Set(groups.map((group) => group.toolsetId));
      const inset = (SIZE.groupWidth - SIZE.rowWidth) / 2;
      nodes.push({
        id: box,
        kind: "capabilities",
        agentId,
        ref: "",
        x,
        y: top + agentHeight(agent) + COLUMN_GAP,
        width: SIZE.agentWidth,
        height: boxHeight(rows.map(rowHeightOf)),
        parentId: null,
      });
      let rowY = SIZE.groupHeader;
      for (const row of rows) {
        const height = rowHeightOf(row);
        const at = rowY;
        rowY += height + SIZE.rowGap;
        if (row.kind === "skill") {
          const indent = inset + SIZE.skillInset;
          nodes.push({
            id: skillNodeId(agentId, row.index, row.name),
            kind: "skill",
            agentId,
            ref: `${row.index}:${row.name}`,
            x: indent,
            y: at,
            width: SIZE.agentWidth - inset - indent,
            height,
            parentId: box,
          });
          continue;
        }
        const { index, capability } = row;
        const id = capabilityNodeId(agentId, index);
        nodes.push({
          id,
          kind: "capability",
          agentId,
          ref: String(index),
          x: inset,
          y: at,
          width: SIZE.agentWidth - 2 * inset,
          height,
          parentId: box,
        });
        for (const toolsetId of capability.toolsets) {
          if (!drawn.has(toolsetId)) continue;
          edges.push({
            id: `gives:${agentId}:${index}:${toolsetId}`,
            kind: "gives",
            source: id,
            target: toolsetNodeId(agentId, toolsetId),
            agentId,
            ref: toolsetId,
            toAgentId: null,
            back: false,
            lane: null,
            floor: null,
          });
        }
      }
    }
    cursor = top + clusterHeight(agent, showDefaults) + CLUSTER_GAP;

    // Sub-agents share one indent. The lane to the first runs straight into its card; the
    // lanes to the others pass the rows above between their cards and columns.
    const childX = x + INDENT;
    let index = 0;
    for (const delegation of shown) {
      if (delegation.fromAgentId !== agentId || parent.has(delegation.toAgentId)) continue;
      const child = agents.get(delegation.toAgentId);
      if (!child) continue;
      lanes.set(
        child.blueprint.id,
        index === 0
          ? childX + DELEGATION_INLET
          : childX + SIZE.agentWidth + LANE_OFFSET + (index - 1) * LANE_STEP,
      );
      index += 1;
      place(child, childX, agentId);
    }
  };

  const subject = agents.get(model.subjectId);
  if (subject) place(subject, 0, null);
  for (const agent of model.agents) {
    if (!parent.has(agent.blueprint.id)) place(agent, 0, null);
  }

  const hasTool = new Set(nodes.filter((node) => node.kind === "tool").map((node) => node.id));
  // Rows are placed in their box; an arc is measured on the page.
  const boxes = new Map<string, Box>();
  for (const node of nodes) {
    const box = node.parentId ? boxes.get(node.parentId) : undefined;
    boxes.set(node.id, box ? { ...node, x: box.x + node.x, y: box.y + node.y } : node);
  }
  const blocks = nodes.filter((node) => node.parentId === null);
  for (const delegation of shown) {
    const tool = toolNodeId(delegation.fromAgentId, delegation.toolName);
    const back = parent.get(delegation.toAgentId) !== delegation.fromAgentId;
    // An arc runs under the card; with capabilities listed there, it runs under their box.
    const end = (agentId: string): string =>
      (back ? bottom.get(agentId) : undefined) ?? agentNodeId(agentId);
    // A delegation through a tool the canvas does not show starts at the agent card.
    const source = hasTool.has(tool) ? tool : end(delegation.fromAgentId);
    const target = end(delegation.toAgentId);
    const from = boxes.get(source);
    const to = boxes.get(target);
    edges.push({
      id: `delegation:${delegation.fromAgentId}:${delegation.toolName}:${delegation.toAgentId}`,
      kind: "delegation",
      source,
      target,
      agentId: delegation.fromAgentId,
      ref: delegation.toolName,
      toAgentId: delegation.toAgentId,
      back,
      lane: back ? null : (lanes.get(delegation.toAgentId) ?? null),
      floor: back && from && to ? arcFloor(from, to, blocks) : null,
    });
  }
  return { nodes, edges };
}

// ---------- graph ----------

const KNOWN_KINDS = new Set(["start", "end", "step", "decision", "join", "broadcast", "map"]);

/** Unknown node kinds are drawn as steps (forward compatibility). */
export const nodeKind = (kind: string): string => (KNOWN_KINDS.has(kind) ? kind : "step");

/** What a graph node is called on screen. Start and end nodes usually have no label. */
export function graphNodeLabel(node: { id: string; kind: string; label: string | null }): string {
  if (node.label) return node.label;
  if (node.kind === "start") return "Start";
  if (node.kind === "end") return "End";
  return node.id;
}

export type GraphLayoutNode = {
  id: string;
  /** The blueprint node id (`GraphNode.id`). */
  nodeId: string;
  /** Drawn kind: an unknown kind has already become "step". */
  kind: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type GraphLayoutEdge = {
  id: string;
  /** Index into `graph.edges`. */
  index: number;
  source: string;
  target: string;
  label: string | null;
  /** The target is not to the right of the source (a loop): drawn as an arc underneath. */
  back: boolean;
};

export type GraphLayout = { nodes: GraphLayoutNode[]; edges: GraphLayoutEdge[] };

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

/** Box size for a graph node: enough for its text at the canvas font sizes. */
export function graphNodeSize(node: {
  id: string;
  kind: string;
  label: string | null;
  note: string | null;
}): { width: number; height: number } {
  const kind = nodeKind(node.kind);
  const label = graphNodeLabel(node);
  if (kind === "start" || kind === "end") {
    return { width: clamp(label.length * 8 + 44, 76, 180), height: 34 };
  }
  if (kind === "decision") {
    // A diamond only holds text in its middle half, so it is wider than a card for the same words.
    const text = Math.max((node.note ?? "").length, node.label ? node.label.length : 0);
    return { width: clamp(text * 9 + 84, 132, 260), height: 88 };
  }
  const longest = Math.max(label.length, label === node.id ? 0 : node.id.length * 0.85);
  return { width: clamp(Math.round(longest * 8) + 44, 116, 240), height: 54 };
}

export function graphLayoutKey(graph: GraphBlueprint, extraNodeIds: readonly string[]): string {
  return JSON.stringify([
    graph.nodes.map((node) => [node.id, node.kind, node.label, node.note]),
    graph.edges.map((edge) => [edge.source, edge.target, edge.label]),
    extraNodeIds,
  ]);
}

/**
 * Lays a graph blueprint out left to right. `extraNodeIds` are nodes a run visited that the
 * blueprint does not list; they are drawn as unconnected steps.
 */
export function layoutGraph(graph: GraphBlueprint, extraNodeIds: readonly string[]): GraphLayout {
  const g = newGraph();
  const nodes: GraphLayoutNode[] = [];
  const listed = new Set<string>();
  const add = (node: { id: string; kind: string; label: string | null; note: string | null }) => {
    if (listed.has(node.id)) return;
    listed.add(node.id);
    const size = graphNodeSize(node);
    const id = graphNodeId(node.id);
    g.setNode(id, size);
    nodes.push({ id, nodeId: node.id, kind: nodeKind(node.kind), x: 0, y: 0, ...size });
  };
  for (const node of graph.nodes) add(node);
  for (const id of extraNodeIds) add({ id, kind: "step", label: null, note: null });

  const loops = backEdges(
    graph.nodes.map((node) => node.id),
    graph.edges.map((edge) => [edge.source, edge.target] as const),
    graph.nodes.filter((node) => node.kind === "start").map((node) => node.id),
  );
  const edges: GraphLayoutEdge[] = [];
  for (const [index, edge] of graph.edges.entries()) {
    if (!listed.has(edge.source) || !listed.has(edge.target)) continue;
    const back = loops.has(index);
    const id = `edge:${index}`;
    const source = graphNodeId(edge.source);
    const target = graphNodeId(edge.target);
    // Reserving the label's width keeps ranks far enough apart for it to fit.
    const width = edge.label ? edge.label.length * 7 + 20 : 0;
    // A loop takes no part in the ranking: the node it returns to is already upstream, and
    // its label sits on the arc, so it needs no room between ranks.
    if (!back) {
      g.setEdge(source, target, { width, height: edge.label ? 20 : 0, labelpos: "c" }, id);
    }
    edges.push({ id, index, source, target, label: edge.label, back });
  }

  dagre.layout(g);

  for (const node of nodes) {
    const placed = g.node(node.id);
    node.x = Math.round((placed?.x ?? 0) - node.width / 2);
    node.y = Math.round((placed?.y ?? 0) - node.height / 2);
  }
  return { nodes, edges };
}
