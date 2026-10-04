"""Smoke tests for the toolkit config + CLI modules."""

from __future__ import annotations

import sys
from pathlib import Path

# Import the toolkit package relative to the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_config_path_defaults(monkeypatch):
    from toolkit import config

    monkeypatch.delenv("MODAL_TOOLKIT_CONFIG", raising=False)
    assert config._path() == config.CONFIG_PATH_DEFAULT


def test_config_path_env_override(monkeypatch, tmp_path):
    from toolkit import config

    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "custom.json"))
    assert config._path() == tmp_path / "custom.json"


def test_write_and_read_roundtrip(monkeypatch, tmp_path):
    from toolkit import config

    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
    config.write({"embedding": {"base_url": "https://x", "token": "t", "model": "m", "dim": 768}})
    data = config.load()
    assert data["embedding"]["base_url"] == "https://x"
    # Permissions must be 600 (owner rw): stat().st_mode masks with 0o777 on macOS.
    assert (tmp_path / "cfg.json").stat().st_mode & 0o777 == 0o600


def test_section_applies_env_override(monkeypatch):
    from toolkit import config

    monkeypatch.setenv("MODAL_BASE_URL", "https://override.example.com")
    got = config.section("embedding")
    assert got["base_url"] == "https://override.example.com"


def test_validate_reports_missing_keys(monkeypatch, tmp_path):
    from toolkit import config

    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("MODAL_BASE_URL", raising=False)
    monkeypatch.delenv("MODAL_PROXY_TOKEN", raising=False)
    config.write({})
    audit = config.validate()
    for _pkg, state in audit["packages"].items():
        assert not state["ok"]  # all packages missing required keys
        assert len(state["missing"]) > 0


def test_repos_root_env_override(monkeypatch, tmp_path):
    from toolkit import config

    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    assert config.repos_root() == tmp_path


def test_repos_root_falls_back_to_sibling_dir(monkeypatch):
    from toolkit import config

    monkeypatch.delenv("MODAL_TOOLKIT_REPOS", raising=False)
    monkeypatch.delenv("MODAL_TOOLKIT_CONFIG", raising=False)
    # __file__ resolves inside the repo, so parents[2] should be the workspace.
    root = config.repos_root()
    assert root.name == "workspace" or (root / "modal-toolkit").exists()
