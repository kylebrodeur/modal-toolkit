"""U2: the fleet-wide billing snapshot, lifted from the inference
dashboard's `_billing_snapshot` + the cost model's `_archive_billing`.

Adaptations from the verbatim port (behavior-preserving):
- the "ours" filter is parameterized by app-name set (the fleet watches
  every toolkit package app, not just inference);
- the archive path comes from the fleet config Volume (ARCHIVE_DIR),
  default `/usage`;
- metered rows are kept per-app (the split generalizes to per-app rows).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any

USAGE_ROOT = Path("/usage")
ARCHIVE_PATH = USAGE_ROOT / "billing-history.jsonl"

_billing_cache: dict[str, Any] = {
    "ts": 0.0,
    "workspace_disabled": False,
    "metered_month_usd": None,
    "metered_today_usd": None,
    "workspace_billed_month_usd": None,
    "workspace_credits_month_usd": None,
    "daily_breakdown": [],
    "per_app_month_usd": {},
    "hourly_rows": [],
    "error": None,
}


def archive_billing(
    daily_ours: list[Any],
    hourly_ours: list[Any],
    archive_path: Path = ARCHIVE_PATH,
) -> None:
    """Append fetched billing rows to billing-history.jsonl, deduped by key.

    Modal's report API won't span beyond 7 days hourly, so archiving on
    every successful pull accumulates the full history on the Volume.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if archive_path.exists():
        with archive_path.open(encoding="utf-8") as handle:
            for line in handle:
                with contextlib.suppress(json.JSONDecodeError):
                    value = json.loads(line)
                    if isinstance(value, dict):
                        seen.add(f"{value.get('kind')}:{value.get('key')}")

    rows: list[dict[str, Any]] = []
    for item in daily_ours:
        key = str(item.interval_start)
        if f"day:{key}" in seen:
            continue
        rows.append(
            {
                "kind": "day",
                "key": key,
                "archived_at": time.time(),
                "usd": float(item.cost),
                "resources": {k: float(v) for k, v in (item.cost_by_resource or {}).items()},
            }
        )
    for item in hourly_ours:
        key = str(item.interval_start)
        if f"hour:{key}" in seen:
            continue
        rows.append(
            {
                "kind": "hour",
                "key": key,
                "archived_at": time.time(),
                "usd": float(item.cost),
                "resources": {k: float(v) for k, v in (item.cost_by_resource or {}).items()},
            }
        )
    if not rows:
        return
    with archive_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


async def billing_snapshot(
    our_apps: list[str],
    archive_dir: str = "/usage",
    force: bool = False,
) -> dict[str, Any]:
    """Workspace billing + per-app metered split for the fleet cards.

    `our_apps`: the package app names counted as the fleet's own metered
    rows (the dashboard config supplies them). Everything else is the
    ported logic: 5-minute cache, daily rows for the month, hourly rows
    for the 7-day window, per-resource splits, workspace-disabled banner
    detection.
    """
    import datetime as _dt

    app_names = set(our_apps)
    now = time.time()
    if not force and now - float(_billing_cache["ts"] or 0) < 300 and _billing_cache["metered_month_usd"] is not None:
        return dict(_billing_cache)

    def _ours(item: Any) -> bool:
        # Prefer the explicit "project" tag (set on each package App); fall back
        # to an exact description match for apps deployed before tagging.
        return item.tags.get("project") in app_names or ("project" not in item.tags and item.description in app_names)

    try:
        import modal

        workspace = modal.Workspace.from_context()
        month_start = _dt.datetime.now(_dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        today_start = _dt.datetime.now(_dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        daily_items = (
            await workspace.billing.report.aio(
                start=month_start, end=today_start, resolution="d", tag_names=["project"]
            )
            if today_start > month_start
            else []
        )
        hourly_start = max(month_start, _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=7) + _dt.timedelta(hours=1))
        hourly_items = await workspace.billing.report.aio(start=hourly_start, resolution="h", tag_names=["project"])
        summary = await workspace.billing.summary.aio()

        hourly_ours = [item for item in hourly_items if _ours(item)]
        today_usd = sum(float(item.cost) for item in hourly_ours if item.interval_start >= today_start)
        # Fold overlap so each dollar counts once (ported comment): daily rows
        # cover month_start..today_start; hourly rows only the last 7 days.
        hourly_cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=7) + _dt.timedelta(hours=1)
        month_usd = sum(
            float(item.cost) for item in daily_items if _ours(item) and item.interval_start < hourly_cutoff
        ) + sum(float(item.cost) for item in hourly_ours)
        per_app: dict[str, float] = {}
        daily_breakdown: list[dict[str, Any]] = []
        resource_split: dict[str, float] = {}
        for item in list(daily_items) + hourly_ours:
            if not _ours(item):
                continue
            app = str(item.tags.get("project") or item.description or "untagged")
            per_app[app] = per_app.get(app, 0.0) + float(item.cost)
        for item in daily_items:
            if not _ours(item):
                continue
            day_resources: dict[str, float] = {}
            for resource, cost in (item.cost_by_resource or {}).items():
                cost_f = float(cost)
                if cost_f <= 0:
                    continue
                day_resources[resource] = round(cost_f, 4)
                resource_split[resource] = resource_split.get(resource, 0.0) + cost_f
            if day_resources:
                daily_breakdown.append({"day": str(item.interval_start)[:10], "resources": day_resources})
        resource_split = {k: round(v, 2) for k, v in sorted(resource_split.items(), key=lambda kv: -kv[1])}
        hourly_rows = [{"hour": item.interval_start.timestamp(), "usd": float(item.cost)} for item in hourly_ours]
        archive_billing(
            [item for item in daily_items if _ours(item)], hourly_ours, Path(archive_dir) / "billing-history.jsonl"
        )
        _billing_cache.update(
            ts=now,
            metered_month_usd=round(month_usd, 4),
            metered_today_usd=round(today_usd, 4),
            workspace_billed_month_usd=float(summary.billed_cost),
            workspace_credits_month_usd=float(-summary.adjustments.get("Credits", 0)),
            daily_breakdown=daily_breakdown,
            per_app_month_usd={k: round(v, 2) for k, v in sorted(per_app.items(), key=lambda kv: -kv[1])},
            hourly_rows=hourly_rows,
            error=None,
        )
    except Exception as exc:
        message = str(exc)
        if "disabled" in message.lower():
            _billing_cache.update(
                ts=now,
                error=("workspace disabled: spend/usage limit reached (raise it in Modal's Usage & Billing settings)"),
                workspace_disabled=True,
            )
            _billing_cache.pop("metered_month_usd", None)
        else:
            _billing_cache.update(ts=now, error=message[:200])
    return dict(_billing_cache)


def reset_cache() -> None:
    """Test seam."""
    _billing_cache.clear()
    _billing_cache.update(
        ts=0.0,
        workspace_disabled=False,
        metered_month_usd=None,
        metered_today_usd=None,
        workspace_billed_month_usd=None,
        workspace_credits_month_usd=None,
        daily_breakdown=[],
        per_app_month_usd={},
        hourly_rows=[],
        error=None,
    )


def _async_publish_safe(publish: Any) -> None:
    def _publish_safe() -> None:
        with contextlib.suppress(Exception):
            publish()

    threading.Thread(target=_publish_safe, daemon=True).start()
