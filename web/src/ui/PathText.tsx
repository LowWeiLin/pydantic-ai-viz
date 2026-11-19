import { cx } from "./tone";

/**
 * A file path on one line that gives way from its folder first, then from the end of the file
 * name. A suffix (":42", the line) is never cut. What tells one file from the next is its name,
 * and the line is what an editor needs, so they are the last to go.
 *
 * The folder's shrink factor dwarfs the name's: the name keeps its width until the folder has
 * none left. The parts are plain text in a row, so the path is read and copied as one.
 */
export function PathText({
  path,
  suffix = "",
  folderClassName,
  className,
}: {
  path: string;
  suffix?: string;
  folderClassName?: string;
  className?: string;
}) {
  const cut = path.lastIndexOf("/") + 1;
  return (
    <span className={cx("flex min-w-0", className)}>
      {cut > 0 && (
        <span className={cx("min-w-0 shrink-[1000] truncate", folderClassName)}>
          {path.slice(0, cut)}
        </span>
      )}
      <span className="min-w-0 truncate">{path.slice(cut)}</span>
      {suffix !== "" && <span className="shrink-0">{suffix}</span>}
    </span>
  );
}
