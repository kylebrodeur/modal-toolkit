"""Modal Secrets management: declarative manifests + check + create (logic only).

Five family repos each ship one inert `server/secrets.toml` manifest declaring
the Modal Secrets their app consumes (key NAMES and generator kinds, never
values). This module is the only code that parses those manifests, and the only
toolkit code that talks to `modal secret`:

    mtk secrets check               # diff manifests against the Modal workspace
    mtk secrets create [--pkg ...]  # create missing secrets (generated or asked)

Guarantees:
  - Values are NEVER written to disk, logs, stdout, or stderr. The one
    unavoidable copy of a value is inside the single
    `modal secret create <name> K=v ...` argv handed to the spawned process.
  - Generated values come from `secrets.token_hex(32)` (CSPRNG, 64 hex chars);
    prompted values arrive via `getpass` (manifest hint shown, never echoed).
  - `check` is read-only: ONE workspace-scoped `modal secret list --json` per
    check_all() invocation regardless of how many packages are scanned.
  - Manifests stay inert: repo runtime code never reads them.
  - Drift: when repo code's committed secret-name default disagrees with the
    manifest name, check() names the disagreement instead of trusting either.

Package set: embedding, inference, vision, finetune, vault. `vault` needs the
same repo-name special case the per-package verbs already apply to finetune
(pkg "vault" -> repo "modal-vault-server").
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from toolkit import config as cfg

# The Modal invocation seam. Tests inject their fake shim either per args
# (create) or by patching this module attribute (check_all). Production runs
# `uvx modal` from the toolkit cwd; secrets are workspace-scoped, so nothing
# is per-repo and --env passthrough is NOT in v1.
MODAL_ARGV = ["uvx", "modal"]

# The packages whose repos ship manifests, in doctor order.
PACKAGES = ("embedding", "inference", "vision", "finetune", "vault", "coding")

# Repo-name special cases (pkg -> dir under the repos root). Everything else is
# the plain modal-<pkg>-server pattern. `coding` = modal-coding-inference (the
# private bespoke fleet core; the mci writing/coding lanes + shadow memory).
REPO_SPECIAL = {
    "finetune": "modal-finetune-server",
    "vault": "modal-vault-server",
    "coding": "modal-coding-inference",
}

# Generator kinds a manifest may request; "hex32" means `secrets.token_hex(32)`.
GENERATORS = ("hex32",)

# A secret-name constant whose DEFAULT the drift check reads. Both family
# idioms: `CONST = os.environ.get("ENV", "default")` / `os.getenv("ENV", "default")`,
# plus the plain `CONST = "default"`.
_CONST_DEFAULT_RE = re.compile(
    r"^\s*(?P<name>[A-Z][A-Z0-9_]+)\s*=\s*"
    r"(?:os\.(?:environ\.get|getenv)\([^,)]+,\s*)?"
    r"(?P<q>[\"'])(?P<value>[^\"']*)(?P=q)"
)

# A `Secret.from_name(<ARG>)` call site: ARG is a quoted literal or a constant.
_FROM_NAME_RE = re.compile(r"Secret\.from_name\(\s*([^)]+?)\s*\)")

# A fully quoted from_name argument (a literal name, not a constant).
_QUOTED_RE = re.compile(r"^(?P<q>[\"'])(?P<value>.*)(?P=q)$")


class ManifestError(Exception):
    """A manifest failed validation. `args` = (manifest_file, message)."""


@dataclass(frozen=True)
class KeySpec:
    env_var: str
    generate: str | None  # "hex32" when the tool generates the value
    ask: str | None  # prompt hint when the operator supplies it


@dataclass(frozen=True)
class ManifestSecret:
    secret_id: str  # manifest table id, e.g. "auth"
    name: str  # Modal secret name
    keys: dict[str, KeySpec]


@dataclass(frozen=True)
class ManifestSpec:
    repo: str
    secrets: list[ManifestSecret]


@dataclass(frozen=True)
class SecretStatus:
    secret_id: str
    name: str
    exists: bool
    drift: str | None


@dataclass(frozen=True)
class PackageStatus:
    pkg: str
    secrets: list[SecretStatus]
    manifest_found: bool
    missing: list[str] = field(default_factory=list)


# ── Modal seam (the only subprocess calls in this module) ───────────────────


def _run_modal(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """One modal subprocess run; a missing binary becomes a clean failure."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SystemExit(f"modal CLI not found ({argv[0]}): {exc}") from None


def _modal_secret_names(modal_argv: list[str]) -> set[str]:
    """Run modal_argv + ["secret", "list", "--json"], parse JSON, return {name, ...}.

    modal_argv: tests inject [fake_shim_path]; production: MODAL_ARGV. Accepts
    both {"names": [...]} and a bare list of {name: ...} objects (the shapes
    the Modal CLI has shipped across versions).
    """
    result = _run_modal([*modal_argv, "secret", "list", "--json"])
    if result.returncode != 0:
        raise SystemExit(f"modal secret list --json failed: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"modal secret list --json: unparseable JSON: {result.stdout[:200]!r}") from None
    items: object
    if isinstance(data, dict):
        items = data.get("names")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        raise SystemExit(f"modal secret list --json: unexpected shape: {result.stdout[:200]!r}")
    names: set[str] = set()
    for item in items:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def _modal_secret_create(modal_argv: list[str], name: str, kv_pairs: dict[str, str]) -> None:
    """Run modal_argv + ["secret", "create", name, "K=v", ...]; raise on non-zero exit.

    The caller must have verified the secret is missing (or decided --force
    applies); this function does not re-verify. The K=v argv is the only copy
    of the values anywhere in the toolkit.
    """
    argv = [*modal_argv, "secret", "create", name, *[f"{k}={v}" for k, v in kv_pairs.items()]]
    result = _run_modal(argv)
    if result.returncode != 0:
        raise SystemExit(f"modal secret create {name} failed: {result.stderr.strip()}")


# ── Manifest parsing ────────────────────────────────────────────────────────


def load_manifest(repo: Path) -> ManifestSpec | None:
    """Parse `<repo>/server/secrets.toml`; return None when the repo ships none.

    Raises ManifestError(file, message) on malformed TOML (including duplicate
    [secret.<id>] tables, which tomllib rejects), a missing or empty `name`,
    a missing or empty keys table, keys declaring neither or both of
    generate/ask, and unknown generator kinds.
    """
    path = repo / "server" / "secrets.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(str(path), f"invalid TOML: {exc}") from None

    table = data.get("secret")
    if not isinstance(table, dict) or not table:
        raise ManifestError(str(path), "missing [secret.<id>] tables")
    out: list[ManifestSecret] = []
    for secret_id, body in table.items():
        if not isinstance(body, dict):
            raise ManifestError(str(path), f"secret {secret_id!r} must be a table")
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ManifestError(str(path), f"secret {secret_id!r} missing non-empty name")
        keys_table = body.get("keys")
        if not isinstance(keys_table, dict) or not keys_table:
            raise ManifestError(str(path), f"secret {secret_id!r} missing non-empty keys table")
        keys: dict[str, KeySpec] = {}
        for env_var, key_body in keys_table.items():
            if not isinstance(key_body, dict):
                raise ManifestError(str(path), f"key {env_var!r} must be a table")
            generate = key_body.get("generate")
            ask = key_body.get("ask")
            if generate is None and ask is None:
                raise ManifestError(str(path), f"key {env_var!r} must declare generate or ask")
            if generate is not None and ask is not None:
                raise ManifestError(str(path), f"key {env_var!r} declares both generate and ask")
            if generate is not None and generate not in GENERATORS:
                raise ManifestError(
                    str(path),
                    f"key {env_var!r} unknown generator {generate!r}; supported kinds: {list(GENERATORS)}",
                )
            keys[env_var] = KeySpec(env_var=env_var, generate=generate, ask=ask)
        out.append(ManifestSecret(secret_id=secret_id, name=name, keys=keys))
    return ManifestSpec(repo=str(repo), secrets=out)


# ── Drift grep (code default vs manifest name) ──────────────────────────────


# Drift scan prunes these directory names from the rglob walk (venvs, caches,
# build output, hidden dirs); they carry third-party constants that would both
# flood the 200-file cap and false-positive the drift grep.
_SCAN_SKIP = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def _py_sources(repo_path: Path) -> list[Path]:
    """The repo's runtime Python sources: <repo>/server/**/*.py, pruned + capped."""
    server = repo_path / "server"
    if not server.is_dir():
        return []

    def _ok(path: Path) -> bool:
        return not any(part in _SCAN_SKIP or part.startswith(".") for part in path.parts)

    return sorted(p for p in server.rglob("*.py") if "tests" not in p.parts and _ok(p))[:200]


def _code_constant_defaults(repo_path: Path) -> dict[str, str]:
    """{CONST_NAME: default} for secret-name constants in the repo's server code."""
    found: dict[str, str] = {}
    for py in _py_sources(repo_path):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            m = _CONST_DEFAULT_RE.match(line)
            if m:
                found.setdefault(m.group("name"), m.group("value"))
    return found


def _from_name_args(repo_path: Path) -> list[str]:
    """Unquoted arguments of every `Secret.from_name(<ARG>)` site (literals + consts).

    `Secret.from_name(config.AUTH_SECRET_NAME)` normalizes to AUTH_SECRET_NAME.
    """
    args: list[str] = []
    for py in _py_sources(repo_path):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _FROM_NAME_RE.finditer(text):
            arg = m.group(1).strip()
            if "." in arg:
                arg = arg.split(".")[-1]
            args.append(arg)
    return args


def _secret_sites(repo_path: Path) -> list[tuple[str, str | None]]:
    """One entry per code reference a drift check could attribute to a secret:
    (ref_name, default_or_none). A reference is either a `Secret.from_name`
    literal ("value", no default) or a constant consumed via from_name whose
    default resolves from the repo's `CONST = ...get/getenv(...)` lines.
    """
    defaults = _code_constant_defaults(repo_path)
    sites: list[tuple[str, str | None]] = []
    for arg in _from_name_args(repo_path):
        q = _QUOTED_RE.match(arg)
        if q:
            sites.append((q.group("value"), None))
        elif arg in defaults:
            sites.append((arg, defaults[arg]))
    return sites


def _drift_for_secret(repo_path: Path, secret: ManifestSecret, *, manifest: ManifestSpec) -> str | None:
    """Drift text when the repo's committed name for THIS secret disagrees.

    Attribution: each code site names at most one secret. The whole manifest's
    sites are partitioned first, then a secret drifts only when NO site in the
    repo resolves to its manifest name AND at least one site is unclaimed by
    any other manifest secret (the disagreement is then about this secret).
    A secret with no attributable site: existence-only (allowed forward gap).
    """
    sites = _secret_sites(repo_path)
    if any(value == secret.name or ref == secret.name for ref, value in sites):
        return None  # some code site names this very secret: consistent

    claimed_by_other = {
        other.name
        for other in manifest.secrets
        if other is not secret
        for ref, value in _sites_covering(sites, other.name)
    }
    unclaimed = [(ref, value) for ref, value in sites if ref not in claimed_by_other and value not in claimed_by_other]
    if not unclaimed:
        return None  # every site belongs to another manifest secret

    # The unclaimed sites disagree about THIS secret; name the strongest one.
    for ref, value in unclaimed:
        if value is not None:
            return f"code declares {ref} default '{value}' but manifest says {secret.name}"
    ref, _value = unclaimed[0]
    return f"code consumes Secret.from_name('{ref}') but manifest says {secret.name}"


def _sites_covering(sites: list[tuple[str, str | None]], name: str) -> list[tuple[str, str | None]]:
    """The sites whose literal or default equals `name` (they claim that secret)."""
    return [(ref, value) for ref, value in sites if ref == name or value == name]


def _pkg_for_repo(repo_path: Path) -> str:
    """Package id for a sibling repo directory name (modal-<pkg>-server)."""
    name = repo_path.name
    for pkg, repo_name in REPO_SPECIAL.items():
        if name == repo_name:
            return pkg
    prefix, suffix = "modal-", "-server"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)]
    return name


def _pkg_repo(pkg: str) -> Path:
    """Repo path for a package id (vault and finetune map to their long names)."""
    repo_name = REPO_SPECIAL.get(pkg, f"modal-{pkg}-server")
    return cfg.repos_root() / repo_name


# ── check ───────────────────────────────────────────────────────────────────


def check(manifest: ManifestSpec, existing_names: set[str], repo_path: Path) -> PackageStatus:
    """Diff one manifest against the workspace's secret-name set. Read-only.

    Per-secret status: exists = name in existing_names; drift text (see
    _drift_for_secret) when the repo's committed default disagrees with the
    manifest name:
        "code declares <CONST_NAME> default '<other-name>' but manifest says <name>".
    """
    statuses: list[SecretStatus] = []
    missing: list[str] = []
    for secret in manifest.secrets:
        exists = secret.name in existing_names
        statuses.append(
            SecretStatus(
                secret_id=secret.secret_id,
                name=secret.name,
                exists=exists,
                drift=_drift_for_secret(repo_path, secret, manifest=manifest),
            )
        )
        if not exists:
            missing.append(secret.name)
    return PackageStatus(pkg=_pkg_for_repo(repo_path), secrets=statuses, manifest_found=True, missing=missing)


def check_all(pkg_filter: list[str] | None = None) -> dict[str, PackageStatus]:
    """Doctor seam: {pkg: PackageStatus} across the manifest-bearing packages.

    ONE `modal secret list --json` call per invocation regardless of package
    count (workspace-scoped). Packages without a manifest (or without a repo
    checkout) report manifest_found=False with no statuses. Read-only.
    """
    existing = _modal_secret_names(MODAL_ARGV)
    wanted = [p for p in PACKAGES if pkg_filter is None or p in pkg_filter]
    out: dict[str, PackageStatus] = {}
    for pkg in wanted:
        repo = _pkg_repo(pkg)
        spec = load_manifest(repo)
        if spec is None:
            out[pkg] = PackageStatus(pkg=pkg, secrets=[], manifest_found=False, missing=[])
            continue
        out[pkg] = check(spec, existing, repo)
    return out


# ── create ──────────────────────────────────────────────────────────────────


def _wanted_packages(args: argparse.Namespace) -> list[str]:
    """The package list a create invocation targets: --all, --pkg list, or every package."""
    if getattr(args, "all", False):
        return list(PACKAGES)
    pkg_cli = getattr(args, "pkg", None)
    if isinstance(pkg_cli, str):
        return [p for p in pkg_cli.split(",") if p]
    if pkg_cli:
        return list(pkg_cli)
    return list(PACKAGES)


def create(args: argparse.Namespace) -> int:
    """Verb body for `mtk secrets create` (spec section 2).

    One workspace list call up front, then per (package, secret):
      - missing: generate values (`token_hex(32)`) or prompt via getpass with
        the manifest hint, then one `modal secret create <name> K=v ...`.
      - existing without --force: refuse (non-zero exit at the end, no argv).
      - existing with --force: regenerate ALL keys (printed clearly, because
        it invalidates live deployments).
    After creating, re-run the check and print the per-package table.
    Values never appear in stdout, stderr, logs, or on disk.
    """
    argv: list[str] = list(getattr(args, "modal_argv", None) or MODAL_ARGV)
    wanted = _wanted_packages(args)

    plan: list[tuple[str, ManifestSpec]] = []
    for pkg in wanted:
        spec = load_manifest(_pkg_repo(pkg))
        if spec is not None:
            plan.append((pkg, spec))
    if not plan:
        print("no manifests found; check repos root ($MODAL_TOOLKIT_REPOS / config repos.root)", file=sys.stderr)
        return 1

    existing = _modal_secret_names(argv)

    refusals = 0
    for pkg, spec in plan:
        for secret in spec.secrets:
            exists = secret.name in existing
            if exists and not args.force:
                # --force refusal: an existing secret is never touched without
                # the flag; the caller must rerun with --force to regenerate.
                refusals += 1
                print(
                    f"{pkg:10s}  {secret.name} already exists; refusing without --force",
                    file=sys.stderr,
                )
                continue
            if exists and args.force:
                print(
                    f"{pkg:10s}  {secret.name} exists; --force regenerates ALL of its keys "
                    "(live deployments must switch to the new values)",
                    file=sys.stderr,
                )
            kv: dict[str, str] = {}
            for env_var, key_spec in secret.keys.items():
                if key_spec.generate == "hex32":
                    kv[env_var] = secrets.token_hex(32)
                else:
                    hint = key_spec.ask or env_var
                    kv[env_var] = getpass.getpass(f"    {secret.name} {env_var} ({hint}): ")
            _modal_secret_create(argv, secret.name, kv)

    # Spec section 2: after create, re-check and print the table.
    statuses: dict[str, PackageStatus] = {}
    for pkg, spec in plan:
        repo = _pkg_repo(pkg)
        statuses[pkg] = check(spec, _modal_secret_names(argv), repo)
    _print_table(statuses)
    return 0 if refusals == 0 else 1


def _print_table(statuses: dict[str, PackageStatus]) -> None:
    """Human table: one line per package; missing list under the line."""
    for status in statuses.values():
        if not status.manifest_found:
            print(f"{status.pkg:10s}  manifest absent")
            continue
        marks: list[str] = []
        for s in status.secrets:
            if s.drift:
                mark = f"drift ({s.drift})"
            elif s.exists:
                mark = "ok"
            else:
                mark = f"missing {s.name}"
            marks.append(mark)
        print(f"{status.pkg:10s}  {'; '.join(marks)}")
        if status.missing:
            print(f"           missing: {', '.join(status.missing)}")


# ── Test-facing parser (toolkit/cli.py wiring lands in Task 3) ──────────────


def build_parser() -> argparse.ArgumentParser:
    """Standalone `mtk secrets` parser: create + --force semantics end to end.

    cli.py attaches its own copy with `set_defaults(func=secrets.create)` in
    Task 3; this one exists so tests can exercise argv shapes without the CLI.
    """
    p = argparse.ArgumentParser(prog="mtk secrets")
    sub = p.add_subparsers(dest="subcommand", required=True)
    check_p = sub.add_parser("check", help="diff manifests against the Modal workspace (read-only)")
    check_p.add_argument("--pkg", help="comma-separated package names (default: all)")
    create_p = sub.add_parser("create", help="create missing secrets (generated or prompted)")
    create_p.add_argument("--pkg", help="comma-separated package names (default: all)")
    create_p.add_argument("--all", action="store_true", help="every manifest-bearing package")
    create_p.add_argument(
        "--force",
        action="store_true",
        help="regenerate ALL keys of existing secrets (live deployments must switch)",
    )
    create_p.set_defaults(func=create)
    return p
