"""Skill discovery: each server repo declares its skills; mtk finds and filters them.

Contract:
- A server repo declares its skills in server/mtk-skills.toml. Each entry gives the
  skill name (mtk-<pkg>-<stage>), its path in the repo, its stage, when to call
  it, and the condition under which it applies (configured or deployed).
- Discovery reads only pulled repos. It never suggests a skill for a package
  that is not pulled, configured, or deployed, as the condition requires.
- A malformed entry is reported, not silently dropped, and never breaks the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import skills_catalog as catalog

MANIFEST = """
[[skill]]
name = "mtk-vault-build-deploy"
path = "skills/mtk-vault-build-deploy"
stage = "build-deploy"
when = "before the first deploy and after each tag upgrade"
applies = "configured"
summary = "Build and deploy the vault through the overlay."

[[skill]]
name = "mtk-vault-develop"
path = "skills/mtk-vault-develop"
stage = "develop"
when = "when writing a hook or overlay on top of the vault"
applies = "deployed"
summary = "Extend the vault from an overlay."
"""


def _repo(root: Path, pkg_dir: str, manifest: str | None) -> Path:
    repo = root / pkg_dir
    (repo / "server").mkdir(parents=True)
    if manifest is not None:
        (repo / "server" / "mtk-skills.toml").write_text(manifest)
    return repo


class TestManifestParsing:
    def test_reads_each_declared_skill(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", MANIFEST)
        skills = catalog.load_manifest(repo, pkg="vault")
        assert [s.name for s in skills] == ["mtk-vault-build-deploy", "mtk-vault-develop"]
        assert skills[0].stage == "build-deploy"
        assert skills[1].applies == "deployed"

    def test_repo_without_manifest_declares_nothing(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", None)
        assert catalog.load_manifest(repo, pkg="vault") == []

    def test_name_must_compose_as_mtk_pkg_stage(self, tmp_path):
        bad = MANIFEST.replace('name = "mtk-vault-develop"', 'name = "develop-notes"')
        repo = _repo(tmp_path, "modal-vault-server", bad)
        skills, problems = catalog.load_manifest_with_problems(repo, pkg="vault")
        assert [s.name for s in skills] == ["mtk-vault-build-deploy"]
        assert any("develop-notes" in p for p in problems)

    def test_unknown_stage_is_reported_not_loaded(self, tmp_path):
        bad = MANIFEST.replace('stage = "develop"', 'stage = "party"')
        repo = _repo(tmp_path, "modal-vault-server", bad)
        skills, problems = catalog.load_manifest_with_problems(repo, pkg="vault")
        assert "mtk-vault-develop" not in [s.name for s in skills]
        assert any("party" in p for p in problems)


class TestFilterByProjectState:
    def test_configured_skill_is_suggested_when_package_is_configured(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", MANIFEST)
        got = catalog.suggest(
            [repo], pkg_state={"vault": {"configured": True, "deployed": False}}, pkgs_pulled={"vault"}
        )
        names = [s.name for s in got]
        assert "mtk-vault-build-deploy" in names
        assert "mtk-vault-develop" not in names  # needs deployed

    def test_deployed_skill_only_when_deployed(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", MANIFEST)
        got = catalog.suggest(
            [repo], pkg_state={"vault": {"configured": True, "deployed": True}}, pkgs_pulled={"vault"}
        )
        assert "mtk-vault-develop" in [s.name for s in got]

    def test_package_not_pulled_suggests_nothing(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", MANIFEST)
        got = catalog.suggest([repo], pkg_state={"vault": {"configured": True, "deployed": True}}, pkgs_pulled=set())
        assert got == []

    def test_package_not_deployed_gets_no_deployed_only_skills(self, tmp_path):
        repo = _repo(tmp_path, "modal-vault-server", MANIFEST)
        got = catalog.suggest(
            [repo], pkg_state={"vault": {"configured": True, "deployed": False}}, pkgs_pulled={"vault"}
        )
        assert all(s.applies != "deployed" for s in got)


@pytest.mark.parametrize("stage", catalog.STAGES)
def test_every_stage_name_is_accepted(tmp_path, stage):
    text = MANIFEST.replace('name = "mtk-vault-develop"', f'name = "mtk-vault-{stage}"').replace(
        'stage = "develop"', f'stage = "{stage}"'
    )
    repo = _repo(tmp_path, "modal-vault-server", text)
    _skills, problems = catalog.load_manifest_with_problems(repo, pkg="vault")
    assert not problems
