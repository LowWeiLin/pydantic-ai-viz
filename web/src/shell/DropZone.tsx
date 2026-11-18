/**
 * Loading a recording from disk: a visible target with a file picker, plus a page-wide drop
 * overlay so a file can be dropped anywhere, on any screen.
 */
import { useEffect, useRef, useState } from "react";
import { RecordingError, readRecordingFile } from "../data/fileSource";
import { useViewer } from "../data/store";
import { Icon } from "../ui/Icon";
import { cx } from "../ui/tone";
import { navigate } from "./router";

/** Reads a `.pviz.jsonl` file into the store and goes to its latest run. */
export async function openRecordingFile(file: File): Promise<void> {
  const store = useViewer.getState();
  if (store.source.kind === "live") return; // A live session is never replaced by a drop.
  store.setLoading();
  try {
    const envelopes = await readRecordingFile(file);
    useViewer.getState().openRecording({ kind: "recording", name: file.name }, envelopes);
    navigate({ name: "home" });
  } catch (error) {
    useViewer
      .getState()
      .fail(error instanceof RecordingError ? error : new RecordingError(file.name, String(error)));
  }
}

const hasFiles = (event: DragEvent): boolean =>
  Array.from(event.dataTransfer?.types ?? []).includes("Files");

/** Mount once. Shows an overlay while a file is dragged over the window and loads it on drop. */
export function WindowDropTarget() {
  const [over, setOver] = useState(false);
  const live = useViewer((state) => state.source.kind === "live");

  useEffect(() => {
    if (live) return;
    let depth = 0;
    const enter = (event: DragEvent): void => {
      if (!hasFiles(event)) return;
      depth += 1;
      setOver(true);
    };
    const leave = (event: DragEvent): void => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setOver(false);
    };
    const dragOver = (event: DragEvent): void => {
      if (hasFiles(event)) event.preventDefault(); // Required for `drop` to fire.
    };
    const drop = (event: DragEvent): void => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setOver(false);
      const file = event.dataTransfer?.files[0];
      if (file) void openRecordingFile(file);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", dragOver);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", dragOver);
      window.removeEventListener("drop", drop);
    };
  }, [live]);

  if (!over) return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-scrim p-6">
      <div className="tone-accent flex flex-col items-center gap-2 rounded-card border-2 border-accent border-dashed bg-surface px-10 py-8 text-center shadow-float">
        <Icon name="upload" size={24} className="tone-text" />
        <p className="m-0 font-semibold text-title">Drop to open the recording</p>
        <p className="m-0 text-muted text-ui">
          It replaces what is on screen. Nothing is uploaded.
        </p>
      </div>
    </div>
  );
}

/** The visible target: click or press Enter to pick a file, or drop one on it. */
export function DropZone({
  pickLabel = "choose a file",
  className,
}: {
  /** The words of the file-picker link, e.g. "choose another file". */
  pickLabel?: string;
  className?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      className={cx(
        "flex flex-col items-center gap-2 rounded-card border border-line-strong border-dashed bg-surface px-6 py-7 text-center",
        "transition-colors duration-150 ease-out hover:border-accent",
        className,
      )}
    >
      <Icon name="upload" size={20} className="text-faint" />
      <p className="m-0 text-em text-fg">
        Drop a <code>.pviz.jsonl</code> recording anywhere on this page
      </p>
      <p className="m-0 text-muted text-ui">
        or{" "}
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="font-medium text-accent underline underline-offset-2"
        >
          {pickLabel}
        </button>
        . It is read in your browser and never uploaded.
      </p>
      <input
        ref={input}
        type="file"
        accept=".jsonl,.pviz.jsonl,application/x-ndjson,application/jsonl"
        aria-label="Recording file"
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void openRecordingFile(file);
        }}
      />
    </div>
  );
}
