import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
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
