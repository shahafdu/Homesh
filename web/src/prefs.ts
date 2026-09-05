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
    name: "Hearth",
    blurb: "Warm charcoal lit by amber. Built for a dim room in the evening.",
    swatch: ["#17130f", "#f2ebe2", "#f5a94d"],
  },
  {
    id: "studio",
    name: "Studio",
    blurb: "Deep slate and instrument cyan. Cooler, and the highest contrast of the three.",
    swatch: ["#0e1318", "#e6edf3", "#4cc2e8"],
  },
  {
    id: "daylight",
    name: "Violet",
    blurb: "Ink and orchid. Softest of the three for long reading.",
    swatch: ["#14111c", "#ede8f5", "#b79bff"],
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
