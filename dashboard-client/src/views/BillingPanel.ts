// U5: the workspace billing panel - month-to-date, today, credits,
// per-app split, workspace-disabled banner (fleet-wide data).
import { html } from "@arrow-js/core";
import type { ArrowTemplate } from "@arrow-js/core";
import { store, refreshBilling } from "../lib/store.js";

const usd = (v: number | null | undefined): string =>
  v == null ? "—" : `$${v < 1 ? v.toFixed(3) : v.toFixed(2)}`;

export const BillingPanel = (): ArrowTemplate => {
  const b = store.billing;
  const row = (label: string, value: string, mini = "") => html`
    <div class="tile"><small>${label}</small><div class="value">${value}</div>${mini ? html`<p class="mini">${mini}</p>` : null}</div>
  `;
  const tiles = html`<div class="grid">
    ${row("Metered month", usd(b?.metered_month_usd))}
    ${row("Metered today", usd(b?.metered_today_usd))}
    ${row("Workspace billed", usd(b?.workspace_billed_month_usd))}
    ${row("Workspace credits", usd(b?.workspace_credits_month_usd))}
  </div>`;
  const perApp = b?.per_app_month_usd
    ? Object.entries(b.per_app_month_usd)
        .map(([app, v]) => `<div><b>${app}</b><span>${usd(v)}</span></div>`)
        .join("")
    : "";
  return html`
    <section class="panel">
      <h2>Billing
        <button class="mini-btn" onClick=${() => void refreshBilling()} disabled=${store.billingBusy}>
          ${store.billingBusy ? "Refreshing…" : "Refresh"}
        </button>
      </h2>
      ${b?.workspace_disabled
        ? html`<div class="disabled"><b>Workspace disabled</b><p>${String(
            b.error ?? "spend/usage limit reached (raise it in Modal's Usage & Billing settings)",
          )}</p></div>`
        : null}
      ${b?.error && !b.workspace_disabled ? html`<div class="error">${String(b.error)}</div>` : null}
      ${tiles}
      <div class="meta apps">${perApp ? html`<div class="meta">${perApp}</div>` : html`<p class="mini">No metered rows this month.</p>`}</div>
    </section>
  `;
};