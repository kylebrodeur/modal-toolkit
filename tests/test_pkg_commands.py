"""Tests for the per-package command host (toolkit/pkg_commands.py + its mount).

Design A: each repo declares its `mtk <pkg>` commands in `server/mtk-commands.toml`;
the toolkit mounts one subcommand per declaration and executes a closed set of
run kinds. These tests pin the observable contract: what a manifest declares is
what `mtk` offers, and each kind runs the argv the manifest names.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli as mtk_cli
from toolkit import pkg_commands


def _write_manifest(repos: Path, repo_dir: str, body: str) -> None:
    (repos / repo_dir / "server").mkdir(parents=True, exist_ok=True)
    (repos / repo_dir / "server" / pkg_commands.MANIFEST_NAME).write_text(body, encoding="utf-8")


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repos"
    root.mkdir()
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(root))
    monkeypatch.setenv("MODAL_TOOLKIT_CONFIG", str(tmp_path / "cfg.json"))
    return root


def _run_mtk(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["mtk", *argv])
    return mtk_cli.main()


# ── load ─────────────────────────────────────────────────────────────────────


def test_load_returns_none_without_a_manifest(tmp_path: Path):
    repo = tmp_path / "modal-embedding-server"
    (repo / "server").mkdir(parents=True)
    assert pkg_commands.load(repo) is None


def test_load_parses_commands_and_defaults(tmp_path: Path):
    repo = tmp_path / "modal-vault-server"
    _write_manifest(
        repo.parent,
        "modal-vault-server",
        """\
[defaults]
kind = "container"
app = "modal-vault-server"
requires = ["base_url"]

[[command]]
name = "status"
summary = "sync state"
argv = ["ob", "sync-status"]

[[command]]
name = "exec"
summary = "any ob command"
argv = ["ob"]
passthrough = true
""",
    )
    manifest = pkg_commands.load(repo)
    assert manifest is not None and manifest.passthrough is None
    names = [c.name for c in manifest.commands]
    assert names == ["status", "exec"]
    status = manifest.commands[0]
    assert status.kind == "container"
    assert status.app == "modal-vault-server"
    assert status.requires == ("base_url",)
    # defaults merge in but a command's own value wins
    assert manifest.commands[1].passthrough is True
    assert manifest.commands[1].requires == ("base_url",)


def test_load_parses_passthrough_surface(tmp_path: Path):
    repo = tmp_path / "modal-inference-server"
    _write_manifest(
        repo.parent,
        "modal-inference-server",
        """\
[passthrough]
summary = "the repo's own CLI"
argv = ["modal-inference"]
""",
    )
    manifest = pkg_commands.load(repo)
    assert manifest is not None and manifest.commands == []
    assert manifest.passthrough is not None
    assert manifest.passthrough.argv == ("modal-inference",)


def test_load_rejects_unknown_kind(tmp_path: Path):
    repo = tmp_path / "modal-vision-server"
    _write_manifest(
        repo.parent,
        "modal-vision-server",
        """\
[[command]]
name = "deploy"
summary = "deploy"
kind = "teleport"
argv = ["deploy"]
""",
    )
    with pytest.raises(pkg_commands.CommandManifestError, match="unknown kind"):
        pkg_commands.load(repo)


def test_load_rejects_commands_and_passthrough_together(tmp_path: Path):
    repo = tmp_path / "modal-vision-server"
    _write_manifest(
        repo.parent,
        "modal-vision-server",
        """\
[passthrough]
summary = "x"
argv = ["modal-inference"]

[[command]]
name = "deploy"
summary = "deploy"
kind = "modal"
argv = ["deploy"]
""",
    )
    with pytest.raises(pkg_commands.CommandManifestError, match="not both"):
        pkg_commands.load(repo)


def test_load_rejects_kind_without_argv(tmp_path: Path):
    repo = tmp_path / "modal-vision-server"
    _write_manifest(
        repo.parent,
        "modal-vision-server",
        """\
[[command]]
name = "deploy"
summary = "deploy"
kind = "modal"
""",
    )
    with pytest.raises(pkg_commands.CommandManifestError, match="requires a non-empty argv"):
        pkg_commands.load(repo)


# ── mount (what mtk offers) ──────────────────────────────────────────────────


def test_mount_offers_exactly_the_declared_commands(repos: Path, monkeypatch):
    _write_manifest(
        repos,
        "modal-vision-server",
        """\
[[command]]
name = "deploy"
summary = "deploy the vision server"
kind = "modal"
argv = ["deploy", "server/app.py"]

[[command]]
name = "warm"
summary = "probe the vision lane"
kind = "probe"
""",
    )
    parser = mtk_cli.build_parser()
    vision = parser._subparsers._group_actions[0].choices["vision"]
    mounted = vision._subparsers._group_actions[0].choices
    assert set(mounted) == {"deploy", "warm"}


def test_mount_skips_repos_without_a_manifest(repos: Path):
    # No manifests written: a fresh toolkit-only clone mounts no package groups.
    parser = mtk_cli.build_parser()
    groups = set(parser._subparsers._group_actions[0].choices)
    assert groups.isdisjoint({"embedding", "vision", "vault", "finetune", "inference"})


def test_mount_raises_on_a_malformed_manifest(repos: Path):
    _write_manifest(
        repos,
        "modal-vision-server",
        """\
[[command]]
name = "deploy"
summary = "deploy"
kind = "nonsense"
argv = ["deploy"]
""",
    )
    with pytest.raises(SystemExit, match="invalid command manifest"):
        mtk_cli.build_parser()


# ── dispatch (each kind runs the argv it declared) ───────────────────────────


def test_echo_kind_prints_guidance(repos: Path, monkeypatch, capsys):
    _write_manifest(
        repos,
        "modal-embedding-server",
        """\
[[command]]
name = "sync"
summary = "client-side guidance"
kind = "echo"
argv = ["do the client-side thing"]
""",
    )
    rc = _run_mtk(monkeypatch, ["embedding", "sync"])
    assert rc == 0
    assert "do the client-side thing" in capsys.readouterr().out


def test_probe_kind_probes_the_package(repos: Path, monkeypatch, capsys):
    _write_manifest(
        repos,
        "modal-vision-server",
        """\
[[command]]
name = "warm"
summary = "probe"
kind = "probe"
""",
    )
    monkeypatch.setattr(mtk_cli, "_probe", lambda pkg: {"status": "ok"})
    rc = _run_mtk(monkeypatch, ["vision", "warm"])
    assert rc == 0
    assert "status=ok" in capsys.readouterr().out


def test_modal_kind_runs_argvt_in_the_repo(repos: Path, monkeypatch):
    _write_manifest(
        repos,
        "modal-finetune-server",
        """\
[[command]]
name = "train"
summary = "train a job"
kind = "modal"
argv = ["run", "server/train_modal.py"]
passthrough = true
""",
    )
    seen: dict[str, object] = {}

    def fake_modal_run(pkg, *argv, timeout=0):
        seen["pkg"], seen["argv"], seen["timeout"] = pkg, argv, timeout
        return True, "", "done"

    monkeypatch.setattr(mtk_cli, "_modal_run", fake_modal_run)
    rc = _run_mtk(monkeypatch, ["finetune", "train", "--lr", "3e-4"])
    assert rc == 0
    assert seen["pkg"] == "finetune"
    assert seen["argv"] == ("run", "server/train_modal.py", "--lr", "3e-4")


def test_requires_gate_uses_the_exact_fix_error(repos: Path, monkeypatch, capsys):
    _write_manifest(
        repos,
        "modal-embedding-server",
        """\
[[command]]
name = "reindex"
summary = "rebuild the index"
kind = "modal"
argv = ["run", "server/app.py"]
requires = ["base_url", "token"]
""",
    )
    monkeypatch.setattr(mtk_cli, "_modal_run", lambda *a, **k: (True, "", "ran"))
    with pytest.raises(SystemExit) as exc:
        _run_mtk(monkeypatch, ["embedding", "reindex"])
    assert "embedding is not configured" in str(exc.value)


def test_deploy_command_stays_reachable_without_config(repos: Path, monkeypatch):
    # The bootstrap order is deploy -> configure, so a deploy command must not
    # be hidden behind a base_url gate.
    _write_manifest(
        repos,
        "modal-vision-server",
        """\
[[command]]
name = "deploy"
summary = "deploy"
kind = "modal"
argv = ["deploy", "server/app.py"]
""",
    )
    monkeypatch.setattr(mtk_cli, "_modal_run", lambda *a, **k: (True, "", "deployed"))
    rc = _run_mtk(monkeypatch, ["vision", "deploy"])
    assert rc == 0


def test_passthrough_forwards_argv_verbatim(repos: Path, monkeypatch):
    _write_manifest(
        repos,
        "modal-inference-server",
        """\
[passthrough]
summary = "the repo's own CLI"
argv = ["modal-inference"]
""",
    )
    seen: dict[str, object] = {}

    class _Proc:
        returncode = 0

    def fake_run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        return _Proc()

    monkeypatch.setattr(pkg_commands.subprocess, "run", fake_run)
    rc = _run_mtk(monkeypatch, ["inference", "models", "list", "--remote"])
    assert rc == 0
    assert seen["cmd"] == ["uv", "run", "modal-inference", "models", "list", "--remote"]
    assert isinstance(seen["cwd"], Path) and seen["cwd"].name == "modal-inference-server"


def test_container_kind_builds_the_xdg_wrapped_command(repos: Path, monkeypatch):
    _write_manifest(
        repos,
        "modal-vault-server",
        """\
[defaults]
kind = "container"
app = "modal-vault-server"
xdg = "/vault/state"

[[command]]
name = "status"
summary = "sync state"
argv = ["ob", "sync-status", "--path", "/vault"]
""",
    )
    from toolkit import config as cfg

    cfg.write({"vault": {"base_url": "https://vault.example.com", "token": "t"}})
    observed: dict[str, str] = {}

    def fake_container_exec(app_name, base_url, command):
        observed.update(app=app_name, base=base_url, command=command)
        return 0

    monkeypatch.setattr(pkg_commands, "_container_exec", fake_container_exec)
    rc = _run_mtk(monkeypatch, ["vault", "status"])
    assert rc == 0
    assert observed["app"] == "modal-vault-server"
    assert observed["base"] == "https://vault.example.com"
    assert observed["command"] == "export XDG_CONFIG_HOME=/vault/state; exec ob sync-status --path /vault"
