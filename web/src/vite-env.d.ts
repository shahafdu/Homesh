/// <reference types="vite/client" />

/** Vite's `?url` suffix returns the built asset's address as a string.
 *
 * Needed for the pdf.js worker: it has to be loaded as a separate script, and
 * pointing at a CDN is not an option — the file must come from this server, both
 * because a strict CSP forbids the alternative and because a document viewer
 * that fetches code from somewhere else is not one that keeps documents at home.
 */
declare module "*?url" {
  const url: string;
  export default url;
}
