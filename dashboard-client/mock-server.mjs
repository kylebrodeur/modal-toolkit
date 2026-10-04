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
  per_app_month_usd: {
    "acme-embedding": 18.42,
    "acme-inference": 15.11,
    "acme-vision": 6.9,
    "acme-finetune": 1.4,
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
  return send(res, 200, "text/html", readFileSync(resolve(clientDist, "index.html")));
});

const port = Number(process.env.PORT ?? 4180);
server.listen(port, "127.0.0.1", () => {
  console.log(`mocked fleet dashboard on http://127.0.0.1:${port}`);
});
