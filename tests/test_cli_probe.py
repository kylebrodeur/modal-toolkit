"""Tests for the CLI health probe's cold-lane handling and the vault container picker.

Both fixes come from operator-reported bugs: the probe misreported a healthy
scale-to-zero lane as unreachable, and the picker selected the first `ta-` row
regardless of app (wrong-app exec) with an empty-string fallthrough.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli


def _configure_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pkg: str) -> None:
    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
    from toolkit import config

    config.write({pkg: {"base_url": "https://lane.example.com", "token": "t"}})


class _Result:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class TestProbeColdLane:
    def test_warm_timeout_then_cold_success_reports_ok(self, monkeypatch, tmp_path):
        _configure_lane(monkeypatch, tmp_path, "embedding")
        calls: list[float] = []

        def fake_get(_url, **kw):
            calls.append(kw.get("timeout"))
            if len(calls) == 1:
                raise httpx.ReadTimeout("warm budget exceeded")
            return httpx.Response(200, json={"status": "ok"})

        monkeypatch.setattr(cli.httpx, "get", fake_get)
        out = cli._probe("embedding")
        assert out["status"] == "ok"
        assert out["cold"] is True
        # The retry used the longer cold budget, not the warm one.
        assert calls == [cli.TIMEOUT_S, cli.COLD_TIMEOUT_S]

    def test_both_timeouts_report_cold_not_unreachable(self, monkeypatch, tmp_path):
        _configure_lane(monkeypatch, tmp_path, "vision")

        def fake_get(_url, **_kw):
            raise httpx.ReadTimeout("still cold")

        monkeypatch.setattr(cli.httpx, "get", fake_get)
        out = cli._probe("vision")
        # Cold is a distinct state from unreachable: the app is deployed but asleep.
        assert out["status"] == "cold"
        assert out["status"] != "unreachable"

    def test_connection_error_stays_unreachable(self, monkeypatch, tmp_path):
        _configure_lane(monkeypatch, tmp_path, "embedding")

        def fake_get(_url, **_kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(cli.httpx, "get", fake_get)
        out = cli._probe("embedding")
        assert out["status"] == "unreachable"


class TestReachableMark:
    """`_reachable_mark` must probe the SAME path the full probe does."""

    def _configure(self, monkeypatch, tmp_path, pkg):
        monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
        from toolkit import config

        config.write({pkg: {"base_url": "https://lane.example.com", "token": "t"}})

    def test_inference_probes_v1_models_not_health(self, monkeypatch, tmp_path):
        self._configure(monkeypatch, tmp_path, "inference")
        seen: list[str] = []

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(url, timeout=None):
            seen.append(url)
            return _Resp()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        mark = cli._reachable_mark("inference")
        assert mark == "up"
        assert seen and seen[0].endswith("/v1/models")

    def test_embedding_probes_health(self, monkeypatch, tmp_path):
        self._configure(monkeypatch, tmp_path, "embedding")
        seen: list[str] = []

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: (seen.append(url), _Resp())[1])
        assert cli._reachable_mark("embedding") == "up"
        assert seen[0].endswith("/health")


class TestVaultContainerPicker:
    def _listing(self, *rows: tuple[str, str, str]) -> str:
        body = "\n".join(f"│ {cid} │ {app_id} │ {app} │ 0s │" for cid, app_id, app in rows)
        return f"header\n{body}\nfooter\n"

    def _patch(self, monkeypatch, listing: str) -> None:
        monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Result(listing))
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
        monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    def test_picks_row_matching_app_name_not_first_row(self, monkeypatch):
        other = ("ta-othercontainerid", "ap-other", "some-other-app")
        ours = ("ta-vaultcontainerid", "ap-vault", "modal-vault-server")
        self._patch(monkeypatch, self._listing(other, ours))
        cid = cli._vault_container_id("modal-vault-server", "https://vault.example.com")
        assert cid == "ta-vaultcontainerid"

    def test_empty_cid_rows_are_skipped(self, monkeypatch):
        row = ("", "ap-empty", "modal-vault-server")
        good = ("ta-realcontainer", "ap-vault", "modal-vault-server")
        self._patch(monkeypatch, self._listing(row, good))
        cid = cli._vault_container_id("modal-vault-server", "https://vault.example.com")
        assert cid == "ta-realcontainer"

    def test_no_matching_row_raises(self, monkeypatch):
        self._patch(monkeypatch, self._listing(("ta-x", "ap-x", "unrelated")))
        with pytest.raises(SystemExit, match="no live container"):
            cli._vault_container_id("modal-vault-server", "https://vault.example.com")
