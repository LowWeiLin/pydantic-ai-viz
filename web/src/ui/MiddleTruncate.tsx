import { cx } from "./tone";

/** Characters of the end that always stay visible. */
const TAIL = 9;

/**
 * A one-line name that keeps its end when it does not fit: `lookup_customer_rec…_system_3`.
 *
 * Tool names from one toolset or one API often share a long prefix and differ only at the end.
 * An ellipsis at the end makes them all read the same; this puts it in the middle. Only the head
 * is clipped, by CSS, so the cut adapts to whatever width the name is given.
 *
 * The two visible halves are hidden from assistive technology, which would read them as two
 * words, and the whole name is given to it in one piece. That copy cannot be selected, so
 * copying the visible text yields the name once.
 */
export function MiddleTruncate({ text, className }: { text: string; className?: string }) {
  if (text.length <= TAIL * 2) return <span className={cx("truncate", className)}>{text}</span>;
  return (
    <span className={cx("flex min-w-0", className)}>
      <span className="sr-only select-none">{text}</span>
      <span aria-hidden="true" className="min-w-0 truncate">
        {text.slice(0, -TAIL)}
      </span>
      <span aria-hidden="true" className="shrink-0 whitespace-pre">
        {text.slice(-TAIL)}
      </span>
    </span>
  );
}
