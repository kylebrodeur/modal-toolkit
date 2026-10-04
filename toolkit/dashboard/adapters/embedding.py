"""U3: the `embedding` adapter - unauth `/health`, authed `/stats`.

Card headline: collections count + loaded models (from `/stats`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters import Probe, _base, _headers, _timed


class EmbeddingAdapter:
    key = "embedding"
    label = "Embedding"

    def health(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        return _timed("GET", f"{base}/health", timeout=3.0)

    def detail(self, cfg: Mapping[str, Any]) -> Probe:
        base = _base(cfg)
        if not base:
            return Probe(ok=False, error="not configured")
        probe = _timed("GET", f"{base}/stats", headers=_headers(cfg))
        if not probe.ok:
            return probe
        stats = probe.data
        collections = stats.get("collections")
        n_collections = len(collections) if isinstance(collections, list) else None
        probe.data = {
            "collections": n_collections,
            "loaded_models": stats.get("loaded_models"),
            "default_model": stats.get("default_model") or stats.get("default"),
            "gpu": stats.get("gpu"),
        }
        return probe


ADAPTER = EmbeddingAdapter()
