import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TvApp from "./TvApp";
import Boundary from "../Boundary";
import "./tv.css";

/** Everything that can go wrong on a television, reported rather than silent.
 *
 * A screen across the room has no console anybody will open, and the app has
 * already crashed there twice without leaving a trace: the native side reports
 * its crashes, but nothing here reported a JavaScript one, so the log was empty
 * and the diagnosis was guesswork. The phone app got a boundary and this did
 * not, which is exactly backwards — a phone has developer tools within reach
 * and a television has nothing.
 *
 * Two nets, because React's boundary catches only what happens inside a render.
 * A failure in a socket handler, a timer or a promise never touches it.
 */
window.addEventListener("error", (e) => {
  reportToServer("tv-error", `${e.message}\n  at ${e.filename}:${e.lineno}:${e.colno}`);
});

window.addEventListener("unhandledrejection", (e) => {
  const reason = e.reason;
  reportToServer(
    "tv-rejection",
    reason instanceof Error ? `${reason.message}\n${reason.stack ?? ""}` : String(reason),
  );
});

/** Best effort, and deliberately quiet about its own failure.
 *
 * Rate-limited to one report a minute per kind: a fault inside a two-second
 * timer would otherwise report thirty times a minute for as long as the screen
 * is on, and drown the log it exists to fill.
 */
const lastSent: Record<string, number> = {};

function reportToServer(what: string, detail: string) {
  const now = Date.now();
  if (now - (lastSent[what] ?? 0) < 60_000) return;
  lastSent[what] = now;

  void fetch("/api/renderers/crash", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      device: navigator.userAgent.slice(0, 120),
      thread: what,
      version: "tv-web",
      trace: detail.slice(0, 9000),
    }),
  }).catch(() => undefined);
}

createRoot(document.getElementById("tv-root")!).render(
  <StrictMode>
    <Boundary what="tv">
      <TvApp />
    </Boundary>
  </StrictMode>,
);
