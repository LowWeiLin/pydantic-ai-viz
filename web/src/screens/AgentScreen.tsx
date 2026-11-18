/**
 * The agent blueprint screen: the canvas in the main slot, the inspector beside it, and
 * "Copy Mermaid" in the bar. Clicking a node inspects that part; with nothing picked the
 * inspector shows the agent itself.
 */
import { useMemo, useState } from "react";
import { AgentCanvas } from "../canvas/AgentCanvas";
import type { CanvasTarget } from "../canvas/targets";
import { agentMermaid } from "../data/mermaid";
import { canvasModel } from "../data/selectors";
import { useViewer } from "../data/store";
import { BlueprintInspector } from "../inspector/Inspector";
import { plural } from "../shell/format";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip } from "../ui/Badge";
import { CopyButton } from "../ui/CopyButton";
import { ScreenBar } from "./parts";

function AgentBlueprintView({ id }: { id: string }) {
  const data = useViewer((state) => state.data);
  const [picked, setPicked] = useState<CanvasTarget | null>(null);
  const model = useMemo(() => canvasModel(data, id, null), [data, id]);
  const target = useMemo<CanvasTarget>(
    () => picked ?? { kind: "agent", agentId: id },
    [picked, id],
  );
  const highlight = useMemo(() => [target], [target]);
  const agent = data.agents[id];
  if (!model || !agent) return null;

  return (
    <Workspace
      inspectorLabel="Blueprint details"
      main={
        <div className="flex h-full flex-col">
          <WaitingForRun />
          <ScreenBar
            icon="agent"
            tone="agent"
            kind="Agent blueprint"
            title={agent.label}
            chips={
              agent.warnings.length > 0 ? (
                <Chip icon="warning">{plural(agent.warnings.length, "warning")}</Chip>
              ) : undefined
            }
            aside={<CopyButton text={() => agentMermaid(model)} label="Copy Mermaid" />}
          />
          <div className="min-h-0 flex-1">
            <AgentCanvas
              model={model}
              highlight={highlight}
              onPick={setPicked}
              onClear={() => setPicked(null)}
            />
          </div>
        </div>
      }
      inspector={<BlueprintInspector target={target} />}
    />
  );
}

export function AgentScreen({ id }: { id: string }) {
  const known = useViewer((state) => Object.hasOwn(state.data.agents, id));
  if (!known) {
    return (
      <NotFound
        what="Agent blueprint"
        detail={`This session has no agent blueprint with the id "${id}". The link may come from another session.`}
      />
    );
  }
  // Keyed by id: what was picked on one blueprint means nothing on the next.
  return <AgentBlueprintView key={id} id={id} />;
}
