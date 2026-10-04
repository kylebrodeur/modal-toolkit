"""The fleet dashboard's per-package adapter contract.

One adapter per package answers two questions over plain HTTP:

- is the package up (`health`, unauthenticated)
- what is the ONE authed read worth showing on the card (`detail`)

No writes from the fleet dashboard (single-writer per package); tokens
never render. `asyncio`-free: adapters are called from FastAPI handlers.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

UNAUTH_TIMEOUT = 3.0
AUTH_TIMEOUT = 5.0


@dataclass(frozen=True)
class Probe:
    """One adapter probe result; `error` set means the read failed."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    took_ms: int = 0

    @classmethod
    def from_exception(cls, exc: Exception, took_ms: int) -> Probe:
        return cls(ok=False, error=str(exc)[:200], took_ms=took_ms)


class PackageAdapter(Protocol):
    """What every package adapter provides. `key` is the config key."""

    key: str
    label: str

    def health(self, cfg: Mapping[str, Any]) -> Probe: ...

    def detail(self, cfg: Mapping[str, Any]) -> Probe: ...


def _base(cfg: Mapping[str, Any]) -> str:
    return str(cfg.get("base_url", "")).rstrip("/")


def _headers(cfg: Mapping[str, Any]) -> dict[str, str]:
    token = str(cfg.get("token", "") or "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _timed(method: str, url: str, **kwargs: Any) -> Probe:
    started = time.monotonic()
    try:
        with httpx.Client(timeout=kwargs.pop("timeout", AUTH_TIMEOUT)) as client:
            response = client.request(method, url, **kwargs)
            took = int((time.monotonic() - started) * 1000)
            response.raise_for_status()
            payload = response.json() if response.content else {}
            return Probe(ok=True, data=payload if isinstance(payload, dict) else {}, took_ms=took)
    except Exception as exc:
        return Probe.from_exception(exc, int((time.monotonic() - started) * 1000))
