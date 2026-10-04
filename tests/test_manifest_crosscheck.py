"""Task 5 TDD tests: manifest vs. code cross-check across the five sibling repos.

EXPECTED_* tables are INTENTIONAL pin-downs: a manifest/code change in any
sibling repo turns this suite RED until the table is updated IN THE SAME
COMMIT as the change. A red run here is drift being caught loudly (by
design), not a toolkit bug.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the toolkit package relative to the repo root (mirrors test_secrets.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import secrets as mtk_secrets

EXPECTED_REPO = {
    "embedding": "modal-embedding-server",
    "inference": "modal-inference-server",
    "vision": "modal-vision-server",
    "finetune": "modal-finetune-server",  # REPO_SPECIAL short name
    "vault": "modal-vault-server",  # REPO_SPECIAL long name
}

# Operative names pinned (progress.md 2026-10-07): manifest `name` == the
# `SECRET_NAME`-family constant default the repo's server code actually
# consumes via Secret.from_name. Vision's config.py carries a dead
# vision-auth-secret default (app.py's hardcoded modal-vision-secret wins).
EXPECTED_MANIFEST_NAMES = {
    "embedding": ["embedding-auth", "huggingface-secret"],
    "inference": ["inference-auth-secret", "modal-inference-server-dashboard", "modal-inference-server-huggingface"],
    "vision": ["modal-vision-secret"],
    "finetune": ["modal-finetune-secrets", "HF_TOKEN"],
    "vault": ["modal-vault-secret"],
}

# Code-side truth (server/*.py grep, 2026-10-07): const name -> operative
# default. Vision's dead vision-auth-secret default is excluded per T1 ruling.
EXPECTED_CODE_DEFAULTS: dict[str, dict[str, str]] = {
    "embedding": {"AUTH_SECRET_NAME": "embedding-auth", "HF_SECRET_NAME": "huggingface-secret"},
    "inference": {
        "AUTH_SECRET_NAME": "inference-auth-secret",
        "HF_SECRET_NAME": "modal-inference-server-huggingface",
    },
    # app.py hardcodes the const (config.py's vision-auth-secret default is
    # dead); the operative name is the app.py value.
    "vision": {"AUTH_SECRET_NAME": "modal-vision-secret"},
    "finetune": {},
    "vault": {"SECRET_NAME": "modal-vault-secret"},
}

# from_name literals per repo (server/*.py, deduped), in the raw quoted form
# `_from_name_args` emits. Vault's from_name consumes the SECRET_NAME constant;
# inference's dashboard name is a double-quoted site literal (line 619).
EXPECTED_FROM_NAME_LITERALS: dict[str, set[str]] = {
    "embedding": set(),
    "inference": {'"modal-inference-server-dashboard"'},
    "vision": set(),
    "finetune": {'"modal-finetune-secrets"', '"HF_TOKEN"'},
    "vault": set(),
}


def _sibling_repo(pkg: str) -> Path:
    """The sibling checkout for a package (discovery via `_pkg_repo` mapping)."""
    repo = mtk_secrets._pkg_repo(pkg)
    assert repo.name == EXPECTED_REPO[pkg], f"repo map drifted for {pkg}: {repo.name}"
    return repo


def _sibling_or_skip(pkg: str) -> Path:
    """Sibling checkout, or pytest.skip when this checkout runs standalone.

    CI clones ONLY the toolkit repo: the five sibling checkouts exist on the
    operator's machine, not on the runner, so every real-repo test here
    clean-skips there and runs only where the family is actually present.
    """
    repo = _sibling_repo(pkg)
    if not repo.is_dir():
        pytest.skip(f"sibling checkout absent in this environment: {repo}")
    return repo


@pytest.mark.parametrize("pkg", sorted(EXPECTED_REPO))
def test_sibling_checkout_present(pkg: str):
    """All five sibling checkouts sit under the configured repos root.

    Operator-machine requirement only: clean-skip in CI or any standalone
    clone (the assertion below holds where the family lives on disk).
    """
    repo = _sibling_repo(pkg)
    if not repo.is_dir():
        pytest.skip(f"sibling checkout absent in this environment: {repo}")
    assert repo.is_dir()


@pytest.mark.parametrize("pkg", sorted(EXPECTED_REPO))
def test_manifest_names_match_code_constants(pkg: str):
    """Manifest `name` == the repo's operative SECRET_NAME constant default.

    Per repo: manifest + parse (Task 5 brief); assert every manifest `name`
    equals the code constant default, and assert every `from_name` site in the
    repo names a manifest secret (no undeclared secret consumption).
    """
    repo = _sibling_or_skip(pkg)
    spec = mtk_secrets.load_manifest(repo)
    assert spec is not None, f"{repo}/server/secrets.toml missing"
    assert [s.name for s in spec.secrets] == EXPECTED_MANIFEST_NAMES[pkg]

    consts = mtk_secrets._code_constant_defaults(repo)
    for const_name, expected_default in EXPECTED_CODE_DEFAULTS[pkg].items():
        assert consts.get(const_name) == expected_default, (
            f"{repo.name}: const {const_name} = {consts.get(const_name)!r}, want {expected_default!r}"
        )

    # Every from_name site in the repo names a manifest secret: literals
    # directly; constants through their operative default. `_from_name_args`
    # keeps literals quoted (as the site was written); strip to the bare name.
    manifest_names = {s.name for s in spec.secrets}
    for arg in mtk_secrets._from_name_args(repo):
        const_default = EXPECTED_CODE_DEFAULTS[pkg].get(arg)
        name = const_default if const_default is not None else arg.strip("\"'")
        assert name in manifest_names, f"{repo.name}: undeclared from_name site {arg!r}"
    # The expected literals really show up in the repo's from_name args.
    for lit in EXPECTED_FROM_NAME_LITERALS[pkg]:
        assert lit in mtk_secrets._from_name_args(repo), f"{repo.name}: literal {lit!r} absent"


@pytest.mark.parametrize("pkg", sorted(EXPECTED_REPO))
def test_code_defaults_agree_with_manifest_names(pkg: str):
    """Each manifest secret's name appears as some code site's ref or default.

    This is the drift check's own attribution logic (Task 1 ruling): every
    manifest secret that the repo's server code can attribute must agree; a
    secret with no attributable site is existence-only and stays allowed
    (inference's auth secret has NO from_name site today: forward gap).
    """
    repo = _sibling_or_skip(pkg)
    spec = mtk_secrets.load_manifest(repo)
    assert spec is not None
    status = mtk_secrets.check(spec, {s.name for s in spec.secrets}, repo)
    assert status.manifest_found is True
    assert status.missing == []
    for sec in status.secrets:
        # Any drift text at all is a cross-check failure; with the current
        # repos every entry must come back clean. (Inference's auth secret
        # has NO from_name site today — existence-only, not drift.)
        assert sec.drift is None, f"{pkg}/{sec.secret_id}: {sec.drift}"


@pytest.mark.parametrize("pkg", sorted(EXPECTED_REPO))
def test_drift_scan_prunes_caches_and_venvs(pkg: str):
    """The pruning contract survives the real trees (Task 2's venv/vendor rule)."""
    repo = _sibling_or_skip(pkg)
    for py in mtk_secrets._py_sources(repo):
        parts = py.relative_to(repo).parts
        assert not any(p in mtk_secrets._SCAN_SKIP or p.startswith(".") for p in parts), (
            f"{py}: pruned dir leaked into the scan"
        )
        assert "tests" not in py.relative_to(repo).parts, f"{py}: tests leaked into the scan"
