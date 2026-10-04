// Capture the fleet dashboard screenshot from the mocked client.
//
// The output is the PNG that goes into the toolkit README, plus the SVG
// twin of doctor.svg. Both are rendered from the same mocked state, so the
// pair shows one fleet rather than two.
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// The output path is absolute, so the script writes to the toolkit's docs
// no matter which directory it is run from. The client lives at
// <repo>/dashboard-client, so the images are one level up.
const outDir = resolve(here, "..", "docs", "images");
const port = process.env.PORT ?? "4180";
const base = `http://127.0.0.1:${port}`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 700 }, deviceScaleFactor: 2 });
// The client polls every 15s, so "networkidle" never settles. Wait for the
// fleet grid to replace the skeleton instead.
await page.goto(base, { waitUntil: "domcontentloaded" });

// The client polls; wait for the fleet grid to replace the skeleton.
await page.waitForSelector(".cards article", { timeout: 15_000 });
await page.waitForTimeout(1200);

// The billing snapshot is only fetched on demand (the endpoint forces a
// refresh, which is expensive), so the panel is empty until the button is
// pressed. Press it, so the screenshot shows the whole dashboard.
await page.locator(".panel .mini-btn").click();
await page.waitForSelector(".panel .tile .value", { timeout: 15_000 });
await page.waitForTimeout(600);

const png = resolve(outDir, "dashboard.png");
// The screenshot is of the content, not the page: a full-page capture on a
// tall viewport carries a band of empty background under the panel.
//
// It must include the mock banner though. The banner is the disclaimer that
// the numbers are illustrative, and the client's own "live" pill means only
// "the API answered" - to a reader who has not seen the source, a "live"
// pill plus plausible numbers reads as a live fleet. Screenshotting #app
// alone cropped the banner out, so the published image carried the "live"
// pill with nothing saying the data was mocked. That is the exact
// overstatement the banner exists to prevent.
//
// The banner is a sibling of #app (the mock server injects it above the
// app div), so it is captured by clipping the page to the union of the two
// elements rather than screenshotting one of them.
const banner = page.locator("text=Demo data: this is the real dashboard client");
const hasBanner = (await banner.count()) > 0;
if (!hasBanner) {
  throw new Error(
    "the mock banner is missing from the served page; the screenshot would " +
      "show a 'live' pill with no disclaimer. Is the mock server the current " +
      "mock-server.mjs? (an older build injects nothing above #app)"
  );
}
const bannerBox = await banner.boundingBox();
const appBox = await page.locator("#app").boundingBox();
await page.screenshot({
  path: png,
  clip: {
    x: Math.min(bannerBox.x, appBox.x),
    y: bannerBox.y,
    width: Math.max(bannerBox.x + bannerBox.width, appBox.x + appBox.width) - Math.min(bannerBox.x, appBox.x),
    height: appBox.y + appBox.height - bannerBox.y,
  },
});
console.log("wrote", png);

const state = await page.evaluate(() => ({
  cards: Array.from(document.querySelectorAll(".cards article")).map((card) => ({
    name: card.querySelector("h3")?.textContent,
    pill: card.querySelector(".pill")?.textContent,
    value: card.querySelector(".value")?.textContent,
  })),
  tiles: Array.from(document.querySelectorAll(".panel .tile")).map((tile) => ({
    label: tile.querySelector("small")?.textContent,
    value: tile.querySelector(".value")?.textContent,
  })),
}));
console.log(JSON.stringify(state, null, 2));

await browser.close();
