// Vite + TanStack Start + Cloudflare Workers configuration.
//
// We wire each plugin explicitly rather than depending on a meta-package,
// so the build stays auditable and easy to debug. Order matters:
//   1. tsconfigPaths       resolves the @ alias from tsconfig.json
//   2. tailwindcss         JIT pipeline used by src/styles.css
//   3. tanstackStart       file-based router + SSR entry redirect
//   4. react               JSX transform (after tanstackStart so it
//                          processes the generated route tree)
//   5. cloudflare          builds the Workers entry from src/server.ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsconfigPaths from "vite-tsconfig-paths";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import { cloudflare } from "@cloudflare/vite-plugin";

export default defineConfig(({ command }) => {
  const isBuild = command === "build";

  return {
    plugins: [
      tsconfigPaths(),
      tailwindcss(),
      // Redirect TanStack Start's bundled server entry to src/server.ts
      // (our SSR error wrapper). wrangler.jsonc main alone is insufficient
      // because @cloudflare/vite-plugin builds from this entry.
      tanstackStart({ server: { entry: "server" } }),
      react(),
      // The Cloudflare plugin is only required for building Workers bundles.
      // In dev, it can cause the server runtime to be bundled outside of Start's
      // virtual-module pipeline, which breaks TanStack Start.
      ...(isBuild ? [cloudflare()] : []),
    ],
    resolve: {
      alias: { "@": path.resolve(__dirname, "src") },
      // Ensure a single React + TanStack instance across SSR + client bundles;
      // duplicates manifest as cryptic invalid hook call errors.
      dedupe: ["react", "react-dom", "@tanstack/react-router", "@tanstack/react-query"],
    },
    server: {
      port: 5173,
      strictPort: true,
      host: true,
      // Dev: browser calls /api and /ws on the Vite origin (same-origin, no CORS).
      // Production builds talk to the backend URL from VITE_API_BASE or absolute defaults.
      proxy: {
        "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
        "/ws": { target: "http://127.0.0.1:8000", ws: true, changeOrigin: true },
      },
    },
    // TanStack Start injects a few virtual modules (e.g. `#tanstack-router-entry`).
    // Any attempt to prebundle Start's server/runtime in plain esbuild will fail.
    optimizeDeps: {
      noDiscovery: true,
      include: [],
      exclude: [
        "@tanstack/react-start",
        "@tanstack/react-start-server",
        "@tanstack/react-start-rsc",
        "@tanstack/start-plugin-core",
        "@tanstack/start-server-core",
      ],
    },
  };
});
