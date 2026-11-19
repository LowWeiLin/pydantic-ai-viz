/**
 * The agent blueprint screen: the canvas in the main slot, the inspector beside it, and
 * "Copy Mermaid" in the bar. Clicking a node inspects that part; with nothing picked the
 * inspector shows the agent itself. The picked part is the `?part=` of the route, so a link
 * opens the screen on it.
 */
import { useMemo, useState } from "react";
import { AgentCanvas } from "../canvas/AgentCanvas";
import { agentHas, type CanvasTarget, parsePart, partOf } from "../canvas/targets";
import { agentMermaid } from "../data/mermaid";
import { canvasModel } from "../data/selectors";
import { useViewer } from "../data/store";
import { BlueprintInspector } from "../inspector/Inspector";
import type { Reveal } from "../inspector/parts";
import { InspectorPick } from "../inspector/pick";
import { navigate } from "../shell/router";
import { NotFound, WaitingForRun } from "../shell/states";
import { Workspace } from "../shell/Workspace";
import { CopyButton } from "../ui/CopyButton";
import { ScreenBar, WarningsChip } from "./parts";

function AgentBlueprintView({ id, part }: { id: string; part: string | undefined }) {
  const data = useViewer((state) => state.data);
  // Counts the picks, so that picking the same part again is a new target: the output and the
  // model are sections of the agent's panel, and a second pick scrolls back to them.
  const [picks, setPicks] = useState(0);
  // Set by the warning count: the agent's own panel, at its warnings.
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const pick = (next: CanvasTarget | null): void => {
    setReveal(null);
    setPicks((n) => n + 1);
    const named = next ? partOf(next) : undefined;
    navigate(named ? { name: "agent", id, part: named } : { name: "agent", id }, { replace: true });
  };
  const model = useMemo(() => canvasModel(data, id, null), [data, id]);
  const agent = data.agents[id];
  // biome-ignore lint/correctness/useExhaustiveDependencies: `picks` makes each pick a new target.
  const asked = useMemo(() => parsePart(id, part), [id, part, picks]);
  const there = asked !== null && agent !== undefined && agentHas(agent, asked);
  const target = useMemo<CanvasTarget>(
    () => (there && asked) || { kind: "agent", agentId: id },
    [there, asked, id],
  );
  const highlight = useMemo(() => [target], [target]);
  if (!model || !agent) return null;

  return (
    <Workspace
      inspectorLabel="Blueprint details"
      main={
        <div className="flex h-full flex-col">
          <ScreenBar
            icon="agent"
            tone="agent"
            kind="Agent blueprint"
            title={agent.label}
            chips={
              agent.warnings.length > 0 ? (
                <WarningsChip
                  count={agent.warnings.length}
                  onShow={() => {
                    pick(null);
                    setReveal({ section: "warnings" });
                  }}
                />
              ) : undefined
            }
            aside={<CopyButton text={() => agentMermaid(model)} label="Copy Mermaid" />}
          />
          {/* Under the bar, so the inspector toggle keeps its place at the bar's end. */}
          <WaitingForRun />
          <div className="min-h-0 flex-1">
            <AgentCanvas
              model={model}
              highlight={highlight}
              onPick={pick}
              onClear={() => pick(null)}
            />
          </div>
        </div>
      }
      inspector={
        <InspectorPick.Provider value={pick}>
          <BlueprintInspector target={target} reveal={reveal} />
        </InspectorPick.Provider>
      }
      openRequest={reveal}
    />
  );
}

export function AgentScreen({ id, part }: { id: string; part?: string | undefined }) {
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
  return <AgentBlueprintView key={id} id={id} part={part} />;
}
