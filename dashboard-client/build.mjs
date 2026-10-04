// Build the fleet client: bundle src/main.ts -> dist/dashboard.js (IIFE, minified).
import { build } from "esbuild";
import { mkdirSync, cpSync } from "node:fs";

mkdirSync("dist", { recursive: true });

await build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  format: "iife",
  target: "es2022",
  minify: true,
  outfile: "dist/dashboard.js",
  sourcemap: false,
  logLevel: "info",
});

cpSync("index.html", "dist/index.html");
console.log("fleet client build complete: dist/dashboard.js + dist/index.html");