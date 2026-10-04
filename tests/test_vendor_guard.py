"""Vendor guard: an mtk deploy refuses a drifted vendor copy unless overridden.

Contract:
- vendor_check(project, pkg) returns (ok, findings) with findings as
  actionable strings; it is read-only.
- A vendored copy whose HEAD is exactly at the recorded tag with a clean
  worktree passes. Anything else (dirty worktree, tag moved, wrong commit)
  is reported, and cmd_deploy refuses before touching the account.
- cmd_deploy --allow-local-changes deploys anyway, and records the override
  in the project record so doctor can show it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from toolkit import project as mtk_project


def _run(cwd: Path, *argv: str) -> None:
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def upstream(tmp_path: Path) -> Path:
    origin = tmp_path / "origin-modal-vault-server"
    origin.mkdir()
    _run(origin, "git", "init", "-q", "-b", "main")
    _run(origin, "git", "config", "user.email", "t@t")
    _run(origin, "git", "config", "user.name", "t")
    (origin / "server").mkdir()
    (origin / "server" / "app.py").write_text("a = 1\n")
    _run(origin, "git", "add", "-A")
    _run(origin, "git", "commit", "-q", "-m", "one")
    _run(origin, "git", "tag", "v1.0.0")
    return origin


@pytest.fixture()
def vendored(tmp_path: Path, upstream: Path) -> tuple[Path, Path]:
    project = tmp_path / "myproj"
    (project / ".mtk").mkdir(parents=True)
    (project / ".mtk" / "config.json").write_text(json.dumps({"slug": "myproj", "packages": {}}) + "\n")
    mtk_project.vendor_packages(project, ["vault"], tags={"vault": "v1.0.0"}, url_for=lambda _p: str(upstream))
    return project, mtk_project.vendor_dir(project, "vault")


def test_clean_vendored_copy_passes(vendored: tuple[Path, Path]) -> None:
    project, _vendor = vendored
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert ok, findings
    assert findings == []


def test_a_local_edit_fails_the_check_with_the_file_named(vendored: tuple[Path, Path]) -> None:
    project, vendor = vendored
    (vendor / "server" / "app.py").write_text("a = 2\n")
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert not ok
    assert any("app.py" in f for f in findings)
    assert any("not a scratch tree" in f.lower() for f in findings)


def test_an_added_file_fails_the_check(vendored: tuple[Path, Path]) -> None:
    project, vendor = vendored
    (vendor / "server" / "extra.py").write_text("x = 1\n")
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert not ok
    assert any("extra.py" in f for f in findings)


def test_a_missing_provenance_file_fails_closed(vendored: tuple[Path, Path]) -> None:
    project, vendor = vendored
    (vendor / mtk_project.VENDOR_META).unlink()
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert not ok
    assert any("provenance" in f.lower() for f in findings)


def test_a_vendor_dir_that_is_not_a_git_repo_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / ".mtk").mkdir(parents=True)
    (project / ".mtk" / "config.json").write_text(json.dumps({"slug": "p", "packages": {}}) + "\n")
    vendor = mtk_project.vendor_dir(project, "vault")
    vendor.mkdir(parents=True)
    (vendor / "server").mkdir()
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert not ok
    assert any("not a vendored git copy" in f.lower() for f in findings)


def test_an_unvendored_package_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / ".mtk").mkdir(parents=True)
    (project / ".mtk" / "config.json").write_text(json.dumps({"slug": "p", "packages": {}}) + "\n")
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert not ok
    assert any("never vendored" in f.lower() for f in findings)


def test_two_tags_on_one_commit_pass_when_the_recorded_tag_resolves(tmp_path: Path) -> None:
    """vendor_check compares commits (like the shell guard): two tags sharing
    one commit must not fail just because describe picks an arbitrary one."""
    project = tmp_path / "p"
    (project / ".mtk").mkdir(parents=True)
    (project / ".mtk" / "config.json").write_text(json.dumps({"slug": "p", "packages": {}}) + "\n")
    origin = tmp_path / "origin-modal-vault-server"
    origin.mkdir()
    _run(origin, "git", "init", "-q", "-b", "main")
    _run(origin, "git", "config", "user.email", "t@t")
    _run(origin, "git", "config", "user.name", "t")
    (origin / "server").mkdir()
    (origin / "server" / "app.py").write_text("a = 1\n")
    _run(origin, "git", "add", "-A")
    _run(origin, "git", "commit", "-q", "-m", "one")
    _run(origin, "git", "tag", "v1.0.0")
    _run(origin, "git", "tag", "release-2026-10")
    mtk_project.vendor_packages(project, ["vault"], tags={"vault": "v1.0.0"}, url_for=lambda _x: str(origin))
    ok, findings = mtk_project.vendor_check(project, "vault")
    assert ok, findings
