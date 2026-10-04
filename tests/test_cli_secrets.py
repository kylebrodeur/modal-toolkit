"""Task 3 TDD tests: `mtk secrets` CLI wiring + doctor hook (tests/test_cli_secrets.py).

CLI-level: every test drives the real `toolkit.cli.main()` with patched argv,
a fake modal shim (the family-proven record-argv pattern from test_secrets.py),
and temp repos/config. Written first per TDD; cli.py had no `secrets` verb, no
cmd_secrets and no doctor secrets line when this file was created.

Pinned semantics (plan Task 3 + design doc):
  - `mtk secrets check` prints the per-package table; exit 1 when anything is
    missing (exit 0 with --json so scripts can consume).
  - `mtk secrets rotate <name>` is create --force behind a confirmation gate;
    declining aborts BEFORE ANY Modal call (empty argv log).
  - ONE `modal secret list --json` per doctor invocation regardless of
    package count; a broken modal CLI degrades the secrets line without
    killing the health report.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

# Import the toolkit package relative to the repo root (mirrors test_secrets.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli as mtk_cli
from toolkit import secrets as mtk_secrets

FAKE_SHIM = r"""#!/bin/sh
log="${MTK_FAKE_MODAL_LOG:-/dev/null}"
printf '%s\n' "$*" >> "$log"
case "$1 $2" in
    "secret list")
        printf '{"names": [%s]}\n' "${MTK_FAKE_MODAL_NAMES:-}"
        exit ${MTK_FAKE_MODAL_EXIT:-0}
        ;;
    "secret create")
        case "${MTK_FAKE_MODAL_MODE:-ok}" in
            fail)
                printf 'modal: secret exists\n' >&2
                exit 1
                ;;
        esac
        exit 0
        ;;
esac
exit 0
"""


@pytest.fixture
def shim_path(tmp_path: Path) -> Path:
    """Install the fake modal shim in a temp bin dir; return its path."""
    bin_dir = tmp_path / "modal-fake-bin"
    bin_dir.mkdir()
    shim = bin_dir / "modal"
    shim.write_text(FAKE_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    return shim


@pytest.fixture
def log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty argv log; one recorded line per shim invocation."""
    log = tmp_path / "modal-argv.log"
    monkeypatch.setenv("MTK_FAKE_MODAL_LOG", str(log))
    return log


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temp repos root + toolkit config so ambient machine state leaks nowhere."""
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(repos))
    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "no-config-file.json"))
    return repos


def _manifest(repos: Path, repo_dir: str, body: str) -> None:
    (repos / repo_dir / "server").mkdir(parents=True)
    (repos / repo_dir / "server" / "secrets.toml").write_text(body, encoding="utf-8")


VAULT_MANIFEST = """\
[secret.auth]
name = "vault-auth"
[secret.auth.keys.VAULT_API_TOKEN]
generate = "hex32"
[secret.hf]
name = "vault-hf"
[secret.hf.keys.HF_TOKEN]
ask = "HF token, read"
"""

EMBED_MANIFEST = """\
[secret.auth]
name = "embedding-auth"
[secret.auth.keys.API_TOKEN]
generate = "hex32"
"""


def _run_mtk(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Drive the real mtk CLI entrypoint with argv; return its exit code."""
    monkeypatch.setattr(sys, "argv", ["mtk", *argv])
    return mtk_cli.main()


def _list_lines(log_path: Path) -> list[str]:
    return [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if "list" in ln]


def _create_lines(log_path: Path) -> list[str]:
    return [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if "create" in ln]


# ── mtk secrets check ────────────────────────────────────────────────────────


def test_check_all_ok_exits_zero_and_prints_table(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"vault-auth", "vault-hf"')
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)

    rc = _run_mtk(monkeypatch, ["secrets", "check", "--pkg", "vault"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "vault" in out
    assert "ok" in out
    assert "missing" not in out
    assert len(_list_lines(log_path)) == 1  # ONE list call per invocation


def test_check_missing_secret_exits_one_and_prints_missing(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)

    rc = _run_mtk(monkeypatch, ["secrets", "check", "--pkg", "vault"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "missing vault-auth" in out
    assert "missing vault-hf" in out


def test_check_multi_package_single_list_call(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    # Two manifest-bearing packages -> still one workspace list call.
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)
    _manifest(isolated_repo, "modal-embedding-server", EMBED_MANIFEST)

    rc = _run_mtk(monkeypatch, ["secrets", "check"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "vault" in out and "embedding" in out
    assert len(_list_lines(log_path)) == 1


def test_check_json_flag_exits_zero_and_emits_status_json(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    # --json is the scriptable mode: exit 0 even when secrets are missing.
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)

    rc = _run_mtk(monkeypatch, ["secrets", "check", "--pkg", "vault", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    assert data["vault"]["manifest_found"] is True
    names = {s["name"] for s in data["vault"]["secrets"]}
    assert {"vault-auth", "vault-hf"} <= names


# ── mtk secrets rotate <name>: the confirmation gate ────────────────────────


def test_rotate_confirmed_creates_with_force(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"vault-auth"')
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))

    rc = _run_mtk(monkeypatch, ["secrets", "rotate", "vault-auth"])

    assert rc == 0
    creates = _create_lines(log_path)
    assert len(creates) == 1
    assert "vault-auth" in creates[0]


def test_rotate_declined_aborts_before_any_modal_call(isolated_repo, shim_path, log_path, monkeypatch):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))

    rc = _run_mtk(monkeypatch, ["secrets", "rotate", "vault-auth"])

    assert rc == 1
    # Decline aborts BEFORE ANY modal call: no list, no create, nothing.
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


def test_rotate_bare_enter_declines(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    # The gate defaults to NO: a bare Enter (empty input) aborts safely.
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    rc = _run_mtk(monkeypatch, ["secrets", "rotate", "vault-auth"])

    assert rc == 1
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


def test_rotate_unknown_name_fails_clean_with_no_modal_call(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    _manifest(isolated_repo, "modal-vault-server", VAULT_MANIFEST)

    rc = _run_mtk(monkeypatch, ["secrets", "rotate", "no-such-secret"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no-such-secret" in err
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


# ── doctor: the secrets line ────────────────────────────────────────────────


def test_doctor_prints_secrets_line_per_package(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _manifest(isolated_repo, "modal-embedding-server", EMBED_MANIFEST)

    rc = _run_mtk(monkeypatch, ["doctor", "--pkg", "embedding"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "secrets: missing embedding-auth" in out
    assert len(_list_lines(log_path)) == 1  # ONE modal call for the whole doctor run


def test_doctor_json_includes_secrets_status(isolated_repo, shim_path, log_path, monkeypatch, capsys):
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"embedding-auth"')
    _manifest(isolated_repo, "modal-embedding-server", EMBED_MANIFEST)

    rc = _run_mtk(monkeypatch, ["doctor", "--pkg", "embedding", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    assert data["embedding"]["secrets"]["ok"] is True


def test_doctor_survives_a_broken_modal_cli(isolated_repo, tmp_path, log_path, monkeypatch, capsys):
    # Doctor's health report must not die because the modal CLI is missing:
    # the secrets line degrades to an error note, probes unaffected, exit 0.
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(tmp_path / "no-such-modal")])
    _manifest(isolated_repo, "modal-embedding-server", EMBED_MANIFEST)

    rc = _run_mtk(monkeypatch, ["doctor", "--pkg", "embedding"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "secrets:" in out
    assert "embedding" in out
