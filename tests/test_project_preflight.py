"""`mtk init` preflight: uv, git, the modal CLI, the active profile, the account.

The runner is injected, so these tests never touch a real Modal account.
Collision check: init runs before any package is pulled, so it checks every
family package's app name (<slug>-<pkg>), not just the ones a project pulls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import project


def _runner(responses: dict[str, tuple[int, str]]):
    """Fake `run(argv) -> (rc, stdout)`; unknown commands fail loudly."""

    def run(argv: list[str]) -> tuple[int, str]:
        key = argv[0]
        if key not in responses:
            raise AssertionError(f"unexpected command {argv!r}")
        return responses[key]

    return run


HEALTHY = {
    "uv": (0, "uv 0.5.30"),
    "git": (0, "git version 2.45"),
    "modal": (0, "modal client version: 1.5.3"),
}


class TestPrerequisites:
    def test_missing_uv_refuses_with_the_fix(self):
        responses = dict(HEALTHY)
        responses["uv"] = (127, "")
        with pytest.raises(project.ProjectError, match="uv"):
            project.check_prerequisites(_run=_runner(responses))

    def test_missing_modal_cli_refuses_with_the_install_fix(self):
        responses = dict(HEALTHY)
        responses["modal"] = (127, "")
        with pytest.raises(project.ProjectError, match="uv tool install modal"):
            project.check_prerequisites(_run=_runner(responses))

    def test_no_active_profile_refuses_and_names_modal_setup(self):
        responses = dict(HEALTHY)
        responses["modal"] = (0, "")  # version ok; profile check handled below
        run = _runner(responses)

        def run_with_profile(argv):
            if argv[:2] == ["modal", "profile"]:
                return (1, "no active profile")
            return run(argv)

        with pytest.raises(project.ProjectError, match="modal setup"):
            project.check_prerequisites(_run=run_with_profile)

    def test_all_healthy_passes(self):
        def run(argv):
            if argv[:2] == ["modal", "profile"]:
                return (0, "kylebrodeur-uof")
            if argv[:3] == ["modal", "app", "list"]:
                return (0, "[]")
            return HEALTHY[argv[0]]

        project.check_prerequisites(_run=run)  # no exception


class TestAccountCollision:
    def test_existing_app_with_composed_name_refuses(self):
        apps = [{"description": "writing-lab-embedding", "state": "deployed"}]
        with pytest.raises(project.ProjectError, match="writing-lab-embedding"):
            project.check_collisions("writing-lab", apps)

    def test_unrelated_apps_do_not_collide(self):
        apps = [{"description": "someone-else-vision", "state": "deployed"}]
        project.check_collisions("writing-lab", apps)  # no exception

    def test_same_slug_prefix_but_different_package_is_not_a_collision(self):
        apps = [{"description": "writing-lab-notes-extra", "state": "deployed"}]
        project.check_collisions("writing-lab", apps)  # not a family app name
