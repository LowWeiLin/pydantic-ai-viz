import {
  createReadStream,
  existsSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
} from "node:fs";
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

/** Opens the notices; `copy-viewer.mjs` checks that the built page has them. */
const NOTICES_HEADING = "Third-party software in this file";

/** The package a bundled module comes from: its nearest package.json inside node_modules. */
function packageRoot(moduleId: string): string | null {
  const file = moduleId.replace(/^\0/, "").split("?")[0] ?? "";
  if (!file.includes(`${path.sep}node_modules${path.sep}`) && !file.includes("/node_modules/")) {
    return null;
  }
  for (let dir = path.dirname(file); dir !== path.dirname(dir); dir = path.dirname(dir)) {
    const manifest = path.join(dir, "package.json");
    // Some packages keep a package.json in a subfolder only to set the module type.
    if (existsSync(manifest) && JSON.parse(readFileSync(manifest, "utf8")).name) return dir;
  }
  return null;
}

/** `@import "tailwindcss";`, `@import "@xyflow/react/dist/base.css";`: the package part. */
const CSS_PACKAGE_IMPORT = /@import\s+["']((?:@[\w.-]+\/)?[\w.-]+)[^"']*["']/g;

/** Where a package named in an import is installed, looking up from the importing file. */
function packageDir(fromDir: string, name: string): string | null {
  for (let dir = fromDir; dir !== path.dirname(dir); dir = path.dirname(dir)) {
    const candidate = path.join(dir, "node_modules", name);
    if (existsSync(path.join(candidate, "package.json"))) return candidate;
  }
  return null;
}

const MIT_PERMISSION = `Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`;

/**
 * A few packages declare MIT in package.json and ship no licence file: their notice is the MIT
 * text with the author the manifest names. Any other licence without a file stops the build,
 * so that it is looked at rather than left out.
 */
function mitFromManifest(manifest: { name?: string; license?: string; author?: unknown }): string {
  if (manifest.license !== "MIT") {
    throw new Error(`pviz:licences: ${manifest.name} ships no licence file and is not MIT`);
  }
  const { author } = manifest;
  const holder =
    typeof author === "string"
      ? author
      : typeof author === "object" && author !== null && "name" in author
        ? String(author.name)
        : `the authors of ${manifest.name}`;
  return `MIT License\n\nCopyright (c) ${holder}\n\n${MIT_PERMISSION}`;
}

/**
 * Puts the licence of every package whose code is in the built page at its top, as a comment.
 * The minifier drops the libraries' own headers, and the page (and every export made from it)
 * is a copy of their code, which their licences ask to carry the notice. React Flow in
 * particular is credited here rather than in a link on every canvas.
 */
function licenceNotices(): Plugin {
  const roots = new Set<string>();
  return {
    name: "pviz:licences",
    apply: "build",
    enforce: "post",
    renderChunk(_code, chunk) {
      for (const [id, info] of Object.entries(chunk.modules)) {
        const dir = info.renderedLength > 0 ? packageRoot(id) : null;
        if (dir) roots.add(dir);
        // A stylesheet's own imports (Tailwind, React Flow's base sheet) are compiled into the
        // page's CSS without becoming modules of their own.
        if (id.endsWith(".css") && existsSync(id)) {
          for (const [, specifier] of readFileSync(id, "utf8").matchAll(CSS_PACKAGE_IMPORT)) {
            const found = specifier && packageDir(path.dirname(id), specifier);
            if (found) roots.add(found);
          }
        }
      }
      return null;
    },
    generateBundle: {
      // After the single-file plugin has inlined everything into the page.
      order: "post",
      handler(_options, bundle) {
        const page = Object.values(bundle).find(
          (item) => item.type === "asset" && item.fileName.endsWith(".html"),
        );
        if (page?.type !== "asset") throw new Error("pviz:licences: no page to annotate");
        const notices = [...roots]
          .map((dir) => {
            const manifest = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
            const file = readdirSync(dir).find((name) => /^licen[cs]e/i.test(name));
            const text = file
              ? readFileSync(path.join(dir, file), "utf8").trim()
              : mitFromManifest(manifest);
            return { name: String(manifest.name), version: String(manifest.version), text };
          })
          // One entry per package, even when two copies of it are bundled.
          .filter((entry, i, all) => all.findIndex((e) => e.name === entry.name) === i)
          .sort((a, b) => a.name.localeCompare(b.name));
        const body = notices
          .map((n) => `${n.name} ${n.version}\n\n${n.text.replaceAll("-->", "-- >")}`)
          .join(`\n\n${"=".repeat(72)}\n\n`);
        const comment = `<!--\n${NOTICES_HEADING}\n\n${body}\n-->\n`;
        const html = String(page.source);
        page.source = html.replace(/^(<!doctype html>\s*)/i, `$1${comment}`);
      },
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
    licenceNotices(),
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
