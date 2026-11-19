import { cx } from "./tone";

/**
 * A file path on one line that gives way from its folder first, then from the end of the file
 * name. A suffix (":42", the line) is never cut. What tells one file from the next is its name,
 * and the line is what an editor needs, so they are the last to go.
 *
 * The name and the suffix are one part that does not shrink, but is never wider than the
 * whole: the folder takes what room is left, down to none, and only a name that does not fit on
 * its own is cut. (Shrink factors cannot do this: any share of the shrinking, however small,
 * puts an ellipsis on the name.) The parts are plain text in a row, so the path is read and
 * copied as one.
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
        <span className={cx("min-w-0 truncate", folderClassName)}>{path.slice(0, cut)}</span>
      )}
      <span className="flex min-w-0 max-w-full shrink-0">
        <span className="min-w-0 truncate">{path.slice(cut)}</span>
        {suffix !== "" && <span className="shrink-0">{suffix}</span>}
      </span>
    </span>
  );
}
