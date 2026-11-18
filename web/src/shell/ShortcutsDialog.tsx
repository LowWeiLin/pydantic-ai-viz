import { Kbd } from "../ui/Badge";
import { Dialog } from "../ui/overlays";
import { SHORTCUTS } from "./shortcuts";

const GROUPS = ["Timeline", "Playback", "View"] as const;

export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Keyboard shortcuts"
      description="Shortcuts are off while you type in a field."
    >
      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        {GROUPS.map((group) => (
          <section key={group} aria-label={group}>
            <h3 className="m-0 mb-1.5 font-semibold text-faint text-meta uppercase tracking-[0.06em]">
              {group}
            </h3>
            <dl className="m-0 flex flex-col gap-1.5">
              {SHORTCUTS.filter((s) => s.group === group).map((shortcut) => (
                <div key={shortcut.id} className="flex items-center justify-between gap-3">
                  <dt className="text-ui">{shortcut.label}</dt>
                  <dd className="m-0 flex shrink-0 gap-1">
                    {shortcut.keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
