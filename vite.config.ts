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

export default defineConfig({
  plugins: [
    tsconfigPaths(),
    tailwindcss(),
    // Redirect TanStack Start's bundled server entry to src/server.ts
    // (our SSR error wrapper). wrangler.jsonc main alone is insufficient
    // because @cloudflare/vite-plugin builds from this entry.
    tanstackStart({ server: { entry: "server" } }),
    react(),
    cloudflare(),
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
  },
});
