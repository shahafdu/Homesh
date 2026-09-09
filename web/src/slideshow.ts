import { api } from "./api";

/** Showing a folder of photographs, here or in a room.
 *
 * The gathering is recursive because that is how photographs are kept: a year,
 * with months inside it, with a weekend inside that. Asking for "2019" and
 * getting only the loose files at the top of it would be a slideshow of the
 * wrong thing.
 */

export interface PhotoSet {
  under: string;
  item_ids: string[];
  count: number;
  /** How many are actually there, which may be far more than were sent. */
  total: number;
  truncated: boolean;
  shuffled: boolean;
}

/** Gather a folder's photographs.
 *
 * Shuffling is asked of the server rather than done here, and that is not an
 * optimisation. This house has 105,000 photographs and only ten thousand are
 * sent; shuffling the ones that arrived would be a random ordering of one
 * corner of the library while claiming to be a random ordering of all of it.
 * Only the database can take a sample of everything.
 */
export const gatherPhotos = (under: string, shuffle = false) =>
  api.get<PhotoSet>(
    `/api/slideshow?under=${encodeURIComponent(under)}${shuffle ? "&shuffle=1" : ""}`,
  );

/** How one photograph gives way to the next. */
export const TRANSITIONS = [
  { id: "fade", label: "Fade" },
  { id: "slide", label: "Slide" },
  { id: "zoom", label: "Zoom" },
  { id: "none", label: "Cut" },
  // Chosen per photograph rather than once, so a long slideshow does not
  // settle into a rhythm — which is the whole reason to ask for random.
  { id: "random", label: "Random" },
] as const;

export type Transition = (typeof TRANSITIONS)[number]["id"];

/** The ones a random slideshow picks between. Not "none", which would read as a
 *  transition having failed rather than as a choice, and not "random" itself. */
const PICKABLE: Transition[] = ["fade", "slide", "zoom"];

export function resolveTransition(chosen: Transition): Exclude<Transition, "random"> {
  if (chosen !== "random") return chosen;
  return PICKABLE[Math.floor(Math.random() * PICKABLE.length)] as Exclude<
    Transition,
    "random"
  >;
}

/** Seconds per photograph, offered as choices rather than as a number to type.
 *
 * Nobody has an opinion about 7 seconds. The useful distinction is between
 * glancing, looking, and leaving it on the wall. */
export const HOLDS = [
  { ms: 3000, label: "3s" },
  { ms: 5000, label: "5s" },
  { ms: 8000, label: "8s" },
  { ms: 15000, label: "15s" },
  { ms: 30000, label: "30s" },
  { ms: 60000, label: "1m" },
] as const;

export interface ShowSettings {
  holdMs: number;
  transition: Transition;
  shuffle: boolean;
}

export const DEFAULTS: ShowSettings = { holdMs: 5000, transition: "fade", shuffle: false };

/** A Fisher-Yates shuffle over a copy.
 *
 * For a list already in hand — a room being sent a queue that was gathered
 * unshuffled. Gathering with `shuffle` is the better route when the folder is
 * large, because it samples the whole of it rather than reordering a slice.
 */
export function shuffled<T>(items: T[]): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
