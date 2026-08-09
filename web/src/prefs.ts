import { api } from "./api";

export type Palette = "warm" | "studio" | "daylight";
export type Appearance = "auto" | "light" | "dark";
export type View = "details" | "columns" | "tiles-small" | "tiles-large";

export interface Prefs {
  palette: Palette;
  appearance: Appearance;
  view: View;
}

export const DEFAULT_PREFS: Prefs = {
  palette: "warm",
  appearance: "auto",
  view: "details",
};

export const PALETTES: { id: Palette; name: string; blurb: string; swatch: string[] }[] = [
  {
    id: "warm",
    name: "Listening Room",
    blurb: "Warm graphite lit by valve-amp amber. Built for a dim room in the evening.",
    swatch: ["#1b1917", "#ece7e0", "#e0a458"],
  },
  {
    id: "studio",
    name: "Studio",
    blurb: "Cool slate and VU-needle teal. Quieter, more technical.",
    swatch: ["#14181a", "#e3eaec", "#59b6ab"],
  },
  {
    id: "daylight",
    name: "Daylight",
    blurb: "Warm paper and indigo. Easiest on the eyes for documents and long sessions.",
    swatch: ["#f6f4ef", "#232026", "#4a4b96"],
  },
];

export const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "details", label: "Details", hint: "Name, duration, size and date" },
  { id: "columns", label: "Columns", hint: "Names only, flowing into columns — for huge folders" },
  { id: "tiles-small", label: "Small tiles", hint: "Compact grid" },
  { id: "tiles-large", label: "Large tiles", hint: "Big thumbnails, for photos and video" },
];

export const getPrefs = () => api.get<Prefs>("/api/prefs");
export const savePrefs = (patch: Partial<Prefs>) => api.put<Prefs>("/api/prefs", patch);

/** Push the palette onto <html> so every token resolves from one place. */
export function applyPrefs(p: Prefs): void {
  const root = document.documentElement;
  root.dataset.palette = p.palette;
  if (p.appearance === "auto") {
    delete root.dataset.appearance;
  } else {
    root.dataset.appearance = p.appearance;
  }
}
