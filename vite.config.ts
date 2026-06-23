// Vite + TanStack Start — dual deploy targets:
//   • Vercel: nitro/vite (auto when VERCEL=1 or DEPLOY_TARGET=vercel)
//   • Cloudflare Workers: @cloudflare/vite-plugin (default local prod build)
//
// Plugin order: tsconfigPaths → tailwind → tanstackStart → deploy adapter → react
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsconfigPaths from "vite-tsconfig-paths";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import { cloudflare } from "@cloudflare/vite-plugin";
import { nitro } from "nitro/vite";

export type DeployTarget = "vercel" | "cloudflare";

/** Resolved once per Vite process (build/dev server start). */
export function resolveDeployTarget(): DeployTarget {
  const explicit = process.env.DEPLOY_TARGET?.trim().toLowerCase();
  if (explicit === "vercel" || explicit === "cloudflare") {
    return explicit;
  }
  // Vercel sets this during `vercel build` / Git deployments.
  if (process.env.VERCEL === "1") {
    return "vercel";
  }
  return "cloudflare";
}

export default defineConfig(({ command }) => {
  const isBuild = command === "build";
  const deployTarget = resolveDeployTarget();

  return {
    plugins: [
      tsconfigPaths(),
      tailwindcss(),
      tanstackStart({ server: { entry: "server" } }),
      ...(isBuild && deployTarget === "vercel"
        ? [
            nitro({
              preset: "vercel",
              vercel: { entryFormat: "node" },
            }),
          ]
        : []),
      react(),
      // Cloudflare plugin only for Workers bundles (not used on Vercel).
      ...(isBuild && deployTarget === "cloudflare" ? [cloudflare()] : []),
    ],
    resolve: {
      alias: { "@": path.resolve(__dirname, "src") },
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
    optimizeDeps: {
      noDiscovery: true,
      include: ["recharts"],
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
