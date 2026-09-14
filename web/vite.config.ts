import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// When this build was made, baked in.
//
// A phone can sit on a page loaded days ago -- a tab that was never closed, a
// Custom Tab resumed from the background -- and then a fix that is live looks
// like a fix that was never made. That has now cost an exchange of "it is fixed"
// and "it is not", with both of us right. Settings shows this, so the question
// "which build am I looking at" has an answer instead of an argument.
const BUILT = new Date().toISOString();

export default defineConfig({
  define: { __BUILT__: JSON.stringify(BUILT) },
  plugins: [react()],
  server: {
    port: 5173,
    // Dev only. In production the API serves the built assets from the same
    // origin, which keeps cookies and the WebAuthn RP ID simple.
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      // Two entry points, one codebase. The phone and the TV are different
      // interfaces to the same system, not different applications.
      input: {
        main: resolve(__dirname, "index.html"),
        tv: resolve(__dirname, "tv.html"),
      },
    },
  },
});
