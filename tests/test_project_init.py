"""`mtk init`: scaffold a project record and its deploys/ directory.

Contract: the scaffold is local. It writes .mtk/config.json (slug + pulled
packages) and creates deploys/. It refuses to overwrite an existing record and
refuses a slug that cannot compose a Modal app name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import project


def _record(root: Path) -> dict:
    return json.loads((root / ".mtk" / "config.json").read_text())


class TestInitBare:
    def test_slug_is_the_directory_name(self, tmp_path):
        target = tmp_path / "writing-lab"
        target.mkdir()
        root = project.init_project(target)
        assert root == target
        assert _record(root)["slug"] == "writing-lab"
        assert _record(root)["packages"] == {}

    def test_creates_deploys_directory(self, tmp_path):
        target = tmp_path / "lab-notes"
        target.mkdir()
        project.init_project(target)
        assert (target / "deploys").is_dir()

    def test_rejects_a_directory_name_that_cannot_compose_an_app_name(self, tmp_path):
        target = tmp_path / "Writing_Lab"
        target.mkdir()
        with pytest.raises(project.ProjectError):
            project.init_project(target)
        assert not (target / ".mtk").exists()


class TestInitNamed:
    def test_creates_named_directory_and_uses_the_id_as_slug(self, tmp_path):
        root = project.init_project(tmp_path, project_id="field-notes")
        assert root == tmp_path / "field-notes"
        assert _record(root)["slug"] == "field-notes"
        assert (root / "deploys").is_dir()

    @pytest.mark.parametrize("bad", ["Has Spaces", "UPPER", "under_score", "-leading", "a" * 65])
    def test_rejects_ids_that_cannot_compose_a_modal_app_name(self, tmp_path, bad):
        # App names compose as <slug>-<pkg>. Modal names use lowercase letters,
        # digits, and dashes. Reject anything else before writing a record.
        with pytest.raises(project.ProjectError):
            project.init_project(tmp_path, project_id=bad)
        assert not (tmp_path / bad).exists()


class TestInitRefusesToOverwrite:
    def test_second_init_on_same_directory_raises_and_keeps_the_record(self, tmp_path):
        target = tmp_path / "lab-notes"
        target.mkdir()
        project.init_project(target)
        before = _record(target)
        with pytest.raises(project.ProjectError):
            project.init_project(target)
        assert _record(target) == before
