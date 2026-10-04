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

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).captured.append((self.path, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass


@pytest.fixture()
def vm_server():
    """Spin a real stdlib HTTP server on a random port; yield (url, captured)."""
    _CaptureHandler.captured = []
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
    assert line == "m,a=x\\,y,b=k\\=v,c=back\\\\slash,d=plain 1 100"


def test_line_protocol_escapes_measurement_spaces() -> None:
    line = metrics.line_protocol("vision gate skip", 2, ts=50)
    assert line == "vision_gate_skip 2 50"


def test_write_passes_explicit_ts_through(vm_server) -> None:
    url, captured = vm_server
    writer = metrics.VMWriter(url=url)
    assert writer.write("embedding_job_done", 1, tags={"pkg": "embedding"}, ts=1699999999) is True
    assert len(captured) == 1
    path, body = captured[0]
    assert body == b"embedding_job_done,pkg=embedding 1 1699999999"
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
    assert head == "vision_gate_skip,gate=fast 3"
    assert before <= int(stamp) <= after


def test_emit_flow_states_writes_one_point_for_each_package(vm_server) -> None:
    url, captured = vm_server
    metrics._writer = metrics.VMWriter(url=url, timeout=1.0)

    metrics.emit_flow_states({"embedding": "ok", "inference": "unreachable"})

    lines = [body.decode() for _path, body in captured]
    assert len(lines) == 2
    assert any("mtk_flow_state,package=embedding,state=ok 1 " in line for line in lines)
    assert any("mtk_flow_state,package=inference,state=unreachable 1 " in line for line in lines)


def test_metric_timer_measures_and_writes(vm_server) -> None:
    url, captured = vm_server
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(metrics.ENV_URL, url)
        with metrics.MetricTimer("inference_request_seconds") as timer:
            time.sleep(0.01)
        assert timer.elapsed > 0
    assert len(captured) == 1
    value = float(captured[0][1].decode().split(" ")[1])
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
    assert captured[0][1].decode().startswith("embedding_job_seconds ")


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
