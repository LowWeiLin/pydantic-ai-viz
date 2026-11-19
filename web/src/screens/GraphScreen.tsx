/**
 * The graph blueprint screen: the canvas in the main slot, the inspector beside it, and
 * "Copy Mermaid" in the bar. Clicking a node inspects it; otherwise the graph itself is shown.
 */
import { useMemo, useState } from "react";
import { GraphCanvas } from "../canvas/GraphCanvas";
import type { CanvasTarget } from "../canvas/targets";
import { graphCanvasModel } from "../data/selectors";
import { useViewer } from "../data/store";
import { GraphInspector } from "../inspector/BlueprintInspector";
import { BlueprintInspector } from "../inspector/Inspector";
import type { Reveal } from "../inspector/parts";
import { plural } from "../shell/format";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { ScreenBar, WarningsChip } from "./parts";

function GraphBlueprintView({ id }: { id: string }) {
  const data = useViewer((state) => state.data);
  const [picked, setPicked] = useState<CanvasTarget | null>(null);
  // Set by the warning count: the graph's own panel, at its warnings.
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const pick = (next: CanvasTarget | null): void => {
    setReveal(null);
    setPicked(next);
  };
  const model = useMemo(() => graphCanvasModel(data, id, null), [data, id]);
  const highlight = useMemo(() => (picked ? [picked] : []), [picked]);
  if (!model) return null;
  const { graph } = model;

  return (
    <Workspace
      inspectorLabel="Blueprint details"
      main={
        <div className="flex h-full flex-col">
          <ScreenBar
            icon="graph"
            tone="graph"
            kind="Graph blueprint"
            title={graph.label}
            chips={
              <>
                <Chip tone="graph">{plural(graph.nodes.length, "node")}</Chip>
                <Chip tone="graph">{plural(graph.edges.length, "edge")}</Chip>
                <WarningsChip
                  count={graph.warnings.length}
                  onShow={() => {
                    setPicked(null);
                    setReveal({ section: "warnings" });
                  }}
                />
              </>
            }
            aside={
              graph.mermaid !== null ? (
                <CopyButton text={graph.mermaid} label="Copy Mermaid" />
              ) : undefined
            }
          />
          {/* Under the bar, so the inspector toggle keeps its place at the bar's end. */}
          <WaitingForRun />
          <div className="min-h-0 flex-1">
            <GraphCanvas
              model={model}
              highlight={highlight}
              onPick={pick}
              onClear={() => pick(null)}
            />
          </div>
        </div>
      }
      inspector={
        picked ? (
          <BlueprintInspector target={picked} graph={graph} />
        ) : (
          <GraphInspector graph={graph} reveal={reveal} />
        )
      }
      openRequest={reveal}
    />
  );
}

export function GraphScreen({ id }: { id: string }) {
  const known = useViewer((state) => Object.hasOwn(state.data.graphs, id));
  if (!known) {
    return (
      <NotFound
        what="Graph blueprint"
        detail={`This session has no graph blueprint with the id "${id}". The link may come from another session.`}
      />
    );
  }
  return <GraphBlueprintView key={id} id={id} />;
}
