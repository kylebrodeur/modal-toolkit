"""Provenance guard + vendor_check: filename parsing must survive git's quoting.

git status --porcelain quotes paths containing special characters as
"C-spec escaped" strings. The smoke test caught the guard reporting
'EADME.md' for a README.md edit (the C-escape parser ate the first two
characters as if they were octal). Contract: a drifted file is reported with
its REAL name, and the managed file is excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolkit import project


def _git(cwd: Path, *argv: str) -> None:
    import subprocess

    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def vendored(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "p"
    (project_root / ".mtk").mkdir(parents=True)
    (project_root / ".mtk" / "config.json").write_text(json.dumps({"slug": "p", "packages": {}}) + "\n")
    origin = tmp_path / "origin-modal-vault-server"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "server").mkdir()
    (origin / "README.md").write_text("r\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "one")
    _git(origin, "tag", "v1.0.0")
    project.vendor_packages(project_root, ["vault"], tags={"vault": "v1.0.0"}, url_for=lambda _p: str(origin))
    return project_root, project.vendor_dir(project_root, "vault")


def test_a_renamed_style_edit_reports_the_real_name(vendored: tuple[Path, Path]) -> None:
    project_root, vendor = vendored
    # The exact case the smoke test caught: a README.md edit showed as EADME.md.
    (vendor / "README.md").write_text("drifted\n")
    _ok, findings = project.vendor_check(project_root, "vault")
    assert not _ok
    mangled = [f for f in findings if "is modified:" in f]
    assert mangled, findings
    # the extracted name (the token after "is modified:") is exactly README.md
    assert all(f.split("is modified: ", 1)[1].split(" ", 1)[0] == "README.md" for f in mangled)


def test_an_untracked_file_reports_its_real_name(vendored: tuple[Path, Path]) -> None:
    project_root, vendor = vendored
    (vendor / "server").mkdir()
    (vendor / "server" / "scratch.py").write_text("x = 1\n")
    _ok, findings = project.vendor_check(project_root, "vault")
    assert not _ok
    assert any("scratch.py" in f for f in findings)


def test_the_managed_provenance_file_does_not_count_as_drift(vendored: tuple[Path, Path]) -> None:
    project_root, vendor = vendored
    # vendor_packages just wrote it; re-write with different whitespace to prove the exclusion.
    vendor.joinpath(project.VENDOR_META).write_text(json.dumps({"repo": "x", "tag": "v1.0.0", "commit": "??"}) + "\n")
    ok, findings = project.vendor_check(project_root, "vault")
    # provenance drift IS caught through the commit comparison, not the status scan
    assert not ok
    assert not any("is modified" in f for f in findings)
