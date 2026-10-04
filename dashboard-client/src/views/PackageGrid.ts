// U5: the fleet card grid. One card per package: health pill + the ONE
// authed read's headline. Unconfigured packages render greyed, not erroring.
import { html } from "@arrow-js/core";
import type { ArrowTemplate } from "@arrow-js/core";
import { store } from "../lib/store.js";
import type { PackageState } from "../lib/store.js";

const LABELS: Record<string, string> = {
  embedding: "Embedding",
  inference: "Inference",
  vision: "Vision",
  finetune: "Finetune",
};

// Per-package card headline off the authed read (`detail.data`).
function headline(pkg: string, state: PackageState): ArrowTemplate {
  const detail = state.detail;
  if (!detail.ok) {
    return html`<div class="value">&mdash;</div><p class="mini">${detail.error ?? "no read"}</p>`;
  }
  const d = detail.data;
  if (pkg === "embedding") {
    const cols = d.collections;
    const models = Array.isArray(d.loaded_models) ? d.loaded_models.join(", ") : String(d.default_model ?? "");
    const colsLabel = cols != null ? `${String(cols)} collections` : "-";
    const gpuLabel = d.gpu ? `on ${String(d.gpu)}` : "";
    return html`<div class="value">${colsLabel}</div><p class="mini">${models} ${gpuLabel}</p>`;
  }
  if (pkg === "inference") {
    const aliases = Array.isArray(d.aliases) ? (d.aliases as string[]) : [];
    const countLabel = aliases.length ? `${String(aliases.length)} ${aliases.length === 1 ? "model" : "models"}` : "-";
    const head = aliases.slice(0, 4).join(", ") + (aliases.length > 4 ? " ..." : "");
    return html`<div class="value">${countLabel}</div><p class="mini">${head} <span class="subtle">(${String(d.via ?? "")})</span></p>`;
  }
  if (pkg === "vision") {
    const modelName = String(d.main_model ?? "-");
    const seg = String(d.segmenter ?? "none");
    return html`<div class="value">${modelName}</div><p class="mini">segmenter ${seg} / gate ${String(d.fast_gate ?? "none")}</p>`;
  }
  // finetune: job package
  return html`<div class="value">job package</div><p class="mini">no server; train / eval / gguf run on demand</p>`;
}

const Card = (pkg: string, state: PackageState | undefined): ArrowTemplate => {
  if (!state) return html``;
  const up = state.health.ok;
  const took = state.health.took_ms ? ` · ${state.health.took_ms}ms` : "";
  return html`
    <article class="card ${up ? "" : "card-down"}">
      <header><h3>${LABELS[pkg] ?? pkg}</h3><span class="pill ${up ? "good" : "bad"}">${up ? "up" : "down"}${took}</span></header>
      ${headline(pkg, state)}
      <p class="mini subtle">${
        state.detail.ok && state.detail.took_ms ? `detail read ${state.detail.took_ms}ms` : ""
      }</p>
    </article>
  `;
};

export const PackageGrid = (): ArrowTemplate => {
  const data = store.data;
  if (!data) return html`<div class="skel">Loading fleet…</div>`;
  const cards = data.order.filter((k) => k in data.packages).map((k) => Card(k, data.packages[k]));
  return html`<section><h2>Packages</h2><div class="grid cards">${cards}</div></section>`;
};