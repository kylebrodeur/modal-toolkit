"""U1+U5: the fleet ASGI app factory - session auth + package stats.

`build_fleet_api(package_cfg, adapters)` mounts:

- `/_toolkit/login|logout` (Session auth)
- `GET /_toolkit/api/stats`: `{packages: {key: {health, detail}},
  fleet: {pkg order}}` - every adapter probed live (unauth health +
  authed detail when a token exists)
- `POST /_toolkit/api/billing/refresh`: force-refresh the billing
  snapshot (session or bearer)

The client is a static Arrow.js bundle served at `/_toolkit` (U5), so
the app root also serves `index.html` when `client_dir` is set.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse

from .adapters import Probe
from .auth import Session, mount_auth
from .billing import billing_snapshot


def build_fleet_api(
    package_cfg: Mapping[str, Mapping[str, Any]],
    adapters: Mapping[str, Any],
    client_dir: Path | None = None,
) -> FastAPI:
    """Construct the fleet dashboard ASGI app.

    package_cfg: the dashboard config's per-package section
      ({base_url, token, enabled}); adapters: {key: adapter}.
    """
    from contextlib import asynccontextmanager

    session = Session(token=_token_from_cfg())

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = FastAPI(title="Modal Toolkit Fleet Dashboard", lifespan=lifespan)
    mount_auth(app, session)

    def _enabled(key: str) -> bool:
        section = package_cfg.get(key, {})
        return bool(section.get("enabled", True)) and bool(str(section.get("base_url", "")) or key == "finetune")

    @app.get("/_toolkit/api/stats")
    async def stats(request: Request):
        if not session.authorized(request):
            return Response(status_code=401, content="unauthorized")
        packages: dict[str, Any] = {}
        for key, adapter in adapters.items():
            if key != "finetune" and not _enabled(key):
                continue
            cfg = dict(package_cfg.get(key, {}))
            health = await _probe(adapter.health, cfg)
            detail = (
                await _probe(adapter.detail, cfg, headers_token=str(cfg.get("token", "") or ""))
                if health.ok
                else Probe(ok=False, error="health failed")
            )
            packages[key] = {
                "health": _probe_dict(health),
                "detail": _probe_dict(detail),
            }
        return {"packages": packages, "order": list(adapters)}

    @app.post("/_toolkit/api/billing/refresh")
    async def billing_refresh(request: Request):
        if not session.authorized(request):
            return Response(status_code=401, content="unauthorized")
        our_apps = [
            str(cfg.get("app_name")) for cfg in package_cfg.values() if isinstance(cfg, Mapping) and cfg.get("app_name")
        ]
        snapshot = await billing_snapshot(our_apps=our_apps, force=True)
        return snapshot

    @app.get("/_toolkit")
    async def index():
        if client_dir and (client_dir / "index.html").exists():
            return FileResponse(client_dir / "index.html")
        return Response(status_code=404, content="dashboard client not bundled")

    @app.get("/_toolkit/assets/{name}")
    async def asset(name: str):
        if client_dir:
            asset_path = client_dir / "assets" / name
            if asset_path.exists():
                return FileResponse(asset_path)
        return Response(status_code=404)

    async def _probe(fn: Any, cfg: Mapping[str, Any], *, headers_token: str = "") -> Probe:
        # Adapters are synchronous httpx calls; run them inline (they are
        # sub-second probes; the dashboard polls on the client, not here).
        try:
            return fn(cfg)
        except Exception as exc:
            return Probe.from_exception(exc, 0)

    def _probe_dict(probe: Probe) -> dict[str, Any]:
        return {"ok": probe.ok, "data": probe.data, "error": probe.error, "took_ms": probe.took_ms}

    return app


def _token_from_cfg() -> str:
    import os

    return os.getenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "").strip()
