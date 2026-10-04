import { html } from "@arrow-js/core";
import type { ArrowTemplate } from "@arrow-js/core";
import { store, refreshBilling } from "../lib/store.js";

const usd = (v: number | null | undefined): string =>
  v == null ? "—" : `$${v < 1 ? v.toFixed(3) : v.toFixed(2)}`;

export const BillingPanel = (): ArrowTemplate => {
  // WHY arrow functions in the slots: arrow-js re-renders an expression slot
  // only when the slot holds a FUNCTION. A bare value is rendered once, so
  // the panel stayed on its empty state after the billing snapshot landed.
  const row = (label: string, value: string, mini = "") => html`
    <div class="tile"><small>${label}</small><div class="value">${value}</div>${mini ? html`<p class="mini">${mini}</p>` : null}</div>
  `;
  return html`
    <section class="panel">
      <h2>Billing
        <button class="mini-btn" @click="${() => void refreshBilling()}" disabled="${() => store.billingBusy}">
          ${() => (store.billingBusy ? "Refreshing…" : "Refresh")}
        </button>
      </h2>
      ${() => {
        const b = store.billing;
        if (!b) return null;
        return html`
          ${b.workspace_disabled
            ? html`<div class="disabled"><b>Workspace disabled</b><p>${String(
                b.error ?? "spend/usage limit reached (raise it in Modal's Usage & Billing settings)",
              )}</p></div>`
            : null}
          ${b.error && !b.workspace_disabled ? html`<div class="error">${String(b.error)}</div>` : null}
          <div class="grid">
            ${row("Metered month", usd(b.metered_month_usd))}
            ${row("Metered today", usd(b.metered_today_usd))}
            ${row("Workspace billed", usd(b.workspace_billed_month_usd))}
            ${row("Workspace credits", usd(b.workspace_credits_month_usd))}
          </div>
          <div class="meta apps">
            ${() => {
              const apps = Object.entries(b.per_app_month_usd ?? {});
              if (apps.length === 0) {
                return html`<p class="mini">No metered rows this month.</p>`;
              }
              // Built as templates, not as an HTML string: a string would
              // render as literal text, because arrow-js escapes text slots.
              return html`<div class="meta">${apps.map(
                ([app, value]) => html`<div><b>${app}</b><span>${usd(value)}</span></div>`
              )}</div>`;
            }}
          </div>
        `;
      }}
    </section>
  `;
};
