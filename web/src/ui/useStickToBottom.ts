import { type RefObject, useEffect, useLayoutEffect, useRef } from "react";

/** How far from the bottom (px, about one row) still counts as being at the bottom. */
const SLACK = 32;

const itself = (element: HTMLElement): HTMLElement | null => element;

/**
 * Keeps a scroll area on its newest content, the way a log tail does: while `active`, every
 * change of `growth` scrolls to the bottom. Scrolling away from the bottom lets go, so earlier
 * content can be read while more arrives; scrolling back to the bottom takes hold again.
 *
 * The returned ref goes on the scroll area, or on an element inside one it does not own: then
 * `scrollerOf` (a stable function) finds the scroll area from that element.
 */
export function useStickToBottom<T extends HTMLElement>(
  active: boolean,
  growth: unknown,
  scrollerOf: (element: T) => HTMLElement | null = itself,
): RefObject<T | null> {
  const ref = useRef<T>(null);
  const stuck = useRef(true);

  useEffect(() => {
    const scroller = ref.current ? scrollerOf(ref.current) : null;
    if (!scroller) return;
    // Our own scroll to the bottom lands here too, and reads as "at the bottom".
    const onScroll = (): void => {
      stuck.current = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < SLACK;
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [scrollerOf]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when content is added, not when the clock starts or stops.
  useLayoutEffect(() => {
    const scroller = ref.current ? scrollerOf(ref.current) : null;
    if (scroller && active && stuck.current) scroller.scrollTop = scroller.scrollHeight;
  }, [growth]);

  return ref;
}
