// The fleet store + typed API bridge. Endpoints live under /_toolkit/api/....
import { reactive } from "@arrow-js/core";

export type Probe = {
  ok: boolean;
  data: Record<string, unknown>;
  error: string | null;
  took_ms: number;
};

export type PackageState = { health: Probe; detail: Probe };

export type FleetStats = {
  packages: Record<string, PackageState>;
  order: string[];
};

export type BillingSnapshot = {
  metered_month_usd: number | null;
  metered_today_usd: number | null;
  workspace_billed_month_usd: number | null;
  workspace_credits_month_usd: number | null;
  daily_breakdown: { day: string; resources: Record<string, number> }[];
  per_app_month_usd: Record<string, number>;
  hourly_rows: { hour: number; usd: number }[];
  error: string | null;
  workspace_disabled: boolean;
};

export const store = reactive<{
  data: FleetStats | null;
  billing: BillingSnapshot | null;
  error: string;
  billingBusy: boolean;
}>({
  data: null,
  billing: null,
  error: "",
  billingBusy: false,
});

const jsonOrThrow = async (response: Response): Promise<unknown> => {
  if (response.status === 401) {
    window.location.href = "/_toolkit/login";
    throw new Error("session expired");
  }
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json() as Promise<unknown>;
};

export async function refresh(): Promise<void> {
  try {
    const payload = (await jsonOrThrow(await fetch("/_toolkit/api/stats", { credentials: "same-origin" }))) as FleetStats;
    store.data = payload;
    store.error = "";
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message === "session expired") return;
    if (!store.data) store.error = message; // keep last-good data on transient errors
  }
}

export async function refreshBilling(): Promise<void> {
  if (store.billingBusy) return;
  store.billingBusy = true;
  try {
    const payload = (await jsonOrThrow(
      await fetch("/_toolkit/api/billing/refresh", { method: "POST", credentials: "same-origin" }),
    )) as BillingSnapshot;
    store.billing = payload;
  } finally {
    store.billingBusy = false;
  }
}