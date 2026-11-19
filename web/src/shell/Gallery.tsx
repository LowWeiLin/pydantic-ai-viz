import { recordingUrl } from "../data/fileSource";
import { useViewer } from "../data/store";
import { ButtonLink } from "../ui/Button";
import type { RecordingEntry } from "../wire";
import { DropZone } from "./DropZone";

/**
 * The link that plays a recording: `?src=<file>#/`. It is a plain page load, so the address
 * can be shared and reloaded, and "back" returns to the gallery.
 */
export function recordingHref(entry: RecordingEntry, base: string = window.location.href): string {
  const page = new URL(base);
  const directory = new URL(".", page).href;
  const target = recordingUrl(entry, base);
  const src = target.startsWith(directory) ? target.slice(directory.length) : target;
  return `${page.pathname}?src=${encodeURIComponent(src)}#/`;
}

export function RecordingCard({ entry, playing }: { entry: RecordingEntry; playing: boolean }) {
  const action = entry.kind === "blueprint" ? "Open" : "Play";
  return (
    <li className="flex flex-col gap-2 rounded-card border border-line bg-surface p-4 transition-colors duration-150 ease-out hover:border-line-strong">
      <h3 className="m-0 font-semibold text-em">{entry.title}</h3>
      <p className="m-0 flex-1 text-muted text-ui">{entry.description}</p>
      <div className="mt-1 flex items-center justify-between gap-2">
        <ButtonLink
          href={recordingHref(entry)}
          variant="primary"
          icon={entry.kind === "blueprint" ? "blueprint" : "play"}
          aria-label={`${action} ${entry.title}`}
        >
          {action}
        </ButtonLink>
        <span className="truncate font-mono text-faint text-meta">
          {playing ? "loaded" : entry.src}
        </span>
      </div>
    </li>
  );
}

export function Gallery() {
  const entries = useViewer((state) => state.gallery);
  const loaded = useViewer((state) =>
    state.source.kind === "recording" ? state.source.name : null,
  );
  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
        <header className="flex flex-col gap-1">
          <h2 className="m-0 font-semibold text-page tracking-[-0.01em]">Recordings</h2>
          <p className="m-0 max-w-2xl text-em text-muted">
            Each recording is a session captured by pydantic-ai-viz, replayed in your browser.
            Nothing here calls a model or needs an API key.
          </p>
        </header>
        {entries && entries.length > 0 ? (
          <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3 p-0">
            {entries.map((entry) => (
              <RecordingCard
                key={entry.id}
                entry={entry}
                playing={loaded !== null && entry.src.endsWith(loaded)}
              />
            ))}
          </ul>
        ) : (
          <p className="m-0 text-muted text-ui">
            This page has no recording index. Open a recording from your disk instead.
          </p>
        )}
        <DropZone />
        <p className="m-0 text-faint text-meta">
          Made with{" "}
          <a href="https://github.com/LowWeiLin/pydantic-ai-viz" className="text-muted underline">
            pydantic-ai-viz
          </a>
          , an unofficial viewer for Pydantic AI agents. To see your own:{" "}
          <code className="font-mono">uvx pydantic-ai-viz view .</code>
        </p>
      </div>
    </div>
  );
}
