"""cmd_deploy vendor behavior: refusal without the flag, override recorded only on success.

Account and subprocess calls are injected; nothing here touches Modal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli
from toolkit import project as mtk_project


def _git(cwd: Path, *argv: str) -> None:
    import subprocess

    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def project_with_vendor(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    mtk_project.init_project(root)
    monkeypatch.chdir(root)  # cmd_deploy uses cwd
    # a tiny upstream at v1.0.0, vendored
    origin = tmp_path / "origin-modal-vault-server"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "server").mkdir()
    (origin / "server" / "secrets.toml").write_text("[vault]\nname = 'modal-vault-secret'\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "one")
    _git(origin, "tag", "v1.0.0")
    mtk_project.vendor_packages(root, ["vault"], tags={"vault": "v1.0.0"}, url_for=lambda _p: str(origin))
    # overlay (no secrets gate pass possible in tests, so the gate gets monkeypatched below)
    overlay = mtk_project.overlay_dir(root, "vault")
    overlay.mkdir(parents=True)
    (overlay / "deploy.json").write_text(json.dumps({"app_name": "lab-vault"}) + "\n")
    (overlay / "deploy.sh").write_text("#!/bin/sh\necho would-run\n")
    return root, mtk_project.vendor_dir(root, "vault"), overlay


class _OkStatus:
    manifest_found = True
    missing = frozenset()


def _run_deploy(monkeypatch, project_root, *extra, run_calls, rc=0):
    monkeypatch.setattr(mtk_project, "check_prerequisites", lambda: None)
    monkeypatch.setattr(cli.mtk_secrets, "check_all", lambda pkgs: {"vault": _OkStatus()})
    monkeypatch.setattr(mtk_project, "missing_secrets", lambda s, p: [])
    monkeypatch.setattr(cli, "ensure_secrets_gate_ready", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mtk_project, "ensure_secrets", lambda missing, confirm, create_all: None)

    real_run = cli.subprocess.run

    def fake_run(argv, **kwargs):
        if argv[0] != "sh":  # git internals (vendor_check) stay real
            return real_run(argv, **kwargs)
        run_calls.append(list(argv))
        import types

        return types.SimpleNamespace(returncode=rc)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    args = cli.build_parser().parse_args(["deploy", "vault", *extra])
    return cli.cmd_deploy(args)


def test_drifted_vendor_refuses_without_the_flag(project_with_vendor, monkeypatch):
    root, vendor, _overlay = project_with_vendor
    (vendor / "server" / "drift.txt").write_text("x\n")
    run_calls: list = []
    with pytest.raises(mtk_project.ProjectError, match="vendor drift"):
        _run_deploy(monkeypatch, root, run_calls=run_calls)
    assert run_calls == []


def test_override_runs_and_records_only_after_rc0(project_with_vendor, monkeypatch):
    root, vendor, _overlay = project_with_vendor
    (vendor / "server" / "drift.txt").write_text("x\n")
    run_calls: list = []
    rc = _run_deploy(monkeypatch, root, "--allow-local-changes", run_calls=run_calls, rc=0)
    assert rc == 0
    deploys = [c for c in run_calls if c[-1].endswith("deploy.sh")]
    assert len(deploys) == 1
    record = json.loads((root / ".mtk" / "config.json").read_text())
    assert len(record["packages"]["vault"]["overrides"]) == 1


def test_a_failed_deploy_leaves_no_override_record(project_with_vendor, monkeypatch):
    root, vendor, _overlay = project_with_vendor
    (vendor / "server" / "drift.txt").write_text("x\n")
    run_calls: list = []
    rc = _run_deploy(monkeypatch, root, "--allow-local-changes", run_calls=run_calls, rc=3)
    assert rc == 3
    record = json.loads((root / ".mtk" / "config.json").read_text())
    assert not record["packages"]["vault"].get("overrides")
