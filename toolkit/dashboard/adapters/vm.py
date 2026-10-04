"""The `vm` adapter - Basic-Auth'd `/health` against the metrics store.

modal-victoriametrics-server serves NO unauthenticated endpoint (the
binary's own `-httpAuth.*` is the only auth layer), so every probe carries
the auth pair: `token` in a dashboard config entry holds `user:password`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters import Probe, _base, _headers, _timed


class VMAdapter:
    key = "vm"
    label = "Metrics (VM)"

    def health(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        return _timed("GET", f"{base}/health", headers=_headers(cfg), timeout=3.0)

    def detail(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        health = _timed("GET", f"{base}/health", headers=_headers(cfg))
        if not health.ok:
            return health
        # VM's /health is a bare 200 (the binary's own); report liveness+
        # auth-works rather than a payload it doesn't serve.
        health.data = {"auth": True, "engine": "victoriametrics"}
        return health


ADAPTER = VMAdapter()
