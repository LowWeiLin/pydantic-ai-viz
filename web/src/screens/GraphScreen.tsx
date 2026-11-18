/**
 * The graph blueprint screen. Stage seam: `GraphOutline` is the text view; the canvas
 * (`canvas/GraphCanvas`) takes over the `main` slot. "Copy Mermaid" lives here and stays.
 */
import { useViewer } from "../data/store";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { Icon } from "../ui/Icon";
import type { GraphBlueprint } from "../wire";
import { Card, Facts, orDash, ScreenHeader, Section } from "./parts";

const KNOWN_KINDS = new Set(["start", "end", "step", "decision", "join", "broadcast", "map"]);

/** Unknown node kinds are drawn as steps (forward compatibility). */
export const nodeKind = (kind: string): string => (KNOWN_KINDS.has(kind) ? kind : "step");

export function GraphOutline({ graph }: { graph: GraphBlueprint }) {
  const label = (id: string): string => graph.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-7 px-6 py-6">
      <ScreenHeader
        icon="graph"
        tone="graph"
        kind="Graph blueprint"
        title={graph.label}
        chips={
          <>
            <Chip tone="graph">{graph.nodes.length} nodes</Chip>
            <Chip tone="graph">{graph.edges.length} edges</Chip>
            {graph.warnings.length > 0 && (
              <Chip tone="retry" icon="warning">
                {graph.warnings.length === 1 ? "1 warning" : `${graph.warnings.length} warnings`}
              </Chip>
            )}
          </>
        }
        aside={
          graph.mermaid !== null ? (
            <CopyButton text={graph.mermaid} label="Copy Mermaid" className="mr-2" />
          ) : undefined
        }
      />

      {graph.warnings.length > 0 && (
        <Section title="Warnings" count={graph.warnings.length}>
          <ul className="tone-retry m-0 flex list-none flex-col gap-1 p-0">
            {graph.warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-2 text-ui">
                <Icon name="warning" size={14} className="tone-text mt-0.5" />
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Types">
        <Facts
          items={[
            ["State", orDash(graph.state_type)],
            ["Input", orDash(graph.input_type)],
            ["Output", orDash(graph.output_type)],
          ]}
        />
      </Section>

      <Section title="Nodes" count={graph.nodes.length}>
        <Card>
          <ul className="m-0 list-none p-0">
            {graph.nodes.map((node) => (
              <li
                key={node.id}
                className="flex flex-wrap items-center gap-2 border-line border-b px-3 py-2 last:border-b-0"
              >
                <Chip tone="graph">{nodeKind(node.kind)}</Chip>
                <span className="font-medium text-ui">{node.label ?? node.id}</span>
                {node.label !== null && <code className="text-faint text-meta">{node.id}</code>}
                {node.note && <span className="text-muted text-ui">{node.note}</span>}
              </li>
            ))}
          </ul>
        </Card>
      </Section>

      <Section title="Edges" count={graph.edges.length}>
        <Card>
          <ul className="m-0 list-none p-0">
            {graph.edges.map((edge, i) => (
              <li
                key={`${edge.source}-${edge.target}-${i}`}
                className="flex flex-wrap items-center gap-2 border-line border-b px-3 py-1.5 text-ui last:border-b-0"
              >
                <span>{label(edge.source)}</span>
                <Icon name="output" size={14} className="text-faint" label="to" />
                <span>{label(edge.target)}</span>
                {edge.label && <Chip>{edge.label}</Chip>}
              </li>
            ))}
          </ul>
        </Card>
      </Section>

      {graph.mermaid !== null && (
        <Section title="Mermaid">
          <pre className="m-0 overflow-x-auto rounded-card border border-line bg-surface p-3 text-data text-fg">
            {graph.mermaid}
          </pre>
        </Section>
      )}
    </div>
  );
}

export function GraphScreen({ id }: { id: string }) {
  const graph = useViewer((state) => state.data.graphs[id]);
  if (!graph) {
    return (
      <NotFound
        what="Graph blueprint"
        detail={`This session has no graph blueprint with the id "${id}". The link may come from another session.`}
      />
    );
  }
  return (
    <Workspace
      main={
        <>
          <WaitingForRun />
          <GraphOutline graph={graph} />
        </>
      }
    />
  );
}
