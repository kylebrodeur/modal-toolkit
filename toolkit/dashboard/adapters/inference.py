"""U4: the `inference` adapter - probe the serving surface per lane.

The inference package serves OpenAI-compatible traffic through a
catch-all proxy; there is no dedicated `/health`. The package's own
convention (Ollama semantics) is `GET /api/tags` -> `{"models": [...]}`;
`GET /v1/models` works too (`{"data": [...]}`). The adapter tries
`/api/tags` first and falls back to `/v1/models`; the card headline is
the served-alias list (the hot set).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters import Probe, _base, _headers, _timed


class InferenceAdapter:
    key = "inference"
    label = "Inference"

    def health(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        tags = _timed("GET", f"{base}/api/tags", timeout=3.0)
        if tags.ok:
            return tags
        models = _timed("GET", f"{base}/v1/models", timeout=3.0)
        if models.ok:
            return Probe(ok=True, data={"via": "v1/models"}, took_ms=tags.took_ms + models.took_ms)
        return tags

    def detail(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        tags = _timed("GET", f"{base}/api/tags", headers=_headers(cfg))
        if tags.ok:
            models = tags.data.get("models") or []
            aliases = [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
            tags.data = {"aliases": aliases, "via": "api/tags"}
            return tags
        models = _timed("GET", f"{base}/v1/models", headers=_headers(cfg))
        if models.ok:
            data = models.data.get("data") or []
            aliases = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            models.data = {"aliases": aliases, "via": "v1/models"}
        return models


ADAPTER = InferenceAdapter()
