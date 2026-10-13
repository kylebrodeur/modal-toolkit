"""`mtk deploy <pkg>`: provenance preflight, app-name check, and secrets gate.

Contract, enforced before any account-touching step:
- the overlay deploys/<pkg>/ exists and holds deploy.json + deploy.sh;
- deploy.json names the app <slug>-<pkg> (composed, never bare);
- missing secrets are created only after every one is confirmed.
Account and subprocess calls are injected, so tests never touch Modal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import project


def _overlay(root: Path, pkg: str, app_name: str | None) -> Path:
    overlay = root / "deploys" / pkg
    overlay.mkdir(parents=True)
    body: dict = {"pkg": pkg}
    if app_name is not None:
        body["app_name"] = app_name
    (overlay / "deploy.json").write_text(json.dumps(body))
    (overlay / "deploy.sh").write_text("#!/bin/sh\n")
    return overlay


@pytest.fixture()
def project_root(tmp_path):
    root = tmp_path / "writing-lab"
    root.mkdir()
    project.init_project(root)
    return root


class TestOverlayProvenance:
    def test_missing_overlay_refuses(self, project_root):
        with pytest.raises(project.ProjectError, match="deploys/vault"):
            project.check_overlay(project_root, "vault")

    def test_overlay_without_deploy_sh_refuses(self, project_root):
        overlay = _overlay(project_root, "vault", "writing-lab-vault")
        (overlay / "deploy.sh").unlink()
        with pytest.raises(project.ProjectError, match=r"deploy\.sh"):
            project.check_overlay(project_root, "vault")

    def test_overlay_without_deploy_json_refuses(self, project_root):
        overlay = _overlay(project_root, "vault", "writing-lab-vault")
        (overlay / "deploy.json").unlink()
        with pytest.raises(project.ProjectError, match=r"deploy\.json"):
            project.check_overlay(project_root, "vault")


class TestAppNameIsComposed:
    def test_bare_app_name_is_refused(self, project_root):
        _overlay(project_root, "vault", "vault")
        with pytest.raises(project.ProjectError, match="writing-lab-vault"):
            project.check_overlay(project_root, "vault")

    def test_wrong_slug_is_refused(self, project_root):
        _overlay(project_root, "vault", "someone-else-vault")
        with pytest.raises(project.ProjectError, match="writing-lab-vault"):
            project.check_overlay(project_root, "vault")

    def test_composed_name_passes(self, project_root):
        _overlay(project_root, "vault", "writing-lab-vault")
        overlay = project.check_overlay(project_root, "vault")
        assert overlay == project_root / "deploys" / "vault"


class TestSecretsGate:
    def test_one_unconfirmed_name_refuses_and_creates_nothing(self):
        created: list[list[str]] = []
        answers = {"modal-vault-secret": True, "HF_TOKEN": False}
        with pytest.raises(project.ProjectError, match="not confirmed"):
            project.ensure_secrets(
                ["modal-vault-secret", "HF_TOKEN"],
                confirm=lambda name: answers[name],
                create_all=lambda names: created.append(names),
            )
        assert created == []

    def test_all_confirmed_creates_the_missing_set_once(self):
        created: list[list[str]] = []
        project.ensure_secrets(
            ["modal-vault-secret", "HF_TOKEN"],
            confirm=lambda name: True,
            create_all=lambda names: created.append(names),
        )
        assert created == [["modal-vault-secret", "HF_TOKEN"]]

    def test_nothing_missing_creates_nothing_and_asks_nothing(self):
        created: list[list[str]] = []

        def ask(name: str) -> bool:
            raise AssertionError("no prompt when nothing is missing")

        project.ensure_secrets([], confirm=ask, create_all=lambda names: created.append(names))
        assert created == []


class _Status:
    def __init__(self, manifest_found: bool, missing: list[str]) -> None:
        self.manifest_found = manifest_found
        self.missing = missing


class TestManifestRequired:
    """The gate fails CLOSED: no readable manifest is a refusal, never an empty list."""

    def test_absent_status_refuses(self):
        with pytest.raises(project.ProjectError, match="mtk pull inference"):
            project.missing_secrets(None, "inference")

    def test_manifest_not_found_refuses(self):
        with pytest.raises(project.ProjectError, match="mtk pull inference"):
            project.missing_secrets(_Status(manifest_found=False, missing=[]), "inference")

    def test_found_manifest_returns_its_missing_names(self):
        got = project.missing_secrets(_Status(manifest_found=True, missing=["inference-auth-secret"]), "inference")
        assert got == ["inference-auth-secret"]

    def test_found_manifest_with_nothing_missing_returns_empty(self):
        assert project.missing_secrets(_Status(manifest_found=True, missing=[]), "inference") == []
