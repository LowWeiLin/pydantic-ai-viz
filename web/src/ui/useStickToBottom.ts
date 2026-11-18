import { type RefObject, useEffect, useLayoutEffect, useRef } from "react";

/** How far from the bottom (px, about one row) still counts as being at the bottom. */
const SLACK = 32;
const KEYS_UP = new Set(["ArrowUp", "PageUp", "Home"]);

const itself = (element: HTMLElement): HTMLElement | null => element;

/**
 * Keeps a scroll area on its newest content, the way a log tail does: while `active`, every
 * change of `growth` scrolls to the bottom. Scrolling away from the bottom lets go, so earlier
 * content can be read while more arrives; scrolling back down to the bottom takes hold again.
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
    let lastTop = scroller.scrollTop;
    const letGo = (): void => {
      stuck.current = false;
    };
    // Distance alone is not enough to let go: text that streams in many times a second would
    // pull a slow scroll back before it had got a row away. So the wish to go up counts at
    // once: the wheel, the keys, a finger, a press on the scrollbar.
    const onWheel = (event: WheelEvent): void => {
      if (event.deltaY < 0) letGo();
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (KEYS_UP.has(event.key)) letGo();
    };
    const onPointerDown = (event: PointerEvent): void => {
      if (event.target === scroller && event.offsetX >= scroller.clientWidth) letGo();
    };
    const onScroll = (): void => {
      const top = scroller.scrollTop;
      const fromBottom = scroller.scrollHeight - top - scroller.clientHeight;
      if (fromBottom >= SLACK) letGo();
      // Only a move down takes hold again. Our own scroll is one; a few pixels lost when
      // content settles (a row's entry animation) are not.
      else if (top > lastTop) stuck.current = true;
      lastTop = top;
    };
    const passive = { passive: true };
    scroller.addEventListener("scroll", onScroll, passive);
    scroller.addEventListener("wheel", onWheel, passive);
    scroller.addEventListener("touchstart", letGo, passive);
    scroller.addEventListener("keydown", onKeyDown);
    scroller.addEventListener("pointerdown", onPointerDown);
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("wheel", onWheel);
      scroller.removeEventListener("touchstart", letGo);
      scroller.removeEventListener("keydown", onKeyDown);
      scroller.removeEventListener("pointerdown", onPointerDown);
    };
  }, [scrollerOf]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when content is added, not when the clock starts or stops.
  useLayoutEffect(() => {
    const scroller = ref.current ? scrollerOf(ref.current) : null;
    if (scroller && active && stuck.current) scroller.scrollTop = scroller.scrollHeight;
  }, [growth]);

  return ref;
}
