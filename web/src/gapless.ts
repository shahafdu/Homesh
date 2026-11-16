/** The decisions behind gapless playback, kept free of the DOM so the type
 *  checker and the unit tests cover them. usePlayer.ts keeps the elements. */

/** Seconds before the end at which the following track starts loading. A signed
 *  URL lives five minutes, so this is well inside it; earlier would buffer
 *  needlessly on mobile data. */
export const PRELOAD_SECONDS = 20;

/** HAVE_FUTURE_DATA: enough buffered to start without stalling. */
const HAVE_FUTURE_DATA = 3;

/** Whether the track that follows should begin loading now.
 *
 *  `nextTrack` is whatever is due next, or null when there is nothing (end of
 *  the queue, or nothing playable). A duration the element has not reported yet
 *  is NaN or 0 and never counts: guessing would preload at the first second. */
export function shouldPreload(
  position: number,
  duration: number,
  nextTrack: { item_id: string } | null,
  alreadyFor: string | null = null,
): boolean {
  if (!nextTrack) return false;
  if (alreadyFor === nextTrack.item_id) return false;
  if (!isFinite(duration) || duration <= 0) return false;
  return duration - position < PRELOAD_SECONDS;
}

/** Whether the waiting element may take over from the one that just ended. It
 *  must hold the track that is due, and have enough of it to play. */
export function canHandOver(
  readyState: number,
  dueId: string | null,
  loadedId: string | null,
): boolean {
  return dueId !== null && dueId === loadedId && readyState >= HAVE_FUTURE_DATA;
}
