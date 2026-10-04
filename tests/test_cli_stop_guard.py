"""`modal app stop` is destructive (undeploys the app: every Function/Cls
torn down, in-flight requests dropped, URL dead until redeploy) and gets
confused for scale-to-zero (automatic, reversible, no action needed via
scaledown_window/min_containers). `_confirm_app_stop` is the fail-closed
arm gate in front of every real `modal app stop` invocation in the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli


class TestConfirmAppStopInteractive:
    def test_refuses_when_typed_name_does_not_match(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _prompt="": "wrong-name")
        assert cli._confirm_app_stop("modal-vision-server", force=False) is False

    def test_confirms_when_typed_name_matches(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _prompt="": "modal-vision-server")
        assert cli._confirm_app_stop("modal-vision-server", force=False) is True

    def test_refuses_on_eof_no_confirmation_received(self, monkeypatch):
        def _raise(_prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        assert cli._confirm_app_stop("modal-vision-server", force=False) is False


class TestConfirmAppStopForced:
    def test_force_without_env_signal_still_refuses(self, monkeypatch):
        monkeypatch.delenv("MTK_ALLOW_APP_STOP", raising=False)
        assert cli._confirm_app_stop("modal-vision-server", force=True) is False

    def test_force_with_env_signal_confirms_without_prompting(self, monkeypatch):
        monkeypatch.setenv("MTK_ALLOW_APP_STOP", "1")

        def _should_not_be_called(_prompt: str = "") -> str:
            raise AssertionError("force path must not prompt")

        monkeypatch.setattr("builtins.input", _should_not_be_called)
        assert cli._confirm_app_stop("modal-vision-server", force=True) is True


class TestShutdownVisionGoesThroughTheGate:
    def test_refused_confirmation_skips_the_real_stop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(cli, "_confirm_app_stop", lambda *a, **k: False)
        calls = []
        monkeypatch.setattr(cli, "_modal_run", lambda *a, **k: calls.append(a) or (True, "", ""))
        args = type("Args", (), {"all": False, "pkg": "vision", "force": False})()
        cli.cmd_shutdown(args)
        assert calls == []

    def test_confirmed_runs_app_stop_without_blind_yes_flag(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(cli, "_confirm_app_stop", lambda *a, **k: True)
        calls = []
        monkeypatch.setattr(cli, "_modal_run", lambda *a, **k: calls.append(a) or (True, "", ""))
        args = type("Args", (), {"all": False, "pkg": "vision", "force": False})()
        cli.cmd_shutdown(args)
        assert len(calls) == 1
        argv = calls[0]
        assert "stop" in argv
        assert "-y" not in argv  # no blind auto-confirm; the mtk gate IS the confirmation


class TestDashboardStopGoesThroughTheGate:
    def test_refused_confirmation_skips_the_real_stop(self, monkeypatch):
        monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
        monkeypatch.setattr(cli, "_confirm_app_stop", lambda *a, **k: False)
        calls = []
        monkeypatch.setattr(cli, "_modal_run", lambda *a, **k: calls.append(a) or (True, "", ""))
        args = type("Args", (), {"subcommand": "stop", "force": False})()
        rc = cli.cmd_dashboard(args)
        assert calls == []
        assert rc == 1

    def test_confirmed_runs_app_stop(self, monkeypatch):
        monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
        monkeypatch.setattr(cli, "_confirm_app_stop", lambda *a, **k: True)
        calls = []
        monkeypatch.setattr(cli, "_modal_run", lambda *a, **k: calls.append(a) or (True, "", ""))
        args = type("Args", (), {"subcommand": "stop", "force": False})()
        rc = cli.cmd_dashboard(args)
        assert len(calls) == 1
        assert rc == 0
