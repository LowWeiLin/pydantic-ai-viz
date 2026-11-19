// Checks the single-file build and copies it into the Python package.
// Run by `pnpm build` after `vite build`.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const distDir = path.join(webDir, "dist");
const source = path.join(distDir, "viewer.html");
const targetDir = path.resolve(webDir, "..", "src", "pydantic_ai_viz", "_static");
const target = path.join(targetDir, "viewer.html");

const MAX_BYTES = 1_500_000;
// Assembled at run time so this file never contains the literal slot token itself.
const BOOT_TOKEN = `"__PVIZ_${"BOOT"}__"`;
const BOOT_SLOT = `<script id="pviz-boot" type="application/json">${BOOT_TOKEN}</script>`;

function fail(message) {
  console.error(`copy-viewer: ${message}`);
  process.exit(1);
}

if (!existsSync(source)) fail(`${source} not found. Run "vite build" first.`);

const extra = readdirSync(distDir, { recursive: true }).filter(
  (entry) => entry !== "viewer.html" && statSync(path.join(distDir, String(entry))).isFile(),
);
if (extra.length > 0) fail(`dist must hold only viewer.html, found: ${extra.join(", ")}`);

const html = readFileSync(source, "utf8");
const bytes = Buffer.byteLength(html, "utf8");
if (bytes > MAX_BYTES) fail(`viewer.html is ${bytes} bytes, over the ${MAX_BYTES} byte budget`);

const tokenCount = html.split(BOOT_TOKEN).length - 1;
if (tokenCount !== 1) fail(`expected exactly one boot token, found ${tokenCount}`);
if (!html.includes(BOOT_SLOT))
  fail("boot slot script tag is missing or was rewritten by the build");
if (/<script[^>]+\bsrc=|<link[^>]+rel="stylesheet"/i.test(html)) {
  fail("viewer.html references an external script or stylesheet");
}
// The licences of the bundled libraries open the page (see `licenceNotices` in vite.config.ts).
const notices = /^<!doctype html>\s*<!--\nThird-party software in this file\n([\s\S]*?)\n-->/i.exec(
  html,
);
if (!notices) fail("viewer.html does not open with the third-party licence notices");
for (const name of ["@xyflow/react", "react", "react-dom", "tailwindcss"]) {
  if (!new RegExp(`^${name} \\d`, "m").test(notices[1])) fail(`the notices do not list ${name}`);
}

if (!existsSync(path.join(targetDir, ".gitignore"))) {
  fail(`${targetDir} does not look like the package static directory`);
}
mkdirSync(targetDir, { recursive: true });
copyFileSync(source, target);
console.log(
  `copy-viewer: ${(bytes / 1024).toFixed(1)} kB -> ${path.relative(process.cwd(), target)}`,
);
