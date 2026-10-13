"""`mtk pull`: clone package repos into the repos root and record them in the project.

Contract: clone only when the checkout is absent. An existing checkout is left
alone (no fetch, no reset). Never touches the Modal account. The clone source is
injected so tests use a local bare repo, not GitHub.
"""

from __future__ import annotations

import json
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


class TestPullClones:
    def test_clones_missing_checkout_into_repos_root(self, project_root, tmp_path, upstream):
        repos = tmp_path / "repos"
        repos.mkdir()
        project.pull_packages(project_root, repos, ["vault"], url_for=_url_map(upstream))
        assert (repos / "modal-vault-server" / "README.md").is_file()

    def test_records_the_pulled_package_in_the_project_record(self, project_root, tmp_path, upstream):
        repos = tmp_path / "repos"
        repos.mkdir()
        project.pull_packages(project_root, repos, ["vault"], url_for=_url_map(upstream))
        record = json.loads((project_root / ".mtk" / "config.json").read_text())
        assert record["packages"]["vault"]["path"] == str(repos / "modal-vault-server")


class TestPullLeavesExistingCheckoutAlone:
    def test_present_checkout_is_not_fetched_or_reset(self, project_root, tmp_path, upstream):
        repos = tmp_path / "repos"
        repos.mkdir()
        existing = repos / "modal-vault-server"
        project.pull_packages(project_root, repos, ["vault"], url_for=_url_map(upstream))
        marker = existing / "LOCAL-EDIT.txt"
        marker.write_text("operator work, must survive\n")

        project.pull_packages(project_root, repos, ["vault"], url_for=_url_map(upstream))

        assert marker.read_text() == "operator work, must survive\n"


class TestPullRefusals:
    def test_unknown_package_is_refused_before_any_clone(self, project_root, tmp_path, upstream):
        repos = tmp_path / "repos"
        repos.mkdir()
        with pytest.raises(project.ProjectError, match="unknown package"):
            project.pull_packages(project_root, repos, ["nonsense"], url_for=_url_map(upstream))
        assert not any(repos.iterdir())

    def test_default_url_maps_each_family_package_to_its_repo(self):
        assert project.repo_url("vault") == "https://github.com/kylebrodeur/modal-vault-server"
        assert project.repo_url("embedding") == "https://github.com/kylebrodeur/modal-embedding-server"
        assert project.repo_url("inference") == "https://github.com/kylebrodeur/modal-inference-server"
        assert project.repo_url("vision") == "https://github.com/kylebrodeur/modal-vision-server"
        assert project.repo_url("finetune") == "https://github.com/kylebrodeur/modal-finetune-server"
