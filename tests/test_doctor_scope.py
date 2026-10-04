"""The fleet commands report on the packages the operator is RUNNING.

Three rules came out of an operator-reported problem: `mtk doctor` printed
"unknown" for packages that had never been set up, and it listed missing
secrets for servers that were never going to be deployed. A doctor line about
a server nobody asked for is noise, and a "missing secrets" line for a server
that is not deployed reads like a fault that is not there.

The rules under test:

  1. IN PLAY: a package is in play when it is VENDORED in the project (the
     operator pulled it) or CONFIGURED (the config file has a section for it,
     or one of its dedicated env vars is set). A package that is neither gets
     NO line at all.
  2. The SHARED env vars (MODAL_BASE_URL names ONE url) only reach a package
     the operator has claimed. Otherwise one export makes four packages look
     configured at the same address.
  3. A package with no base_url reports "not-configured" and names the
     missing keys, and its secrets line is suppressed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli as mtk_cli
from toolkit import config as cfg
from toolkit import project as mtk_project
from toolkit import secrets as mtk_secrets


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp config + repos root, so ambient machine state leaks nowhere."""
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(repos))
    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
    for name in ("MODAL_BASE_URL", "MODAL_PROXY_TOKEN", "MODAL_VAULT_BASE_URL", "MODAL_VAULT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return repos


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["mtk", *argv])
    return mtk_cli.main()


def _project(tmp_path: Path, packages: list[str]) -> Path:
    """A project record with `packages` pulled into it."""
    root = tmp_path / "proj"
    (root / ".mtk").mkdir(parents=True)
    (root / ".mtk" / "config.json").write_text(
        json.dumps({"slug": "lab", "packages": {p: {} for p in packages}}) + "\n",
        encoding="utf-8",
    )
    return root


# ── rule 1: in play ─────────────────────────────────────────────────────────


def test_doctor_says_nothing_about_a_package_that_was_never_set_up(isolated, monkeypatch, capsys):
    # Nothing configured, no project: the doctor has no package to report.
    rc = _run(monkeypatch, ["doctor"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "embedding" not in out
    assert "inference" not in out
    assert "vision" not in out
    assert "no packages set up" in out


def test_doctor_reports_a_configured_package(isolated, monkeypatch, capsys):
    cfg.write({"embedding": {"base_url": "https://lane.example.com", "token": "t"}})

    rc = _run(monkeypatch, ["doctor", "--pkg", "embedding"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "embedding" in out


def test_doctor_reports_a_vendored_package_the_operator_never_configured(isolated, tmp_path, monkeypatch, capsys):
    # The project pulled it, so it IS part of the operator's fleet, even
    # though the config file says nothing about it.
    root = _project(tmp_path, ["vision"])
    monkeypatch.chdir(root)

    rc = _run(monkeypatch, ["doctor"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "vision" in out


def test_status_table_skips_packages_that_are_not_in_play(isolated, monkeypatch, capsys):
    cfg.write({"embedding": {"base_url": "https://lane.example.com", "token": "t"}})

    rc = _run(monkeypatch, ["status", "--no-net"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "embedding" in out
    assert "inference" not in out
    assert "vision" not in out


def test_flow_lists_only_the_packages_in_play(isolated, monkeypatch, capsys):
    cfg.write({"embedding": {"base_url": "https://lane.example.com", "token": "t"}})

    rc = _run(monkeypatch, ["flow"])

    out = capsys.readouterr().out
    assert rc == 0
    # The node lines are the "  <pkg>  [mark]" rows. The static data-path
    # text below them always describes the whole family, so it is not what
    # this assertion is about.
    node_lines = [ln for ln in out.splitlines() if ln.startswith("  ") and "[" in ln]
    assert [ln.split()[0] for ln in node_lines] == ["embedding"]


# ── rule 2: the shared env vars are scoped ─────────────────────────────────


def test_shared_base_url_does_not_claim_every_package(isolated, monkeypatch):
    # One export of MODAL_BASE_URL must not make embedding, inference, and
    # vision all look configured at the same address.
    monkeypatch.setenv("MODAL_BASE_URL", "https://shared.example.com")

    assert cfg.section("embedding").get("base_url") is None
    assert cfg.section("inference").get("base_url") is None
    assert cfg.section("vision").get("base_url") is None


def test_shared_base_url_reaches_a_claimed_package(isolated, monkeypatch):
    # The operator named embedding in the config, so the shared env var is
    # allowed to fill in the rest of embedding's section.
    cfg.write({"embedding": {"token": "t"}})
    monkeypatch.setenv("MODAL_BASE_URL", "https://shared.example.com")

    assert cfg.section("embedding")["base_url"] == "https://shared.example.com"


def test_dedicated_env_var_claims_its_package(isolated, monkeypatch):
    # MODAL_VAULT_BASE_URL names the vault lane and nothing else, so setting
    # it is a claim, and the shared vars can then reach vault too.
    monkeypatch.setenv("MODAL_VAULT_BASE_URL", "https://vault.example.com")

    assert cfg.claimed("vault") is True
    assert cfg.section("vault")["base_url"] == "https://vault.example.com"


def test_claimed_is_false_for_an_unmentioned_package(isolated):
    assert cfg.claimed("vision") is False


# ── rule 3: not-configured, and the suppressed secrets line ─────────────────


def test_unconfigured_package_reports_not_configured_and_names_the_keys(isolated, monkeypatch, capsys):
    # --pkg names the package, so it IS reported even though it is not set up.
    rc = _run(monkeypatch, ["doctor", "--pkg", "vision"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "not-configured" in out
    assert "base_url" in out


def test_unconfigured_package_has_no_secrets_line(isolated, monkeypatch, capsys):
    # A server that is not deployed and not going to be should not report
    # missing secrets: that reads like a fault that is not there.
    rc = _run(monkeypatch, ["doctor", "--pkg", "vision"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "secrets:" not in out


def test_the_word_unknown_is_not_a_status(isolated, monkeypatch, capsys):
    # "unknown" said nothing: it did not distinguish "not configured" from
    # "we could not tell". The status is now explicit.
    rc = _run(monkeypatch, ["doctor", "--pkg", "vision"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "unknown" not in out


def test_doctor_json_carries_the_status(isolated, monkeypatch, capsys):
    rc = _run(monkeypatch, ["doctor", "--pkg", "vision", "--json"])

    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["vision"]["status"] == "not-configured"
    assert "base_url" in data["vision"]["missing"]


# ── the secret-name convention ─────────────────────────────────────────────


def test_convention_accepts_the_unprefixed_server_name():
    assert mtk_secrets.convention_note("vision", "modal-vision-secret", "modal-vision-server") is None


def test_convention_accepts_a_prefixed_instance_name():
    # A lane that runs its own instance prefixes the name; that is the point
    # of the convention.
    assert mtk_secrets.convention_note("vm", "writing-duo-victoriametrics-auth", "modal-victoriametrics-server") is None


def test_convention_accepts_a_cross_server_shared_secret():
    # One HuggingFace token serves the whole family, so it is named for what
    # it holds, not for one server. That is not an outlier.
    assert mtk_secrets.convention_note("embedding", "huggingface-secret", "modal-embedding-server") is None


def test_convention_flags_a_name_that_says_nothing_about_its_server():
    note = mtk_secrets.convention_note("vision", "some-random-secret", "modal-vision-server")
    assert note is not None
    assert "vision" in note


def test_the_convention_note_reaches_the_doctor_secrets_line(isolated, monkeypatch, capsys):
    # A manifest whose secret name breaks the convention is REPORTED, never
    # rewritten: the names are public and already deployed.
    cfg.write({"embedding": {"base_url": "https://lane.example.com", "token": "t"}})
    monkeypatch.setattr(
        mtk_secrets,
        "check_all",
        lambda _pkgs=None: {
            "embedding": mtk_secrets.PackageStatus(
                pkg="embedding",
                manifest_found=True,
                secrets=[
                    mtk_secrets.SecretStatus(
                        secret_id="auth",
                        name="mystery-name",
                        exists=False,
                        drift=None,
                        convention="secret 'mystery-name' does not follow the <prefix>-embedding-<kind> convention",
                    )
                ],
                missing=["mystery-name"],
            )
        },
    )

    rc = _run(monkeypatch, ["doctor", "--pkg", "embedding"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "mystery-name" in out
    assert "convention" in out


# ── setup: one url names one lane ──────────────────────────────────────────


def test_setup_base_url_without_pkg_refuses_when_the_target_is_ambiguous(isolated, monkeypatch, capsys):
    cfg.write({"embedding": {"base_url": "https://a.example.com"}, "vision": {"base_url": "https://b.example.com"}})

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["setup", "--base-url", "https://c.example.com"])

    assert "--pkg" in str(exc.value)


def test_setup_base_url_with_pkg_writes_only_that_package(isolated, monkeypatch):
    _run(monkeypatch, ["setup", "--pkg", "vision", "--base-url", "https://vision.example.com"])

    data = cfg.load()
    assert data["vision"]["base_url"] == "https://vision.example.com"
    assert "embedding" not in data
    assert "inference" not in data


# ── the project helpers the in-play rule reads ─────────────────────────────


def test_project_root_walks_up_to_the_project(isolated, tmp_path):
    root = _project(tmp_path, ["vision"])
    deep = root / "deploys" / "vision-server"
    deep.mkdir(parents=True)

    assert mtk_project.project_root(deep) == root.resolve()


def test_project_root_is_none_outside_a_project(isolated, tmp_path):
    assert mtk_project.project_root(tmp_path) is None


def test_recorded_packages_is_empty_without_a_record(isolated, tmp_path):
    assert mtk_project.recorded_packages(tmp_path) == []
