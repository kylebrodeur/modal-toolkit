// Render the fleet dashboard against MOCKED stats, for a documentation
// screenshot. No live fleet, no deploy, no account.
//
// The client is the real built bundle: this only intercepts the two API
// calls it makes, so the screenshot shows the real dashboard.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const clientDist = resolve(here, "dist");

// The numbers below are the shape the client expects, with plausible
// values. They are labeled as mock in the screenshot's own caption.
const STATS = {
  packages: {
    embedding: {
      health: { ok: true, data: {}, error: null, took_ms: 42 },
      detail: {
        ok: true,
        data: {
          collections: 12,
          loaded_models: ["embeddinggemma-300m", "nomic-embed-text-v2"],
          default_model: "embeddinggemma-300m",
          gpu: "L4",
        },
        error: null,
        took_ms: 118,
      },
    },
    inference: {
      health: { ok: true, data: {}, error: null, took_ms: 31 },
      detail: {
        ok: true,
        data: {
          aliases: ["gemma-4-31b:latest", "qwen3.8-27b:latest", "llama-3.3-70b:latest", "qwen3-32b:latest", "phi-4:latest"],
          via: "llama.cpp",
        },
        error: null,
        took_ms: 96,
      },
    },
    vision: {
      health: { ok: true, data: {}, error: null, took_ms: 38 },
      detail: {
        ok: true,
        data: { main_model: "BioCLIP-ViT-B-16", segmenter: "sam2-hiera-tiny", fast_gate: "clip-vit-b32" },
        error: null,
        took_ms: 204,
      },
    },
    finetune: {
      health: { ok: true, data: {}, error: null, took_ms: 12 },
      detail: { ok: true, data: {}, error: null, took_ms: 12 },
    },
  },
  order: ["embedding", "inference", "vision", "finetune"],
};

const BILLING = {
  metered_month_usd: 41.83,
  metered_today_usd: 1.27,
  workspace_billed_month_usd: 62.4,
  workspace_credits_month_usd: 50.0,
  daily_breakdown: [],
  // The app names are the family's own, so a viewer reads this as the real
  // fleet's billing shape rather than a placeholder. The numbers are
  // illustrative, which the caption says.
  per_app_month_usd: {
    "modal-embedding-server": 18.42,
    "modal-inference-server": 15.11,
    "modal-vision-server": 6.9,
    "modal-finetune-server": 1.4,
  },
  hourly_rows: [],
  error: null,
  workspace_disabled: false,
};

const send = (res, status, type, body) => {
  res.writeHead(status, { "content-type": type });
  res.end(body);
};

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (url.pathname === "/_toolkit/api/stats") {
    return send(res, 200, "application/json", JSON.stringify(STATS));
  }
  if (url.pathname === "/_toolkit/api/billing/refresh") {
    return send(res, 200, "application/json", JSON.stringify(BILLING));
  }
  if (url.pathname === "/_toolkit/assets/dashboard.js") {
    return send(res, 200, "application/javascript; charset=utf-8", readFileSync(resolve(clientDist, "dashboard.js")));
  }
  return send(res, 200, "text/html", mockIndexHtml());
});

/**
 * The served page, with a banner that says the numbers are mocked.
 *
 * The client's own "live" pill means "the API answered," which is true
 * here but reads as "this is a live fleet" to anyone who has not read the
 * source. A demo that overstates what it shows is worse than no demo, so
 * the mock says so on the page itself.
 */
function mockIndexHtml() {
  const html = readFileSync(resolve(clientDist, "index.html"), "utf8");
  const banner = `<div style="max-width:1280px;margin:1rem auto 0;padding:.6rem .8rem;border:1px solid #7a5c26;border-radius:8px;background:#241d10;color:#f7c873;font:13px system-ui,sans-serif">
    Demo data: this is the real dashboard client rendered against mocked fleet stats, not a live fleet.
  </div>`;
  // The banner goes ABOVE the app: the client's own header is the first
  // thing a viewer reads, so a caveat that sits below the fold is a
  // caveat that gets missed.
  return html.replace('<div id="app"></div>', `${banner}<div id="app"></div>`);
}

const port = Number(process.env.PORT ?? 4180);
server.listen(port, "127.0.0.1", () => {
  console.log(`mocked fleet dashboard on http://127.0.0.1:${port}`);
});
