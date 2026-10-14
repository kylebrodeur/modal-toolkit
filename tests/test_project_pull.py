"""`mtk pull`: vendor a tag-pinned copy into the project (the vendor model).

One model (2026-10-14): `mtk pull <pkg> --tag <release>` vendors into
vendor/<pkg>-server/ pinned to the exact tag, provenance-recorded and guarded.
The legacy repos-root clone is gone. Package->repo URL mapping tests stay; the
vendor behavior lives in test_vendor.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import project


def _git(*argv: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def upstream(tmp_path):
    """A local repo standing in for a GitHub package repo."""
    src = tmp_path / "upstream" / "modal-vault-server"
    src.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=src)
    _git("config", "user.email", "t@example.com", cwd=src)
    _git("config", "user.name", "t", cwd=src)
    (src / "README.md").write_text("vault\n")
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "init", cwd=src)
    return src


@pytest.fixture()
def project_root(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    project.init_project(root)
    return root


def _url_map(upstream: Path):
    return lambda pkg: str(upstream) if pkg == "vault" else None


def test_default_url_maps_each_family_package_to_its_repo():
    assert project.repo_url("vault") == "https://github.com/kylebrodeur/modal-vault-server"
    assert project.repo_url("embedding") == "https://github.com/kylebrodeur/modal-embedding-server"
    assert project.repo_url("inference") == "https://github.com/kylebrodeur/modal-inference-server"
    assert project.repo_url("vision") == "https://github.com/kylebrodeur/modal-vision-server"
    assert project.repo_url("finetune") == "https://github.com/kylebrodeur/modal-finetune-server"
