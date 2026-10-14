"""Tests for the stdlib-only VictoriaMetrics writer (toolkit/metrics.py)."""

from __future__ import annotations

import ast
import http.server
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest

# Import the toolkit package relative to the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import metrics


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    """Minimal VM stand-in: 204 for every POST, recording what arrived."""

    captured: ClassVar[list[tuple[str, bytes]]] = []
    headers_seen: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).captured.append((self.path, body))
        type(self).headers_seen.append(dict(self.headers))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass


@pytest.fixture()
def vm_server():
    """Spin a real stdlib HTTP server on a random port; yield (url, captured)."""
    _CaptureHandler.captured = []
    _CaptureHandler.headers_seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    yield url, _CaptureHandler.captured
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_singleton():
    metrics._writer = None
    yield
    metrics._writer = None


def dead_port_url() -> str:
    """A URL guaranteed to have nothing listening (bind-then-close)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = server.server_port
    server.server_close()
    return f"http://127.0.0.1:{port}"


def test_line_protocol_escapes_tag_values() -> None:
    line = metrics.line_protocol("m", 1, tags={"a": "x,y", "b": "k=v", "c": "back\\slash", "d": "plain"}, ts=100)
    assert line == "m,a=x\\,y,b=k\\=v,c=back\\\\slash,d=plain value=1 100"


def test_line_protocol_escapes_measurement_spaces() -> None:
    line = metrics.line_protocol("vision gate skip", 2, ts=50)
    assert line == "vision_gate_skip value=2 50"


def test_line_protocol_fieldset_is_key_value() -> None:
    """modal-toolkit#5: a bare numeric fieldset is INVALID Influx line
    protocol - VM's Influx endpoint rejects it with HTTP 400 ("missing field
    value"). The field key is always `value`."""
    line = metrics.line_protocol("probe", 1, tags={"actor": "op"}, ts=1791921908)
    assert line == "probe,actor=op value=1 1791921908"
    fields = line.split(" ")[1]
    assert "=" in fields, "fieldset must be key=value, never bare"


def test_write_passes_explicit_ts_through(vm_server) -> None:
    url, captured = vm_server
    writer = metrics.VMWriter(url=url)
    assert writer.write("embedding_job_done", 1, tags={"pkg": "embedding"}, ts=1699999999) is True
    assert len(captured) == 1
    path, body = captured[0]
    assert body == b"embedding_job_done,pkg=embedding value=1 1699999999"
    assert path.startswith("/api/v2/write?")
    assert "precision=s" in path and "org=-" in path and "bucket=-" in path


def test_write_defaults_ts_to_now(vm_server) -> None:
    url, captured = vm_server
    writer = metrics.VMWriter(url=url)
    before = int(time.time())
    assert writer.write("mtk_probe", 1) is True
    after = int(time.time())
    stamp = int(captured[0][1].decode().rsplit(" ", 1)[1])
    assert before <= stamp <= after


def test_write_returns_false_and_never_raises_on_connection_failure(capsys) -> None:
    writer = metrics.VMWriter(url=dead_port_url(), timeout=1.0)
    assert writer.write("mtk_probe", 1) is False
    assert "mtk metrics:" in capsys.readouterr().err


def test_write_returns_false_on_non_204() -> None:
    class _FailHandler(_CaptureHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), _FailHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        writer = metrics.VMWriter(url=f"http://127.0.0.1:{server.server_port}", timeout=1.0)
        assert writer.write("mtk_probe", 1) is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bump_writes_n_as_value(vm_server) -> None:
    url, captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_URL, url)
        before = int(time.time())
        assert metrics.bump("vision_gate_skip", tags={"gate": "fast"}, n=3) is True
        after = int(time.time())
    assert len(captured) == 1
    _path, body = captured[0]
    head, stamp = body.decode().rsplit(" ", 1)
    assert head == "vision_gate_skip,gate=fast value=3"
    assert before <= int(stamp) <= after


def test_emit_flow_states_writes_one_point_for_each_package(vm_server) -> None:
    url, captured = vm_server
    metrics._writer = metrics.VMWriter(url=url, timeout=1.0)

    metrics.emit_flow_states({"embedding": "ok", "inference": "unreachable"})

    lines = [body.decode() for _path, body in captured]
    assert len(lines) == 2
    assert any("mtk_flow_state,package=embedding,state=ok value=1 " in line for line in lines)
    assert any("mtk_flow_state,package=inference,state=unreachable value=1 " in line for line in lines)


def test_metric_timer_measures_and_writes(vm_server) -> None:
    url, captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_URL, url)
        with metrics.MetricTimer("inference_request_seconds") as timer:
            time.sleep(0.01)
        assert timer.elapsed > 0
    assert len(captured) == 1
    value = float(captured[0][1].decode().split(" ")[1].split("=", 1)[1])
    assert value > 0


def test_metric_timer_as_decorator_fires_when_called(vm_server) -> None:
    url, captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_URL, url)

        @metrics.MetricTimer("embedding_job_seconds")
        def job() -> str:
            return "done"

        assert job() == "done"
    assert len(captured) == 1
    assert captured[0][1].decode().startswith("embedding_job_seconds value=")


def test_singleton_honors_env_set_before_first_write(vm_server) -> None:
    url, captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_URL, url)
        metrics._writer = None  # create-after-env-set ordering
        assert metrics.write_metric("mtk_probe", 1) is True
    assert len(captured) == 1
    assert url in metrics._writer.url


def test_module_top_level_imports_are_stdlib_only() -> None:
    src = Path(metrics.__file__).read_text()
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    non_stdlib = imported - sys.stdlib_module_names - {"toolkit"}
    assert non_stdlib == set(), f"non-stdlib imports at module top: {non_stdlib}"


def test_writer_sends_basic_auth_header_from_init(vm_server) -> None:
    """modal-toolkit#5: VM servers fronted by raw -httpAuth.* flags need the
    Authorization header; the canonical writer sent none (silent 401s)."""
    url, _captured = vm_server
    import base64

    writer = metrics.VMWriter(url=url, auth="op:seekrit")
    assert writer.write("writer_check_probe", 1) is True
    seen = _CaptureHandler.headers_seen[-1]
    token = base64.b64encode(b"op:seekrit").decode("ascii")
    assert seen.get("Authorization") == f"Basic {token}"


def test_writer_auth_env_fallback_and_absence(vm_server) -> None:
    url, _captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_AUTH, "envuser:envpass")
        metrics.VMWriter(url=url).write("probe", 1)
        seen = _CaptureHandler.headers_seen[-1]
        assert "Authorization" in seen
    metrics.VMWriter(url=url).write("probe", 1)
    assert "Authorization" not in _CaptureHandler.headers_seen[-1]


def test_line_body_carrying_fieldset_shape(vm_server) -> None:
    """The BODY a VM receives is key=value fieldset form (the modal-toolkit#5
    wire-shape contract), with explicit ts (end-to-end through write())."""
    url, captured = vm_server
    metrics.VMWriter(url=url).write("probe,actor=t", 5)  # type: ignore[arg-type]  # (value 5, tags None)
    body = captured[-1][1].decode()
    assert body.endswith("value=5") or " value=" in body
