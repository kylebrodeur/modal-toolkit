"""App-level metrics: a stdlib-only VictoriaMetrics drop-in for the toolkit.

One writer usable from any family package, or from an operator's own
Modal app, to push events, counters, and rates into **your own** VictoriaMetrics
(or InfluxDB; the line protocol is compatible with both).

Single-node VictoriaMetrics accepts PUT or POST with Influx line protocol at:

    {url}/api/v2/write?precision=s&org={org}&bucket={bucket}

``org``/``bucket`` are ignored by single-node VM (kept as Influx placeholders);
a 204 means the point landed. Timestamps are in seconds and mark the **reading**,
not the send: pass ``ts`` explicitly when the event happened earlier. 204 or
not, the writer never retries internally: a failed write returns ``False`` and
the *caller* decides the retry policy.

Naming convention for toolkit points: ``<package>_<event>``:
``embedding_job_done``, ``vision_gate_skip``, ``inference_request_seconds``.

    >>> from toolkit.metrics import write_metric, MetricTimer, bump
    >>> bump("embedding_job_done")                      # stateless counter point
    >>> with MetricTimer("inference_request_seconds"):
    ...     ...  # writes elapsed seconds on exit

Only stdlib is imported at module top so the module works inside Modal
containers without extra image dependencies.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

ENV_URL = "MODAL_TOOLKIT_VM_URL"
ENV_AUTH = "MODAL_TOOLKIT_VM_AUTH"  # optional "user:password" for HTTP Basic Auth
DEFAULT_URL = "http://localhost:8428"

# Env-name prefix override for VENDORED copies that live in another project's
# namespace (e.g. the session coordinator's MODAL_SESSION_VM_URL). Set before
# the first read; both env names derive from it.
_env_prefix_override: str | None = None


def _env_url() -> str:
    prefix = _env_prefix_override or "MODAL_TOOLKIT"
    return f"{prefix}_VM_URL"


def _env_auth() -> str:
    prefix = _env_prefix_override or "MODAL_TOOLKIT"
    return f"{prefix}_VM_AUTH"


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _escape_tag(value: str) -> str:
    """Escape a tag key or value for line protocol: backslash, comma, equals."""
    return value.replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=")


def _escape_measurement(name: str) -> str:
    """Measurement names keep it boring: spaces become underscores."""
    return name.replace(" ", "_")


def line_protocol(measurement: str, value: float, tags: dict[str, str] | None = None, ts: int | None = None) -> str:
    """Build one line: ``measurement,tag=v value=<num> timestamp_seconds``.

    The fieldset MUST be ``key=value`` per Influx line protocol - a bare
    number is invalid and VictoriaMetrics' Influx endpoint rejects the line
    with HTTP 400 ("missing field value"). The field key is always ``value``.
    """
    name = _escape_measurement(str(measurement))
    tag_part = ""
    if tags:
        tag_part = "," + ",".join(f"{_escape_tag(str(k))}={_escape_tag(str(v))}" for k, v in tags.items())
    stamp = int(time.time()) if ts is None else int(ts)
    return f"{name}{tag_part} value={value} {stamp}"


def effective_url() -> tuple[str, str]:
    """The VM URL in effect + where it came from (``env MODAL_TOOLKIT_VM_URL`` or ``default``)."""
    url_env = _env_url()
    env = os.environ.get(url_env)
    if env:
        return env.rstrip("/"), f"env {url_env}"
    return DEFAULT_URL, "default"


class VMWriter:
    """Writes single points to a VictoriaMetrics (or InfluxDB) write endpoint.

    Never raises and never retries: ``write`` returns True on 204, False on
    anything else (with a one-line warning on stderr). A metrics failure must
    never fail the host operation.
    """

    def __init__(
        self,
        url: str | None = None,
        org: str = "-",
        bucket: str = "-",
        timeout: float = 5.0,
        auth: str | None = None,
    ) -> None:
        resolved = (url or os.environ.get(_env_url()) or DEFAULT_URL).rstrip("/")
        self.url = resolved
        self.org = org
        self.bucket = bucket
        self.timeout = timeout
        self._auth = auth if auth is not None else os.environ.get(_env_auth())

    @property
    def endpoint(self) -> str:
        query = urllib.parse.urlencode({"precision": "s", "org": self.org, "bucket": self.bucket})
        return f"{self.url}/api/v2/write?{query}"

    def _headers(self) -> dict[str, str]:
        """Basic auth when an `auth` (or env `MODAL_TOOLKIT_VM_AUTH`) is set; none otherwise."""
        import base64

        if not self._auth:
            return {}
        token = base64.b64encode(self._auth.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def write(
        self,
        measurement: str,
        value: float,
        tags: dict[str, str] | None = None,
        ts: int | None = None,
    ) -> bool:
        line = line_protocol(measurement, value, tags=tags, ts=ts)
        endpoint = self.endpoint
        req = urllib.request.Request(endpoint, data=line.encode("utf-8"), method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 204:
                    return True
                print(f"mtk metrics: write to {self.url} got HTTP {resp.status}", file=sys.stderr)
                return False
        except OSError as exc:
            # URLError subclasses OSError; this covers refused/DNS/timeout alike.
            print(f"mtk metrics: write to {self.url} failed: {exc}", file=sys.stderr)
            return False


_writer: VMWriter | None = None


def configure_env_prefix(prefix: str) -> None:
    """Vendors set this once at import (before any write): their env names
    derive from the prefix instead of MODAL_TOOLKIT_."""
    global _env_prefix_override, _writer
    if _env_prefix_override != prefix:
        _env_prefix_override = prefix
        _writer = None  # the singleton re-reads env under the new prefix


def write_metric(measurement: str, value: float, tags: dict[str, str] | None = None, ts: int | None = None) -> bool:
    """Write one point via a lazily-created module singleton (reads the env once)."""
    global _writer
    if _writer is None:
        _writer = VMWriter()
    return _writer.write(measurement, value, tags=tags, ts=ts)


def bump(measurement: str, tags: dict[str, str] | None = None, n: float = 1) -> bool:
    """Stateless counter point: VM does the summation, the client keeps nothing."""
    return write_metric(measurement, n, tags=tags)


def emit_flow_states(states: dict[str, str]) -> None:
    """Write one ``mtk_flow_state`` point for each package without raising."""
    for package, state in states.items():
        with contextlib.suppress(Exception):
            write_metric("mtk_flow_state", 1, tags={"package": package, "state": state})


class MetricTimer:
    """Time a block (context manager) or a function (decorator), then write elapsed seconds.

    Usage::

        with MetricTimer("inference_request_seconds"):
            ...

        @MetricTimer("embedding_job_seconds")
        def job(): ...
    """

    def __init__(self, key: str, tags: dict[str, str] | None = None) -> None:
        self.key = key
        self.tags = tags
        self.elapsed = 0.0
        self._start = 0.0

    def __enter__(self) -> MetricTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.elapsed = time.perf_counter() - self._start
        write_metric(self.key, self.elapsed, tags=self.tags)

    def __call__(self, fn: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with MetricTimer(self.key, self.tags):
                return fn(*args, **kwargs)

        return wrapper
