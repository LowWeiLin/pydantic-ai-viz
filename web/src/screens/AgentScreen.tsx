/**
 * The agent blueprint screen: the canvas in the main slot, the inspector beside it. Clicking a
 * node inspects that part; with nothing picked the inspector shows the agent itself.
 */
import { useMemo, useState } from "react";
import { AgentCanvas } from "../canvas/AgentCanvas";
import type { CanvasTarget } from "../canvas/targets";
import { canvasModel } from "../data/selectors";
import { useViewer } from "../data/store";
import { BlueprintInspector } from "../inspector/Inspector";
import { rememberTab } from "../inspector/parts";
import { plural } from "../shell/format";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { Chip } from "../ui/Badge";
import { Tooltip } from "../ui/overlays";
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

  const others = model.agents.length - 1;
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
              <>
                {agent.model?.offline && (
                  <Tooltip content="A test or function model: runs offline and calls no provider.">
                    <span className="inline-flex">
                      <Chip>offline model</Chip>
                    </span>
                  </Tooltip>
                )}
                {agent.source === "observed" && (
                  <Tooltip content="Built from what its runs showed. Pass the agent to serve() or record() to see instructions, schemas and settings in full.">
                    <span className="inline-flex">
                      <Chip tone="accent" icon="info">
                        Observed from runs
                      </Chip>
                    </span>
                  </Tooltip>
                )}
                <Chip tone="tool" icon="tool">
                  {plural(agent.tools.length, "tool")}
                </Chip>
                {others > 0 && (
                  <Chip tone="agent" icon="agent">
                    {plural(others, "sub-agent")}
                  </Chip>
                )}
                {agent.warnings.length > 0 && (
                  <Chip tone="retry" icon="warning">
                    {plural(agent.warnings.length, "warning")}
                  </Chip>
                )}
              </>
            }
          />
          <div className="min-h-0 flex-1">
            <AgentCanvas
              model={model}
              highlight={highlight}
              onPick={(next) => {
                if (next.kind === "output") rememberTab("agent", "output");
                setPicked(next);
              }}
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
