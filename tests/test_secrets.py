"""Tests for toolkit.secrets: manifest parsing, check, and create flows.

All Modal interaction goes through an executable fake `modal` shim: the shim
appends one argv line per invocation to $MTK_FAKE_MODAL_LOG and, for
`secret list --json`, emits {"names": [...]} JSON built from
$MTK_FAKE_MODAL_NAMES. Nothing here touches the network or a real account.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

# Import the toolkit package relative to the repo root (mirrors test_config.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import secrets as mtk_secrets

# The token generation seam is patched per test with one fixed value so tests
# can assert "inside the create argv" vs "absent from output" deterministically.
TOKEN = "0f" * 32

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


MANIFEST_OK = """\
[secret.auth]
name = "vault-auth"
[secret.auth.keys.VAULT_API_TOKEN]
generate = "hex32"
[secret.hf]
name = "vault-hf"
[secret.hf.keys.HF_TOKEN]
ask = "HF token, read"
"""

# Generate-only manifest (no prompts), for create-flow tests.
MANIFEST_GEN = """\
[secret.auth]
name = "vault-auth"
[secret.auth.keys.VAULT_API_TOKEN]
generate = "hex32"
"""

# Ask-only manifest (no generation) mirroring the real finetune layout.
ASK_MANIFEST = """\
[secret.hf]
name = "asked-secret"
[secret.hf.keys.HF_TOKEN]
ask = "some hint"
"""

# Generate-only manifest for the embedding package (distinct names for fan-out tests).
EMBED_MANIFEST = """\
[secret.auth]
name = "embedding-auth"
[secret.auth.keys.API_TOKEN]
generate = "hex32"
"""

FINETUNE_MANIFEST = """\
[secret.main]
name = "modal-finetune-secrets"
[secret.main.keys.HF_TOKEN]
ask = "Hugging Face token: Hub upload + gated base-model access"
[secret.hf]
name = "HF_TOKEN"
[secret.hf.keys.HF_TOKEN]
ask = "gguf pipeline fallback, mirrors main"
"""


def _manifest_dir(root: Path, body: str) -> Path:
    (root / "server").mkdir(parents=True)
    (root / "server" / "secrets.toml").write_text(body, encoding="utf-8")
    return root


def _spec(tmp_path: Path, body: str = MANIFEST_OK, repo_dir: str = "modal-x-server") -> Path:
    return _manifest_dir(tmp_path / repo_dir, body)


def _args(**kw):
    base = {"pkg": ["vault"], "all": False, "force": False, "modal_argv": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _create_lines(log_path: Path) -> list[str]:
    return [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if "create" in ln]


# ── load_manifest ────────────────────────────────────────────────────────────


def test_load_manifest_happy(tmp_path: Path):
    from toolkit.secrets import load_manifest

    repo = _manifest_dir(tmp_path, MANIFEST_OK)
    spec = load_manifest(repo)
    assert spec.repo == str(repo)
    assert [s.secret_id for s in spec.secrets] == ["auth", "hf"]
    assert spec.secrets[0].name == "vault-auth"
    assert spec.secrets[0].keys["VAULT_API_TOKEN"].generate == "hex32"
    assert spec.secrets[0].keys["VAULT_API_TOKEN"].ask is None
    assert spec.secrets[1].keys["HF_TOKEN"].generate is None
    assert spec.secrets[1].keys["HF_TOKEN"].ask == "HF token, read"


def test_load_manifest_real_sibling_fixture(monkeypatch: pytest.MonkeyPatch):
    # A real manifest ships inert; parsing it must yield the pinned names.
    from toolkit.secrets import load_manifest

    real = Path("/Users/kylebrodeur/workspace/modal-embedding-server")
    if not (real / "server" / "secrets.toml").exists():
        pytest.skip("sibling repo checkout not present")
    spec = load_manifest(real)
    assert [s.name for s in spec.secrets] == ["embedding-auth", "huggingface-secret"]


def test_load_manifest_absent_is_none(tmp_path: Path):
    from toolkit.secrets import load_manifest

    assert load_manifest(tmp_path) is None


@pytest.mark.parametrize(
    "body",
    [
        "name = [unclosed",  # bad TOML
        '[secret.auth]\n[secret.auth.keys.K]\ngenerate = "hex32"\n',  # missing name
        (
            '[secret.auth]\nname = "a"\n[secret.auth.keys.K]\ngenerate = "hex32"\n[secret.auth]\nname = "b"\n'
        ),  # duplicate id (tomllib rejects; surfaced as ManifestError)
        '[secret.auth]\n[secret.auth.keys.K]\ngenerate = "rot13"\n',  # unknown kind
        '[secret.auth]\nname = "x"\n',  # secret with no keys table
        '[secret.auth]\nname = "x"\n[secret.auth.keys.K]\n',  # key with neither kind
        (
            '[secret.auth]\nname = "x"\n[secret.auth.keys.K]\ngenerate = "hex32"\nask = "because"\n'
        ),  # key with both kinds
    ],
)
def test_load_manifest_errors_carry_file_context(tmp_path: Path, body: str):
    from toolkit.secrets import ManifestError

    repo = _manifest_dir(tmp_path, body)
    with pytest.raises(ManifestError) as ci:
        mtk_secrets.load_manifest(repo)
    assert "secrets.toml" in str(ci.value)


# ── check ────────────────────────────────────────────────────────────────────


def test_check_reports_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo = _spec(tmp_path)
    spec = mtk_secrets.load_manifest(repo)
    assert spec is not None
    status = mtk_secrets.check(spec, {"unrelated"}, repo)
    assert status.pkg == "x"
    assert status.manifest_found is True
    assert [s.exists for s in status.secrets] == [False, False]
    assert status.missing == ["vault-auth", "vault-hf"]


def test_check_happy_all_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo = _spec(tmp_path)
    spec = mtk_secrets.load_manifest(repo)
    assert spec is not None
    status = mtk_secrets.check(spec, {"vault-auth", "vault-hf"}, repo)
    assert [s.exists for s in status.secrets] == [True, True]
    assert all(s.drift is None for s in status.secrets)
    assert status.missing == []


def test_check_drift_on_wrong_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo_dir = tmp_path / "modal-vault-server"
    (repo_dir / "server").mkdir(parents=True)
    (repo_dir / "server" / "secrets.toml").write_text(MANIFEST_GEN, encoding="utf-8")
    # A consumed constant whose default disagrees with the manifest name.
    (repo_dir / "server" / "app.py").write_text(
        'SECRET_NAME = os.environ.get("V", "code-default-name")\nvault_secret = modal.Secret.from_name(SECRET_NAME)\n',
        encoding="utf-8",
    )
    spec = mtk_secrets.load_manifest(repo_dir)
    assert spec is not None
    status = mtk_secrets.check(spec, {"vault-auth"}, repo_dir)
    auth = status.secrets[0]
    assert auth.exists is True
    assert auth.drift is not None
    assert "SECRET_NAME" in auth.drift
    assert "code-default-name" in auth.drift
    assert "vault-auth" in auth.drift


def test_check_no_drift_when_no_code_reference_at_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # A repo whose code never references ANY secret: no drift signal for any
    # manifest secret (existence-only, allowed forward gap per Task 1 ruling).
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo_dir = tmp_path / "modal-inference-server"
    (repo_dir / "server").mkdir(parents=True)
    (repo_dir / "server" / "secrets.toml").write_text(MANIFEST_OK, encoding="utf-8")
    (repo_dir / "server" / "app.py").write_text(
        "x = 1\nprint(x)\n",  # no secret-name constants or from_name sites
        encoding="utf-8",
    )
    spec = mtk_secrets.load_manifest(repo_dir)
    assert spec is not None
    status = mtk_secrets.check(spec, {"vault-auth", "vault-hf"}, repo_dir)
    for s in status.secrets:
        assert s.drift is None, s.drift


def test_check_drift_ignores_venv_and_vendor_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # A .venv full of third-party constants must not blind or derange the scan.
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo_dir = tmp_path / "modal-vault-server"
    (repo_dir / "server" / ".venv" / "lib").mkdir(parents=True)
    (repo_dir / "server" / "secrets.toml").write_text(MANIFEST_GEN, encoding="utf-8")
    (repo_dir / "server" / ".venv" / "lib" / "vendor.py").write_text(
        'SOME_CONST = os.environ.get("X", "modal-vendor-noise")\n', encoding="utf-8"
    )
    (repo_dir / "server" / "app.py").write_text(
        'SECRET_NAME = os.environ.get("V", "code-default-name")\nvault_secret = modal.Secret.from_name(SECRET_NAME)\n',
        encoding="utf-8",
    )
    spec = mtk_secrets.load_manifest(repo_dir)
    assert spec is not None
    status = mtk_secrets.check(spec, {"vault-auth"}, repo_dir)
    assert status.secrets[0].drift is not None
    assert "code-default-name" in status.secrets[0].drift


def test_check_drift_supports_getenv_idiom(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    repo_dir = tmp_path / "modal-inference-server"
    (repo_dir / "server").mkdir(parents=True)
    (repo_dir / "server" / "secrets.toml").write_text(MANIFEST_GEN, encoding="utf-8")
    (repo_dir / "server" / "app.py").write_text(
        'SECRET_NAME = os.getenv("V", "code-default-name")\ns = modal.Secret.from_name(SECRET_NAME)\n',
        encoding="utf-8",
    )
    spec = mtk_secrets.load_manifest(repo_dir)
    assert spec is not None
    status = mtk_secrets.check(spec, {"vault-auth"}, repo_dir)
    assert status.secrets[0].drift is not None
    assert "os.getenv" not in status.secrets[0].drift  # the default, not the idiom, is quoted
    assert "code-default-name" in status.secrets[0].drift


def test_check_absent_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    (tmp_path / "modal-vision-server" / "server").mkdir(parents=True)  # repo, no manifest
    statuses = mtk_secrets.check_all(["vision"])
    assert set(statuses) == {"vision"}
    assert statuses["vision"].manifest_found is False
    assert statuses["vision"].secrets == []


# ── check_all: the doctor seam (ONE modal list call per invocation) ─────────


def test_check_all_one_shim_call_regardless_of_package_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path
):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    for pkg in ("embedding", "inference", "vision", "finetune", "vault"):
        _spec(tmp_path, repo_dir=f"modal-{pkg}-server")
    # `coding` maps to the special-case repo (modal-coding-inference, not
    # modal-coding-server): it reports manifest-absent (which is honest for a
    # bare tmp_path fixture) but must still KEY into the result set.
    out = mtk_secrets.check_all()
    assert set(out) == {"embedding", "inference", "vision", "finetune", "vault", "coding"}
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len([ln for ln in lines if "list" in ln and "--json" in ln]) == 1


def test_check_all_vault_pkg_maps_to_vault_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets, "MODAL_ARGV", [str(shim_path)])
    (tmp_path / "modal-vault-server" / "server").mkdir(parents=True)
    (tmp_path / "modal-vault-server" / "server" / "secrets.toml").write_text(MANIFEST_OK, encoding="utf-8")
    out = mtk_secrets.check_all(["vault"])
    assert out["vault"].pkg == "vault"
    assert out["vault"].manifest_found is True


# ── create ───────────────────────────────────────────────────────────────────


def _vault_repo(tmp_path: Path, body: str = MANIFEST_GEN) -> Path:
    return _spec(tmp_path, body=body, repo_dir="modal-vault-server")


def test_create_generates_and_records_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path
):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _vault_repo(tmp_path)
    args = _args(modal_argv=[str(shim_path)])
    rc = mtk_secrets.create(args)
    assert rc == 0
    calls = _create_lines(log_path)
    assert len(calls) == 1
    assert "secret create vault-auth" in calls[0]
    assert f"VAULT_API_TOKEN={TOKEN}" in calls[0]


def test_create_asked_keys_use_getpass_and_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path, capsys
):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    # No secrets exist at the workspace: both asked secrets prompt.
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _spec(tmp_path, body=FINETUNE_MANIFEST, repo_dir="modal-finetune-server")
    args = _args(pkg=["finetune"], modal_argv=[str(shim_path)])
    captured: list[str] = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": (captured.append(prompt), "hf_real_token")[1])
    rc = mtk_secrets.create(args)
    assert rc == 0
    # The manifest hint reaches the user as the prompt.
    assert any("Hub upload" in p for p in captured)
    assert any("gguf pipeline fallback" in p for p in captured)
    calls = _create_lines(log_path)
    assert any("HF_TOKEN=hf_real_token" in ln for ln in calls)
    # The asked value is for the operator's ears only: never printed.
    out = capsys.readouterr()
    assert "hf_real_token" not in out.out
    assert "hf_real_token" not in out.err


def test_create_force_refusal_without_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path
):
    # --force refusal: an existing secret targeted for create refuses without
    # the flag - exit non-zero, and NO create argv is handed to modal.
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"vault-auth"')
    _vault_repo(tmp_path)
    args = _args(modal_argv=[str(shim_path)], force=False)
    rc = mtk_secrets.create(args)
    assert rc != 0
    assert _create_lines(log_path) == []


def test_create_force_flag_allows_regenerate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path
):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"vault-auth"')
    _vault_repo(tmp_path)
    args = _args(modal_argv=[str(shim_path)], force=True)
    rc = mtk_secrets.create(args)
    assert rc == 0
    calls = _create_lines(log_path)
    assert len(calls) == 1
    assert f"VAULT_API_TOKEN={TOKEN}" in calls[0]


def test_create_tokens_never_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path, capsys
):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _vault_repo(tmp_path)
    rc = mtk_secrets.create(_args(modal_argv=[str(shim_path)]))
    assert rc == 0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err
    # The argv log is a test fixture; the token may appear ONLY in the create line.
    log_text = log_path.read_text(encoding="utf-8")
    argv_line = _create_lines(log_path)[0]
    assert f"VAULT_API_TOKEN={TOKEN}" in argv_line
    assert TOKEN not in log_text.replace(argv_line, "")


def test_create_pkg_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path):
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    for pkg in ("embedding", "vault"):
        _spec(tmp_path, body=MANIFEST_GEN, repo_dir=f"modal-{pkg}-server")
    mtk_secrets.create(_args(pkg=["vault"], modal_argv=[str(shim_path)]))
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert any("vault-auth" in ln for ln in lines)
    assert not any("embedding-auth" in ln for ln in lines)


# ── Modal seam shape + failure tolerance ────────────────────────────────────


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """A fake _run_modal result for seam shape tests."""
    return lambda argv: argparse.Namespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_modal_secret_names_accepts_bare_list_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log_path: Path):
    # The real Modal CLI emits a bare list of {"name": ...} objects; the
    # {"names": [...]} shape stays supported for the shim and older versions.
    monkeypatch.setattr(mtk_secrets, "_run_modal", _fake_run(stdout='[{"name": "a"}, {"name": "b"}]'))
    assert mtk_secrets._modal_secret_names(["fake"]) == {"a", "b"}


def test_modal_secret_names_accepts_names_key_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mtk_secrets, "_run_modal", _fake_run(stdout='{"names": ["a", "b"]}'))
    assert mtk_secrets._modal_secret_names(["fake"]) == {"a", "b"}


def test_modal_secret_names_fails_on_bad_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mtk_secrets, "_run_modal", _fake_run(stdout="not json"))
    with pytest.raises(SystemExit, match="unparseable"):
        mtk_secrets._modal_secret_names(["fake"])


def test_modal_secret_names_fails_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mtk_secrets, "_run_modal", _fake_run(returncode=1, stderr="boom"))
    with pytest.raises(SystemExit, match="boom"):
        mtk_secrets._modal_secret_names(["fake"])


def test_modal_secret_create_fails_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mtk_secrets, "_run_modal", _fake_run(returncode=1, stderr="boom"))
    with pytest.raises(SystemExit, match="secret create"):
        mtk_secrets._modal_secret_create(["fake"], "n", {"K": "V"})


def test_missing_modal_binary_becomes_clean_systemexit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _throw(argv, **kw):
        raise FileNotFoundError(2, "No such file", argv[0])

    monkeypatch.setattr(mtk_secrets.subprocess, "run", _throw)
    with pytest.raises(SystemExit, match="modal CLI not found"):
        mtk_secrets._modal_secret_names([str(tmp_path / "nope")])


# ── create robustness ───────────────────────────────────────────────────────


def test_create_without_any_manifest_is_rc1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # No repos at all: a clean rc 1 with a pointer at the repos root, and no
    # modal subprocess ever spawned (plan is built before the list call).
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path / "nowhere"))
    spawned = []
    monkeypatch.setattr(mtk_secrets, "_modal_secret_names", lambda argv: spawned.append(argv) or set())
    rc = mtk_secrets.create(_args(pkg=None, modal_argv=None))
    assert rc == 1
    assert spawned == []


def test_create_mixed_plan_refuses_only_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path
):
    # Two packages: vault's secret exists (refusal rc), embedding's missing one
    # is still created in the same run. Distinct names keep the cases separate.
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", '"vault-auth"')
    for pkg in ("embedding", "vault"):
        _spec(tmp_path, body=MANIFEST_OK if pkg == "vault" else EMBED_MANIFEST, repo_dir=f"modal-{pkg}-server")
    # Both manifests contain asked keys: stub getpass so nothing prompts for real.
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "hf_real_token")
    rc = mtk_secrets.create(_args(pkg=None, modal_argv=[str(shim_path)]))
    assert rc != 0
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert any("secret create embedding-auth" in ln for ln in lines)
    assert not any("secret create vault-auth" in ln for ln in lines)


def test_build_parser_create_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shim_path: Path, log_path: Path):
    # The test-facing parser wires create -> args.func; exercise argv end to end.
    monkeypatch.setenv("MODAL_TOOLKIT_REPOS", str(tmp_path))
    monkeypatch.setattr(mtk_secrets.secrets, "token_hex", lambda nbytes: TOKEN)
    monkeypatch.setenv("MTK_FAKE_MODAL_NAMES", "")
    _vault_repo(tmp_path)
    parser = mtk_secrets.build_parser()
    args = parser.parse_args(["create", "--pkg", "vault"])
    args.modal_argv = [str(shim_path)]
    rc = args.func(args)
    assert rc == 0
    assert any("secret create vault-auth" in ln for ln in log_path.read_text(encoding="utf-8").splitlines())
