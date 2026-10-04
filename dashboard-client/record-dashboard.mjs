// Record the fleet dashboard as a video, against the MOCKED stats.
//
// The terminal demo (tools/demo/record-demo.sh) ends on the dashboard, but
// a terminal GIF cannot show a browser: the viewer is looking at a terminal,
// not at a browser window. So the dashboard gets its own recording, and the
// two are cut together.
//
// The client is the real built bundle; only the two API calls it makes are
// intercepted by the mock server. The card data is labelled as mock in the
// client itself, so the video does not claim to be a live fleet.
//
// Usage:
//   node record-dashboard.mjs <output-dir>
//
// Requires the client to be built (node build.mjs).
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(process.argv[2] ?? resolve(here, "..", "docs", "images"));
const port = Number(process.env.PORT ?? 4186);
const base = `http://127.0.0.1:${port}`;

mkdirSync(outDir, { recursive: true });

// The mock server serves the built client and the two API endpoints.
const server = spawn("node", [resolve(here, "mock-server.mjs")], {
  env: { ...process.env, PORT: String(port) },
  stdio: "ignore",
});

const stop = () => {
  if (!server.killed) server.kill();
};
process.on("exit", stop);
process.on("SIGINT", () => {
  stop();
  process.exit(130);
});

// Wait for the server to answer rather than sleeping a fixed amount.
let up = false;
for (let i = 0; i < 60; i++) {
  try {
    const res = await fetch(`${base}/_toolkit/api/stats`);
    if (res.ok) {
      up = true;
      break;
    }
  } catch {
    // not up yet
  }
  await new Promise((r) => setTimeout(r, 250));
}
if (!up) {
  console.error("the mock server never came up");
  stop();
  process.exit(1);
}

const browser = await chromium.launch();
// 1280x720 at 2x: the video is scaled down on embed, and 2x keeps the
// small type in the card grid legible.
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 2,
  recordVideo: { dir: outDir, size: { width: 1280, height: 720 } },
});
const page = await context.newPage();

await page.goto(base, { waitUntil: "domcontentloaded" });
// The client polls every 15s, so "networkidle" never settles. Wait for the
// fleet grid to replace the skeleton instead.
await page.waitForSelector(".cards article", { timeout: 15_000 });
await page.waitForTimeout(1500);

// The billing snapshot is only fetched on demand (the endpoint forces a
// refresh, which is expensive), so the panel is empty until the button is
// pressed. Press it, so the video shows the whole dashboard.
await page.locator(".panel .mini-btn").click();
await page.waitForSelector(".panel .tile .value", { timeout: 15_000 });
await page.waitForTimeout(1200);

// A slow pan across the card grid, so the video is not a still frame.
const grid = page.locator(".cards");
if (await grid.count()) {
  await grid.hover();
  for (let i = 0; i < 6; i++) {
    await page.mouse.wheel(0, 40);
    await page.waitForTimeout(350);
  }
}
await page.waitForTimeout(800);

// The video is written when the page closes.
await page.close();
const video = page.video();
await context.close();
await browser.close();
stop();

const path = await video.path();
console.log("wrote", path);
