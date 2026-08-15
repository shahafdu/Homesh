import { useEffect } from "react";

/** Stop the page behind a sheet from scrolling while it is open.
 *
 * Without this, dragging anywhere on a modal scrolls the folder underneath it —
 * the sheet stays put while its context slides away, which reads as the app
 * having lost track of what you tapped.
 *
 * The scroll position is restored on close: simply setting `overflow: hidden`
 * makes the page jump to the top the moment the sheet opens on iOS, which is a
 * worse bug than the one it fixes.
 */
export function useLockScroll(): void {
  useEffect(() => {
    const { body } = document;
    const y = window.scrollY;
    const previous = {
      overflow: body.style.overflow,
      position: body.style.position,
      top: body.style.top,
      width: body.style.width,
    };

    body.style.overflow = "hidden";
    body.style.position = "fixed";
    body.style.top = `-${y}px`;
    body.style.width = "100%";

    return () => {
      body.style.overflow = previous.overflow;
      body.style.position = previous.position;
      body.style.top = previous.top;
      body.style.width = previous.width;
      window.scrollTo(0, y);
    };
  }, []);
}
