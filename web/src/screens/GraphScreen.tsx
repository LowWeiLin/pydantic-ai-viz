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
import { plural } from "../shell/format";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { ScreenBar } from "./parts";

function GraphBlueprintView({ id }: { id: string }) {
  const data = useViewer((state) => state.data);
  const [picked, setPicked] = useState<CanvasTarget | null>(null);
  const model = useMemo(() => graphCanvasModel(data, id, null), [data, id]);
  const highlight = useMemo(() => (picked ? [picked] : []), [picked]);
  if (!model) return null;
  const { graph } = model;

  return (
    <Workspace
      inspectorLabel="Blueprint details"
      main={
        <div className="flex h-full flex-col">
          <WaitingForRun />
          <ScreenBar
            icon="graph"
            tone="graph"
            kind="Graph blueprint"
            title={graph.label}
            chips={
              <>
                <Chip tone="graph">{plural(graph.nodes.length, "node")}</Chip>
                <Chip tone="graph">{plural(graph.edges.length, "edge")}</Chip>
                {graph.warnings.length > 0 && (
                  <Chip icon="warning">{plural(graph.warnings.length, "warning")}</Chip>
                )}
              </>
            }
            aside={
              graph.mermaid !== null ? (
                <CopyButton text={graph.mermaid} label="Copy Mermaid" />
              ) : undefined
            }
          />
          <div className="min-h-0 flex-1">
            <GraphCanvas
              model={model}
              highlight={highlight}
              onPick={setPicked}
              onClear={() => setPicked(null)}
            />
          </div>
        </div>
      }
      inspector={
        picked ? (
          <BlueprintInspector target={picked} graph={graph} />
        ) : (
          <GraphInspector graph={graph} />
        )
      }
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
