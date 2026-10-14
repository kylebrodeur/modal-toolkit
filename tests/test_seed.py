"""`mtk seed <pkg>`: pre-deploy weight staging, declared by the project in seed.json.

Contract:
- The declaration is deploys/<pkg>-server/seed.json. Shape (v1):
  {"schemaVersion": 1, "<unit>": {"profile": str, "aliases": [str, ...]}, ...}
  The top-level "$comment" is allowed and never data.
- Units are iterated in file order; for each, seed runs the upstream bootstrap
  command (modal run modal_service.py::bootstrap_model --alias A) once per
  alias, in order. The runner is injected so tests never shell out.
- Refusals before anything runs: not a project, no overlay, no seed.json, an
  unknown schemaVersion, an empty alias list, or a missing unit name/profile.
- A unit whose aliases were already seeded (recorded in project state) is
  skipped; --force re-seeds everything. Completion is recorded per unit+alias.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolkit import project as mtk_project
from toolkit import seed as mtk_seed


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "myproj"
    (root / ".mtk").mkdir(parents=True)
    (root / ".mtk" / "config.json").write_text(json.dumps({"slug": "myproj", "packages": {}}) + "\n")
    overlay = root / "deploys" / "inference-server"
    overlay.mkdir(parents=True)
    (overlay / "deploy.json").write_text(json.dumps({"app_name": "myproj-inference"}) + "\n")
    (overlay / "deploy.sh").write_text("#!/bin/sh\ntrue\n")
    # a minimal vendored tree so the seed runner has somewhere to run
    _vendored = _make_vendor(root)
    return root


SEED = {
    "schemaVersion": 1,
    "$comment": "test catalog",
    "alpha": {"profile": "llama-hemmingway", "aliases": ["hemmingway-1"]},
    "beta": {"profile": "llama-ab", "aliases": ["novelist-eclipse-31b"]},
}


def _make_vendor(project: Path) -> Path:
    """A minimal single-tag upstream repo, vendored into the project."""
    import subprocess

    upstream = project.parent / "upstream-modal-inference-server"
    upstream.mkdir(exist_ok=True)
    subprocess.run(
        "git init -q -b main && git config user.email t@t && git config user.name t "
        "&& mkdir server && echo model > server/modal_service.py "
        "&& git add -A && git commit -qm one && git tag v1.0.0",
        shell=True,
        cwd=upstream,
        check=True,
        capture_output=True,
    )
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    return mtk_project.vendor_dir(project, "inference")


def test_seed_runs_the_upstream_command_once_per_alias_in_order(project: Path) -> None:
    overlay = project / "deploys" / "inference-server"
    (overlay / "seed.json").write_text(json.dumps(SEED) + "\n")
    seen: list[tuple] = []

    def runner(call: tuple) -> None:
        seen.append(call)

    mtk_seed.run(project, "inference", runner=runner)
    assert [c[0] for c in seen] == [
        ["modal", "run", "modal_service.py::bootstrap_model", "--alias", "hemmingway-1"],
        ["modal", "run", "modal_service.py::bootstrap_model", "--alias", "novelist-eclipse-31b"],
    ]
    # every call runs inside the vendored server dir with the unit profile set
    vendor_server = mtk_project.vendor_dir(project, "inference") / "server"
    assert all(c[1] == vendor_server for c in seen)
    assert seen[0][2]["MODAL_INFERENCE_PROFILE"] == "llama-hemmingway"
    assert seen[1][2]["MODAL_INFERENCE_PROFILE"] == "llama-ab"


def test_seed_refuses_without_a_declaration(project: Path) -> None:
    with pytest.raises(mtk_project.ProjectError, match=r"seed\.json"):
        mtk_seed.run(project, "inference", runner=[])


def test_seed_refuses_outside_a_project(tmp_path: Path) -> None:
    # The project check precedes the overlay check, so a bare dir is refused as
    # not-a-project, not as a missing overlay.
    with pytest.raises(mtk_project.ProjectError, match="is not a project"):
        mtk_seed.run(tmp_path, "inference", runner=[])


def test_seed_refuses_an_unknown_schema_version(project: Path) -> None:
    bad = dict(SEED)
    bad["schemaVersion"] = 2
    (project / "deploys" / "inference-server" / "seed.json").write_text(json.dumps(bad) + "\n")
    with pytest.raises(mtk_project.ProjectError, match="schemaVersion"):
        mtk_seed.run(project, "inference", runner=[])


def test_seed_refuses_an_alias_list_that_is_empty_or_missing(project: Path) -> None:
    overlay = project / "deploys" / "inference-server"
    empty = {"schemaVersion": 1, "alpha": {"profile": "p", "aliases": []}}
    (overlay / "seed.json").write_text(json.dumps(empty) + "\n")
    with pytest.raises(mtk_project.ProjectError, match="aliases"):
        mtk_seed.run(project, "inference", runner=[])

    # Same refusal when the aliases list is missing entirely.
    (overlay / "seed.json").write_text(json.dumps({"schemaVersion": 1, "alpha": {"profile": "p"}}) + "\n")
    (project / ".mtk" / "config.json").write_text(json.dumps({"slug": "myproj", "packages": {}}) + "\n")
    with pytest.raises(mtk_project.ProjectError, match="aliases"):
        mtk_seed.run(project, "inference", runner=[])


def test_already_seeded_units_are_skipped_and_completion_is_recorded(project: Path) -> None:
    overlay = project / "deploys" / "inference-server"
    (overlay / "seed.json").write_text(json.dumps(SEED) + "\n")
    seen: list[tuple] = []

    def runner(call: tuple) -> None:
        seen.append(call)

    mtk_seed.run(project, "inference", runner=runner)
    assert len(seen) == 2
    before = len(seen)
    mtk_seed.run(project, "inference", runner=runner)  # nothing new
    assert len(seen) == before
    record = json.loads((project / ".mtk" / "config.json").read_text())
    assert record["packages"]["inference"]["seeded"] == ["alpha:hemmingway-1", "beta:novelist-eclipse-31b"]


def test_force_reseeds_everything(project: Path) -> None:
    overlay = project / "deploys" / "inference-server"
    (overlay / "seed.json").write_text(json.dumps(SEED) + "\n")
    seen: list[tuple] = []

    def runner(call: tuple) -> None:
        seen.append(call)

    mtk_seed.run(project, "inference", runner=runner)
    first = len(seen)
    mtk_seed.run(project, "inference", runner=runner, force=True)
    assert len(seen) == 2 * first


def test_a_unit_needs_a_profile(project: Path) -> None:
    bad = {"schemaVersion": 1, "alpha": {"aliases": ["a"]}}
    (project / "deploys" / "inference-server" / "seed.json").write_text(json.dumps(bad) + "\n")
    with pytest.raises(mtk_project.ProjectError, match="profile"):
        mtk_seed.run(project, "inference", runner=[])
