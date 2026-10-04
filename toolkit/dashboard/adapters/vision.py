"""U4: the `vision` adapter - unauth `/health` with model/segmenter fields.

The identify surface is POST-only and rate-limited; the card shows the
health payload's model + adapter fields, never probing identify.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters import Probe, _base, _headers, _timed


class VisionAdapter:
    key = "vision"
    label = "Vision"

    def health(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        return _timed("GET", f"{base}/health", timeout=3.0)

    def detail(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        health = _timed("GET", f"{base}/health", headers=_headers(cfg))
        if not health.ok:
            return health
        data = health.data
        health.data = {
            "main_model": data.get("main_model") or data.get("model"),
            "segmenter": data.get("segmenter"),
            "fast_gate": data.get("fast_gate"),
            "version": data.get("version"),
        }
        return health


ADAPTER = VisionAdapter()
