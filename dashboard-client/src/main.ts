// Fleet client entrypoint: mount the views + drive the refresh cycle.
// Billing polls SLOW (5-min server cache makes refreshes cheap; refresh on demand).
import { html, reactive, watch } from "@arrow-js/core";
import { store, refresh } from "./lib/store.js";
import { PackageGrid } from "./views/PackageGrid.js";
import { BillingPanel } from "./views/BillingPanel.js";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("Missing #app root");

const status = reactive({ pill: "loading", cls: "warn" });

const View = () =>
  html`<div class="toolbar">
      <div><h1>Modal Toolkit</h1><div class="subtle">Fleet state across the four packages</div></div>
      <div><span class="pill ${status.cls}">${status.pill}</span> <button id="refresh">Refresh</button></div>
    </div>
    ${PackageGrid()}
    ${BillingPanel()}`;

(View as (r: HTMLElement) => unknown)(root);

watch(() => {
  void store.data;
  void store.error;
  status.pill = store.error ? "error" : store.data ? "live" : "loading";
  status.cls = store.error ? "bad" : store.data ? "good" : "warn";
});

document.querySelector<HTMLButtonElement>("#refresh")?.addEventListener("click", () => void refresh());
void refresh();
setInterval(() => void refresh(), 15_000);