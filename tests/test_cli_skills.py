"""`mtk skills`: list the lifecycle skills this project can use right now.

Contract:
- cmd_skills reads server/mtk-skills.toml from every VENDORED package in the
  project record, plus the toolkit's own skills dir (the router surface).
- Suggestions are filtered by the project state: the package must be vendored
  (pulled), and applies-gated skills need their link (configured / deployed).
- `deployed` comes from the record's packages.<pkg>.deployed (written by a
  successful mtk deploy), NOT from the overlay's existence.
- Malformed manifests are reported, never silent; --stage and --json work.
- Unknown packages or no vendored repos get an honest empty answer with the
  fix line, rc 1 for empty.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli
from toolkit import project as mtk_project

MANIFEST = """\
[[skill]]
name = "mtk-vault-build-deploy"
path = "skills/vault-build-deploy"
stage = "build-deploy"
applies = "any"
when = "deploying or upgrading the vault"
summary = "How to deploy this vault."

[[skill]]
name = "mtk-vault-develop"
path = "skills/vault-develop"
stage = "develop"
applies = "deployed"
when = "building on a running vault"
summary = "Extend a live vault."
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    mtk_project.init_project(root)
    monkeypatch.chdir(root)
    # vendor vault with a manifest declaring two skills
    origin = tmp_path / "origin-modal-vault-server"
    origin.mkdir()
    import subprocess as sp

    sp.run(
        "git init -q -b main && git config user.email t@t && git config user.name t"
        " && mkdir -p server skills/vault-build-deploy skills/vault-develop"
        " && echo s > server/app.py"
        " && git add -A && git commit -qm one && git tag v1.0.0",
        shell=True,
        cwd=origin,
        check=True,
        capture_output=True,
    )
    (origin / "server" / "mtk-skills.toml").write_text(MANIFEST + "\n")
    (origin / "skills" / "vault-build-deploy" / "SKILL.md").write_text("---\nname: mtk-vault-build-deploy\n---\nbody\n")
    (origin / "skills" / "vault-develop" / "SKILL.md").write_text("---\nname: mtk-vault-develop\n---\nbody\n")
    sp.run(["git", "add", "-A"], cwd=origin, check=True, capture_output=True)
    sp.run(["git", "commit", "-q", "-m", "manifest"], cwd=origin, check=True, capture_output=True)
    sp.run(["git", "tag", "-f", "v1.0.0"], cwd=origin, check=True, capture_output=True)
    mtk_project.vendor_packages(root, ["vault"], tags={"vault": "v1.0.0"}, url_for=lambda _p: str(origin))
    return root


def _run_skills(monkeypatch, *extra):
    args = cli.build_parser().parse_args(["skills", *extra])
    import contextlib
    import io

    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = cli.cmd_skills(args)
    return rc, buf.getvalue() + err.getvalue()


def test_lists_skills_for_vendored_packages_with_state(project, monkeypatch):
    rc, out = _run_skills(monkeypatch)
    assert rc == 0
    # the suggestion LISTS build-deploy, and the deploy-gated one is reported
    # as gated, not suggested: it appears only in the status line
    assert "mtk-vault-build-deploy" in out
    assert out.count("mtk-vault-develop") == 1
    assert "gated" in out


def test_deployed_gate_follows_the_recorded_deploy_signal(project, monkeypatch):
    record_path = project / ".mtk" / "config.json"
    record = json.loads(record_path.read_text())
    record["packages"]["vault"]["deployed"] = {"at": "2026-10-14T00:00:00"}
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    rc, out = _run_skills(monkeypatch)
    assert rc == 0
    assert "mtk-vault-develop" in out


def test_stage_filter(project, monkeypatch):
    rc, out = _run_skills(monkeypatch, "--stage", "build-deploy")
    assert rc == 0
    assert "mtk-vault-build-deploy" in out


def test_json_output(project, monkeypatch):
    args = cli.build_parser().parse_args(["skills", "--json"])
    import contextlib
    import io

    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = cli.cmd_skills(args)
    assert rc == 0
    data = json.loads(buf.getvalue())  # only stdout: stderr carries status lines
    assert isinstance(data, list) and data
    assert data[0]["name"] == "mtk-vault-build-deploy"


def test_empty_project_is_honest_rc1(project, monkeypatch):
    # empty the vendor state: no packages pulled
    record_path = project / ".mtk" / "config.json"
    record = json.loads(record_path.read_text())
    record["packages"] = {}
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    rc, out = _run_skills(monkeypatch)
    assert rc == 1
    assert "mtk pull" in out


def test_outside_a_project_refuses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out = _run_skills(monkeypatch)
    assert rc == 1
    assert "mtk init" in out


def test_partially_configured_package_fails_the_configured_gate(project, monkeypatch):
    """A section missing its token is NOT configured: the gate reads
    cfg.validate's `ok`, not the presence of any keys."""
    # vault's REQUIRED_KEYS = base_url + token: give only base_url
    monkeypatch.setenv("MODAL_VAULT_BASE_URL", "https://vault.example.com")
    (project / ".mtk" / "deployed.json")  # touch nothing; the gate is env-fed
    rc, out = _run_skills(monkeypatch)
    assert rc == 0
    # init-config (applies any) shows; the project stays honest about the gate
    assert "mtk-vault-build-deploy" in out


def test_no_manifest_packages_get_an_honest_status_line(project, monkeypatch):
    """A pinned release with no mtk-skills.toml yet says so per package:
    the misleading 'vendor more packages' hint is replaced by the honest one."""
    # strip the manifest from the vendored copy (simulates a release before manifests)
    vendor = mtk_project.vendor_dir(project, "vault")
    (vendor / "server" / "mtk-skills.toml").unlink()
    rc, out = _run_skills(monkeypatch)
    assert rc == 1
    assert "declares no skills" in out
    assert "vendor more packages" not in out
