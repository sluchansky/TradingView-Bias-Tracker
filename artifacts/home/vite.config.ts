import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import runtimeErrorOverlay from "@replit/vite-plugin-runtime-error-modal";

const rawPort = process.env.PORT;

// PORT only matters for the dev/preview server. During the production build
// (static output) it is not needed, so fall back to a default instead of
// throwing — otherwise the publish build fails if the env is not injected.
const port = rawPort ? Number(rawPort) : 5173;

if (rawPort && (Number.isNaN(port) || port <= 0)) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// This artifact is always served at the root path; default to "/" so the
// production build never depends on BASE_PATH being present in the env.
const basePath = process.env.BASE_PATH ?? "/";
// Replit's artifact router forwards /api at the deployment boundary. A local
// Windows Vite server needs the same path bridge explicitly so the browser
// talks to the Express proxy rather than asking Vite for an SPA fallback.
const localApiProxyTarget = process.env.LOCAL_API_PROXY_TARGET ?? "http://127.0.0.1:8080";
const localApiProxyEnabled =
  process.env.LOCAL_API_PROXY === "1" ||
  process.env.LOCAL_API_PROXY_TARGET != null;
const localApiProxy = localApiProxyEnabled
  ? {
      "/api": {
        target: localApiProxyTarget,
        changeOrigin: false,
        secure: false,
      },
    }
  : undefined;
const localDashboardHost = process.env.LOCAL_DASHBOARD_HOST ?? "0.0.0.0";

export default defineConfig({
  base: basePath,
  plugins: [
    react(),
    tailwindcss(),
    runtimeErrorOverlay(),
    ...(process.env.NODE_ENV !== "production" &&
    process.env.REPL_ID !== undefined
      ? [
          await import("@replit/vite-plugin-cartographer").then((m) =>
            m.cartographer({
              root: path.resolve(import.meta.dirname, ".."),
            }),
          ),
          await import("@replit/vite-plugin-dev-banner").then((m) =>
            m.devBanner(),
          ),
        ]
      : []),
  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@assets": path.resolve(import.meta.dirname, "..", "..", "attached_assets"),
    },
    dedupe: ["react", "react-dom"],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
  },
  server: {
    port,
    strictPort: true,
    host: localDashboardHost,
    allowedHosts: true,
    ...(localApiProxy ? { proxy: localApiProxy } : {}),
    fs: {
      strict: true,
    },
  },
  preview: {
    port,
    host: localDashboardHost,
    allowedHosts: true,
    ...(localApiProxy ? { proxy: localApiProxy } : {}),
  },
});