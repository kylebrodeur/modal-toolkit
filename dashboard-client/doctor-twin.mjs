// Write the doctor.svg twin: the CLI's `mtk doctor` view, with the same
// fleet state the dashboard screenshot shows.
//
// The twin exists so the README's image set is coherent: doctor.svg is
// what an operator sees in a terminal, dashboard.png is what they see in
// the browser, and both are the same fleet.
//
// The SVG is written by hand rather than rendered, to match the existing
// doctor.svg / cost.svg / flow.svg style exactly: a terminal window with
// monospace lines on the same dark surface.
import { writeFileSync, readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "..", "docs", "images");

// The same fleet the dashboard screenshot renders.
const LINES = [
  "$ mtk doctor",
  "",
  "embedding   ok",
  "inference   ok",
  "           served: gemma-4-31b:latest, qwen3.8-27b:latest",
  "vision      ok",
  "finetune    repo",
];

const WIDTH = 880;
const LINE_HEIGHT = 26;
const FIRST_Y = 110;
const HEIGHT = FIRST_Y + LINES.length * LINE_HEIGHT + 12;

const escape = (value) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const body = LINES.map(
  (line, index) =>
    `<text x="50" y="${FIRST_Y + index * LINE_HEIGHT}" font-family="Menlo,Consolas,monospace" font-size="15" fill="#d0d7de" xml:space="preserve">${escape(line)}</text>`
).join("");

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
<rect width="100%" height="100%" rx="12" fill="#0d1117"/>
<circle cx="34" cy="42" r="7" fill="#ff5f57"/><circle cx="56" cy="42" r="7" fill="#febc2e"/><circle cx="78" cy="42" r="7" fill="#28c840"/>
<text x="100" y="47" font-family="Menlo,Consolas,monospace" font-size="13" fill="#8b949e">mtk doctor</text>
${body}
</svg>
`;

const file = resolve(outDir, "doctor-dashboard.svg");
writeFileSync(file, svg);
console.log("wrote", file);

// The existing doctor.svg keeps its place in the set; the twin is the
// dashboard-side companion named doctor-dashboard.svg.
const existing = readFileSync(resolve(outDir, "doctor.svg"), "utf8");
console.log("doctor.svg is", existing.length, "bytes; the twin is", svg.length);
