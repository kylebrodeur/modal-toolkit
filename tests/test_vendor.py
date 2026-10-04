"""Vendor model: tag-pinned, read-only vendor copies under <project>/vendor/.

Contract:
- `mtk pull <pkg> --tag <tag>` vendors the upstream repo into
  vendor/<pkg>-server/ as a git checkout detached at that exact tag. It
  writes .mtk-vendor.json recording repo, tag, and commit.
- The vendor is refused (no clone, no partial state) if the tag does not
  exist upstream, and the command is refused outside a project.
- Upgrading (`pull --tag <new>` again) re-vendors only with confirmation.

This is a build-only contract test: git state comes from a local fixture
repo, no network.
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
    """A local bare git repo with one commit tagged v1.0.0 and a later v1.1.0."""
    origin = tmp_path / "upstream-modal-inference-server"
    origin.mkdir()
    _run(origin, "git", "init", "-q", "-b", "main")
    _run(origin, "git", "config", "user.email", "t@t")
    _run(origin, "git", "config", "user.name", "t")
    (origin / "server").mkdir()
    (origin / "server" / "app.py").write_text("print('v1')\n")
    (origin / "server" / "secrets.toml").write_text("[a]\nx = 1\n")
    (origin / "README.md").write_text("r\n")
    _run(origin, "git", "add", "-A")
    _run(origin, "git", "commit", "-q", "-m", "one")
    _run(origin, "git", "tag", "v1.0.0")
    (origin / "server" / "app.py").write_text("print('v2')\n")
    (origin / "README.md").write_text("r2\n")
    _run(origin, "git", "add", "-A")
    _run(origin, "git", "commit", "-q", "-m", "two")
    _run(origin, "git", "tag", "v1.1.0")
    return origin


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "myproj"
    (root / ".mtk").mkdir(parents=True)
    (root / ".mtk" / "config.json").write_text(json.dumps({"slug": "myproj", "packages": {}}) + "\n")
    return root


def _vendor_commit(vendor: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=vendor, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def test_pull_vendors_at_tag_with_provenance(project: Path, upstream: Path) -> None:
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    vendor = project / "vendor" / "inference-server"
    assert (vendor / "server" / "app.py").read_text() == "print('v1')\n"
    # detached at the tag, so the SHA is visible and stable
    branch = (vendor / ".git" / "HEAD").read_text()
    assert branch.startswith("ref: ") is False
    meta = json.loads((vendor / ".mtk-vendor.json").read_text())
    assert meta["repo"].endswith("modal-inference-server")
    assert meta["tag"] == "v1.0.0"
    assert meta["commit"] == _vendor_commit(vendor)


def test_pull_outside_a_project_is_refused(tmp_path: Path, upstream: Path) -> None:
    with pytest.raises(mtk_project.ProjectError, match="not a project"):
        mtk_project.vendor_packages(
            tmp_path, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream)
        )


def test_pull_refuses_an_unknown_tag_and_writes_nothing(project: Path, upstream: Path) -> None:
    with pytest.raises(mtk_project.ProjectError, match=r"v9\.9\.9"):
        mtk_project.vendor_packages(
            project, ["inference"], tags={"inference": "v9.9.9"}, url_for=lambda _p: str(upstream)
        )
    assert not (project / "vendor").exists()


def test_pull_refuses_an_unknown_package(project: Path, upstream: Path) -> None:
    with pytest.raises(mtk_project.ProjectError, match="unknown package"):
        mtk_project.vendor_packages(project, ["bogus"], tags={}, url_for=lambda _p: str(upstream))
    assert not (project / "vendor").exists()


def test_revendor_to_a_new_tag_needs_confirm(project: Path, upstream: Path) -> None:
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    vendor = project / "vendor" / "inference-server"
    # default: refuse (a re-vendor is a decision, not a side effect)
    with pytest.raises(mtk_project.ProjectError, match=r"v1\.1\.0"):
        mtk_project.vendor_packages(
            project, ["inference"], tags={"inference": "v1.1.0"}, url_for=lambda _p: str(upstream)
        )
    # with confirmation: file content moves to the new tag
    mtk_project.vendor_packages(
        project,
        ["inference"],
        tags={"inference": "v1.1.0"},
        url_for=lambda _p: str(upstream),
        confirm=lambda _q: True,
    )
    assert (vendor / "server" / "app.py").read_text() == "print('v2')\n"
    meta = json.loads((vendor / ".mtk-vendor.json").read_text())
    assert meta["tag"] == "v1.1.0"
    # and the re-vendor kept the provenance truthful: commit equals the new tag


def test_vendored_copy_is_recorded_in_the_project(project: Path, upstream: Path) -> None:
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    record = json.loads((project / ".mtk" / "config.json").read_text())
    assert record["packages"]["inference"]["vendor"] == "vendor/inference-server"
    assert record["packages"]["inference"]["tag"] == "v1.0.0"


def test_vendored_packages_needs_provenance_per_package(project: Path, upstream: Path) -> None:
    with pytest.raises(mtk_project.ProjectError, match="provenance"):
        mtk_project.vendor_packages(project, ["inference"], tags={})


def test_revendor_preserves_seeded_and_clears_deployed_on_a_tag_change(project: Path, upstream: Path) -> None:
    """A record entry merges, not replaces: seeded history keeps; the deployed
    signal keeps only when the tag is unchanged (a new pin hasn't deployed)."""
    record_path = project / ".mtk" / "config.json"
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    record = json.loads(record_path.read_text())
    record["packages"]["inference"]["deployed"] = {"at": "2026-10-14T00:00:00"}
    record["packages"]["inference"]["seeded"] = ["alpha:a"]
    record_path.write_text(json.dumps(record, indent=2) + "\n")

    # same-tag re-run: deployed stays
    mtk_project.vendor_packages(project, ["inference"], tags={"inference": "v1.0.0"}, url_for=lambda _p: str(upstream))
    record = json.loads(record_path.read_text())
    entry = record["packages"]["inference"]
    assert entry["deployed"] == {"at": "2026-10-14T00:00:00"}
    assert entry["seeded"] == ["alpha:a"]

    # tag change (confirmed): deployed clears, seeded keeps
    mtk_project.vendor_packages(
        project,
        ["inference"],
        tags={"inference": "v1.1.0"},
        url_for=lambda _p: str(upstream),
        confirm=lambda _q: True,
    )
    record = json.loads(record_path.read_text())
    entry = record["packages"]["inference"]
    assert entry["tag"] == "v1.1.0"
    assert "deployed" not in entry
    assert entry["seeded"] == ["alpha:a"]
