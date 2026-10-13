"""Project scaffolding for `mtk` (U9): init, the preflight, and the project record.

Account rule: init is read-only against Modal. It checks the active profile and
reads the app list, and it refuses an app-name collision. It creates nothing
in the account. The runner is injected so tests never touch a real account.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The family packages. A project's apps compose as <slug>-<pkg>.
FAMILY_PACKAGES = ("embedding", "inference", "vision", "finetune", "vault")

# Modal app names: lowercase letters, digits, dashes; start with a letter or digit.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

RECORD_DIR = ".mtk"
RECORD_FILE = "config.json"

Runner = Callable[[list[str]], tuple[int, str]]


class ProjectError(Exception):
    """A refusal with the exact fix in the message."""


def _default_run(argv: list[str]) -> tuple[int, str]:
    """Run a command; return (exit code, stdout). A missing binary is rc 127."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60)
    except FileNotFoundError:
        return 127, ""
    return result.returncode, result.stdout


def check_prerequisites(_run: Runner = _default_run) -> list[dict[str, Any]]:
    """Check uv, git, the modal CLI, the active profile, and the account.

    Returns the account's app list (rows with `description` and `state`) so the
    caller can run the collision check without a second account call.
    """
    for tool, fix in (
        ("uv", "install uv: https://docs.astral.sh/uv/"),
        ("git", "install git from your package manager"),
        ("modal", "uv tool install modal"),
    ):
        rc, _ = _run([tool, "--version"])
        if rc != 0:
            raise ProjectError(f"{tool} is not available on PATH. Fix: {fix}")

    rc, profile = _run(["modal", "profile", "current"])
    if rc != 0 or not profile.strip():
        raise ProjectError("no active Modal profile. Fix: run `modal setup` once to log in")

    rc, listing = _run(["modal", "app", "list", "--json"])
    if rc != 0:
        raise ProjectError("cannot reach your Modal account. Fix: check your network, then `modal setup`")
    try:
        apps = json.loads(listing)
    except json.JSONDecodeError as exc:
        raise ProjectError(f"unexpected output from `modal app list --json`: {exc}") from exc
    return apps


def check_collisions(slug: str, apps: list[dict[str, Any]]) -> None:
    """Refuse when any <slug>-<pkg> app already exists in the account."""
    taken = {f"{slug}-{pkg}": pkg for pkg in FAMILY_PACKAGES}
    for app in apps:
        name = app.get("description", "")
        if name in taken:
            state = app.get("state", "unknown")
            raise ProjectError(
                f"Modal app {name!r} already exists (state: {state}). "
                "Pick another project id, or stop the existing app first."
            )


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise ProjectError(
            f"project id {slug!r} cannot compose a Modal app name. "
            "Use lowercase letters, digits, and dashes; start with a letter or digit; max 64 characters."
        )


def init_project(base: Path, project_id: str | None = None) -> Path:
    """Scaffold a project record. Bare: `base` is the project. Named: `base/<id>` is.

    Pure local scaffold: no account access here (run `check_prerequisites` and
    `check_collisions` first from the CLI). Refuses to overwrite a record.
    """
    if project_id is not None:
        _validate_slug(project_id)
        root = base / project_id
        slug = project_id
    else:
        root = base
        slug = base.name
        _validate_slug(slug)

    record_path = root / RECORD_DIR / RECORD_FILE
    if record_path.exists():
        raise ProjectError(f"{record_path} already exists; this directory is already a project")

    root.mkdir(parents=True, exist_ok=True)
    (root / "deploys").mkdir(exist_ok=True)
    record_path.parent.mkdir(exist_ok=True)
    record_path.write_text(json.dumps({"slug": slug, "packages": {}}, indent=2) + "\n")
    return root


def resolve_slug(base: Path, project_id: str | None = None) -> str:
    """The slug init would record, validated, without touching the disk or the account."""
    if project_id is not None:
        _validate_slug(project_id)
        return project_id
    _validate_slug(base.name)
    return base.name


# Package to GitHub repo name. The family's public repos live under one owner.
REPO_NAMES: dict[str, str] = {
    "embedding": "modal-embedding-server",
    "inference": "modal-inference-server",
    "vision": "modal-vision-server",
    "finetune": "modal-finetune-server",
    "vault": "modal-vault-server",
}
REPO_OWNER = "kylebrodeur"


def repo_url(pkg: str) -> str | None:
    """The public GitHub URL for a family package, or None when unknown."""
    name = REPO_NAMES.get(pkg)
    return f"https://github.com/{REPO_OWNER}/{name}" if name else None


def _default_git(argv: list[str]) -> tuple[int, str]:
    """Run git; return (exit code, combined output). Clone errors arrive on stderr."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=600)
    except FileNotFoundError:
        return 127, "git not found"
    return result.returncode, (result.stdout + result.stderr).strip()


def pull_packages(
    project_root: Path,
    repos_root: Path,
    pkgs: list[str],
    *,
    url_for: Callable[[str], str | None] = repo_url,
    run: Callable[[list[str]], tuple[int, str]] = _default_git,
) -> None:
    """Clone each missing package repo into `repos_root` and record it in the project.

    A present checkout is left alone: no fetch, no reset, no writes into it.
    Unknown names are refused before any clone runs. The record is written only
    after every clone succeeds.
    """
    unknown = [pkg for pkg in pkgs if pkg not in REPO_NAMES]
    if unknown:
        raise ProjectError(f"unknown package {unknown[0]!r}; known packages: {', '.join(REPO_NAMES)}")

    record_path = project_root / RECORD_DIR / RECORD_FILE
    if not record_path.is_file():
        raise ProjectError(f"{project_root} is not a project. Fix: run `mtk init` here first")
    record = json.loads(record_path.read_text())

    for pkg in pkgs:
        dest = repos_root / REPO_NAMES[pkg]
        if not dest.exists():
            url = url_for(pkg)
            if url is None:
                raise ProjectError(f"no repo URL for package {pkg!r}")
            rc, output = run(["git", "clone", "--quiet", url, str(dest)])
            if rc != 0:
                raise ProjectError(f"git clone failed for {pkg!r}: {output}")
        record.setdefault("packages", {})[pkg] = {"path": str(dest)}

    record_path.write_text(json.dumps(record, indent=2) + "\n")


def _project_slug(project_root: Path) -> str:
    record_path = project_root / RECORD_DIR / RECORD_FILE
    if not record_path.is_file():
        raise ProjectError(f"{project_root} is not a project. Fix: run `mtk init` here first")
    return json.loads(record_path.read_text())["slug"]


def check_overlay(project_root: Path, pkg: str) -> Path:
    """Preflight the deploy overlay deploys/<pkg>/ and return its path.

    Refuses a missing overlay, a missing deploy.sh or deploy.json, and any
    deploy.json whose app_name is not the composed <slug>-<pkg>.
    """
    overlay = project_root / "deploys" / pkg
    if not overlay.is_dir():
        raise ProjectError(f"no overlay at deploys/{pkg}/. Fix: create it with deploy.json and deploy.sh")
    if not (overlay / "deploy.json").is_file():
        raise ProjectError(f"deploys/{pkg}/ has no deploy.json. Fix: add one that names the app")
    if not (overlay / "deploy.sh").is_file():
        raise ProjectError(f"deploys/{pkg}/ has no deploy.sh. Fix: add the deploy entry point")

    expected = f"{_project_slug(project_root)}-{pkg}"
    data = json.loads((overlay / "deploy.json").read_text())
    app_name = data.get("app_name")
    if app_name != expected:
        raise ProjectError(
            f"deploys/{pkg}/deploy.json app_name must be {expected!r} (got {app_name!r}). "
            "Apps compose as <project>-<package> so projects never collide."
        )
    return overlay


def missing_secrets(status: Any, pkg: str) -> list[str]:
    """The missing secret names for a package. Fails CLOSED without a readable manifest.

    An absent or unreadable manifest is a refusal, not an empty list: an empty
    list would let the gate pass and the deploy run with unknown secrets.
    """
    if status is None or not status.manifest_found:
        raise ProjectError(
            f"no readable secrets manifest for {pkg!r}. Fix: run `mtk pull {pkg}` so its server/secrets.toml is present"
        )
    return list(status.missing)


def ensure_secrets(
    missing: list[str],
    *,
    confirm: Callable[[str], bool],
    create_all: Callable[[list[str]], None],
) -> None:
    """Create the missing secrets only after every one is confirmed.

    Confirmation is collected for the whole set first, so a refusal leaves no
    secret half-created. `create_all` runs once with the full missing list, since
    the secrets module creates a package's missing set in one call.
    """
    if not missing:
        return
    for name in missing:
        if not confirm(name):
            raise ProjectError(f"secret {name!r} is missing and was not confirmed; nothing was created")
    create_all(list(missing))
