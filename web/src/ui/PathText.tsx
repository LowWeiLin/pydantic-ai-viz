import { cx } from "./tone";

/**
 * A file path on one line that gives way from its folder first, then from the end of the file
 * name. A suffix (":42", the line) is never cut. What tells one file from the next is its name,
 * and the line is what an editor needs, so they are the last to go.
 *
 * The name and the suffix are one part that does not shrink, but is never wider than the
 * whole: the folder takes what room is left, and only a name that does not fit on its own is
 * cut. (Shrink factors cannot do this: any share of the shrinking, however small, puts an
 * ellipsis on the name.)
 *
 * The folder is cut from its start, so the folders nearest the file stay in view:
 * "...services/newsroom/". It overflows right to left, with the path itself isolated as left to
 * right so that its slashes stay where they are. It keeps two characters at the least, room for
 * the ellipsis and its last slash: with one, the slash would show alone and the path would read
 * as starting at the root ("/medical.py"); cut from the end instead, a lone first letter would
 * read as part of the name ("pmedical.py").
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
        <span dir="rtl" className={cx("min-w-[2ch] truncate", folderClassName)}>
          <bdi dir="ltr">{path.slice(0, cut)}</bdi>
        </span>
      )}
      <span
        className={cx("flex min-w-0 shrink-0", cut > 0 ? "max-w-[calc(100%-2ch)]" : "max-w-full")}
      >
        <span className="min-w-0 truncate">{path.slice(cut)}</span>
        {suffix !== "" && <span className="shrink-0">{suffix}</span>}
      </span>
    </span>
  );
}
