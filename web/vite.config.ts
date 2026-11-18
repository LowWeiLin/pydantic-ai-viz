import { createReadStream, existsSync, renameSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { defineConfig } from "vitest/config";

const root = path.dirname(fileURLToPath(import.meta.url));
const fixturesDir = path.join(root, "fixtures");

/** The policy the shipped viewer runs under: everything inline, no network except its own origin. */
const CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data:; font-src data:; connect-src 'self'";

const CSP_SLOT = "<!--pviz:csp-->";

/**
 * Adds the CSP meta tag to the built page only. The dev server loads modules by URL,
 * which `script-src 'unsafe-inline'` would block.
 */
function contentSecurityPolicy(): Plugin {
  return {
    name: "pviz:csp",
    apply: "build",
    transformIndexHtml(html) {
      const meta = `<meta http-equiv="Content-Security-Policy" content="${CSP}" />`;
      if (!html.includes(CSP_SLOT)) throw new Error(`index.html is missing ${CSP_SLOT}`);
      return html.replace(CSP_SLOT, meta);
    },
  };
}

/**
 * Dev only: serves `web/fixtures` at `/fixtures/*`, and again at `/recordings/*` so the
 * gallery (which looks for `recordings/index.json`) works under `pnpm dev`.
 */
function serveFixtures(): Plugin {
  const types: Record<string, string> = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
  };
  return {
    name: "pviz:fixtures",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = (req.url ?? "").split("?")[0] ?? "";
        const match = /^\/(?:fixtures|recordings)\/([^/]+)$/.exec(pathname);
        if (!match?.[1]) return next();
        const file = path.join(fixturesDir, path.basename(decodeURIComponent(match[1])));
        if (!existsSync(file) || !statSync(file).isFile()) return next();
        res.setHeader("Content-Type", types[path.extname(file)] ?? "application/octet-stream");
        res.setHeader("Cache-Control", "no-store");
        createReadStream(file).pipe(res);
      });
    },
  };
}

/** The wheel and the demo site expect `viewer.html`, not Vite's default `index.html`. */
function renameToViewer(): Plugin {
  return {
    name: "pviz:rename-viewer",
    apply: "build",
    closeBundle() {
      const built = path.join(root, "dist", "index.html");
      if (existsSync(built)) renameSync(built, path.join(root, "dist", "viewer.html"));
    },
  };
}

export default defineConfig({
  // Relative asset base: the viewer is opened from file:// and served behind arbitrary prefixes.
  base: "./",
  publicDir: false,
  plugins: [
    react(),
    tailwindcss(),
    viteSingleFile({ removeViteModuleLoader: true }),
    contentSecurityPolicy(),
    serveFixtures(),
    renameToViewer(),
  ],
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    reportCompressedSize: false,
  },
  server: {
    open: "/?src=/fixtures/sample.pviz.jsonl",
    proxy: {
      // `pnpm dev` + `?live` talks to a running `pydantic-ai-viz` server.
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
  },
});
