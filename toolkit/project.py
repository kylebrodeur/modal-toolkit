"""Project scaffolding for `mtk` (U9): init, the preflight, and the project record.

Account rule: init is read-only against Modal. It checks the active profile and
reads the app list, and it refuses an app-name collision. It creates nothing
in the account. The runner is injected so tests never touch a real account.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The family packages. A project's apps compose as <slug>-<pkg>.
FAMILY_PACKAGES = ("embedding", "inference", "vision", "finetune", "vault")

# Modal app names: lowercase letters, digits, dashes; start with a letter or digit.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

RECORD_DIR = ".mtk"
RECORD_FILE = "config.json"


def record_path_of(project_root: Path) -> Path:
    """The project record path; raises when this is not a project."""
    path = Path(project_root) / RECORD_DIR / RECORD_FILE
    if not path.is_file():
        raise ProjectError(f"{project_root} is not a project. Fix: run `mtk init` here first")
    return path


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
    keep_project_git_clean(root)
    return root


def keep_project_git_clean(root: Path) -> None:
    """Ignore vendor/ (and Modal's .venv) inside project git repos.

    A nested checkout under vendor/ shows as one untracked dir in the parent
    project's git status (git does not track inside a nested repo). Ignoring
    it keeps the project clean while the vendor guard still enforces the pin
    (git describe works fine inside an ignored nested checkout).
    """
    gitignore = root / ".gitignore"
    lines = gitignore.read_text().splitlines() if gitignore.is_file() else []
    needed = [ln for ln in ("vendor/", ".venv/") if ln not in lines]
    if needed:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(needed)
        gitignore.write_text("\n".join(lines) + "\n")


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
    return result.returncode, (result.stdout + result.stderr).rstrip("\n")
    # rstrip, not strip: porcelain's leading status column (e.g. " M README.md")
    # must survive; a whole-blob strip() eats it and broke filename extraction.


def _project_slug(project_root: Path) -> str:
    record_path = project_root / RECORD_DIR / RECORD_FILE
    if not record_path.is_file():
        raise ProjectError(f"{project_root} is not a project. Fix: run `mtk init` here first")
    return json.loads(record_path.read_text())["slug"]


def overlay_dir(project_root: Path, pkg: str) -> Path:
    """The overlay dir for a package: deploys/<pkg>-server/.

    One layout, one model (decided 2026-10-14): the overlay dir name matches
    the vendor dir name (vendor/<pkg>-server/). Config, seed.json, hooks, and
    state live here; code never does.
    """
    return project_root / "deploys" / f"{pkg}-server"


def check_overlay(project_root: Path, pkg: str) -> Path:
    """Preflight the deploy overlay for a package and return its path.

    Refuses a missing overlay, a missing deploy.sh or deploy.json, and any
    deploy.json whose app_name is not the composed <slug>-<pkg>.
    """
    overlay = overlay_dir(project_root, pkg)
    shown = overlay.relative_to(project_root) if overlay.is_relative_to(project_root) else overlay
    if not overlay.is_dir():
        raise ProjectError(f"no overlay at {shown}/. Fix: create it with deploy.json and deploy.sh")
    if not (overlay / "deploy.json").is_file():
        raise ProjectError(f"{shown}/ has no deploy.json. Fix: add one that names the app")
    if not (overlay / "deploy.sh").is_file():
        raise ProjectError(f"{shown}/ has no deploy.sh. Fix: add the deploy entry point")

    expected = f"{_project_slug(project_root)}-{pkg}"
    data = json.loads((overlay / "deploy.json").read_text())
    app_name = data.get("app_name")
    if app_name != expected:
        raise ProjectError(
            f"deploys/{pkg}-server/deploy.json app_name must be {expected!r} (got {app_name!r}). "
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


BLOCK_BEGIN = "<!-- mtk:begin -->"
BLOCK_END = "<!-- mtk:end -->"


def agents_block(slug: str, packages: list[str]) -> str:
    """The mtk-managed section for AGENTS.md. Only this block is ever rewritten."""
    pkgs = ", ".join(packages) if packages else "none yet"
    lines = [
        BLOCK_BEGIN,
        "## Modal Toolkit (mtk)",
        "",
        f"This project (`{slug}`) deploys with `mtk`. Packages: {pkgs}.",
        "",
        "- Check state: `mtk doctor`. Vendor a package: `mtk pull <pkg> --tag <release>`.",
        *[f"- Deploy {p}: `mtk deploy {p}`" for p in packages],
        "- Deploy only through the overlays in `deploys/<pkg>-server/`. "
        "Never run `modal deploy` inside a package checkout.",
        "- vendor/ is a tag-pinned read-only copy: never edit it. "
        "Customise from the overlay (config, env, hooks, seed.json).",
        "- Secrets are confirmed before any is created. Never write secret values into this repo.",
        BLOCK_END,
    ]
    return "\n".join(lines) + "\n"


def llms_txt(slug: str, packages: list[str]) -> str:
    """llms.txt in the llmstxt.org layout: H1, blockquote summary, link sections."""
    out = [
        f"# {slug}",
        "",
        "> A Modal Toolkit project. Packages deploy with `mtk` from overlays in `deploys/<pkg>-server/`,",
        "> backed by tag-pinned vendor copies in `vendor/`.",
        "",
        "## Packages",
        "",
    ]
    for pkg in packages:
        url = repo_url(pkg) or ""
        out.append(f"- [{pkg}]({url}): the {pkg} package repo" if url else f"- {pkg}")
    out += ["", "## Toolkit", "", "- [mtk toolkit](https://github.com/kylebrodeur/modal-toolkit): the operator CLI", ""]
    return "\n".join(out)


def install_agent_files(root: Path, slug: str, packages: list[str]) -> None:
    """Write AGENTS.md (create, or refresh only the managed block) and llms.txt.

    `packages` is the FULL current package list (callers read it from the
    record), not just the ones a command just touched, so a second pull never
    erases earlier packages from the managed block. A user's AGENTS.md is
    never overwritten. Text outside the markers is kept byte for byte.
    """
    block = agents_block(slug, packages)
    agents_path = root / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(block)
    else:
        text = agents_path.read_text()
        if BLOCK_BEGIN in text and BLOCK_END in text:
            begin = text.index(BLOCK_BEGIN)
            end = text.index(BLOCK_END, begin) + len(BLOCK_END)
            new = text[:begin] + block.rstrip("\n") + text[end:]
        else:
            sep = "\n" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            new = text + sep + block
        if new != text:
            agents_path.write_text(new)
    (root / "llms.txt").write_text(llms_txt(slug, packages))


# ── vendor model ────────────────────────────────────────────────────────────
VENDOR_DIR = "vendor"
VENDOR_META = ".mtk-vendor.json"


def vendor_dir(project_root: Path, pkg: str) -> Path:
    """vendor/<pkg>-server/ inside the project (matches deploys/<pkg>-server/)."""
    return project_root / VENDOR_DIR / f"{pkg}-server"


def vendor_meta_tag(project_root: Path, pkg: str) -> str | None:
    """The vendored copy's pinned tag from its provenance file, or None."""
    meta_path = vendor_dir(project_root, pkg) / VENDOR_META
    if not meta_path.is_file():
        return None
    try:
        tag = json.loads(meta_path.read_text()).get("tag")
        return str(tag) if tag else None
    except (json.JSONDecodeError, OSError):
        return None


def _tag_head(git: Callable[[list[str]], tuple[int, str]], repo: Path, tag: str) -> str | None:
    """The commit a tag names, or None when the tag does not resolve."""
    rc, out = git(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"])
    return out.strip() if rc == 0 and out.strip() else None


def vendor_packages(
    project_root: Path,
    pkgs: list[str],
    *,
    tags: dict[str, str],
    url_for: Callable[[str], str | None] = repo_url,
    git: Callable[[list[str]], tuple[int, str]] = _default_git,
    confirm: Callable[[str], bool] = lambda _q: False,
) -> None:
    """Vendor each package into vendor/<pkg>-server/ (one layout with the overlay dir).

    Copies pin an exact release tag as a detached checkout with provenance.
    Refusals happen before any write: unknown package, unknown tag,
    provenance missing for a package. Re-vendoring an existing vendor dir to
    a different tag is a decision, so it needs confirm=True; the default
    refuses. A record entry is merged, not replaced: seeded history keeps,
    and the deployed signal keeps only when the tag is unchanged (a new pin
    has not been deployed).
    """
    unknown = [pkg for pkg in pkgs if pkg not in REPO_NAMES]
    if unknown:
        raise ProjectError(f"unknown package {unknown[0]!r}; known packages: {', '.join(REPO_NAMES)}")
    missing_tags = [pkg for pkg in pkgs if not tags.get(pkg, "").strip()]
    if missing_tags:
        raise ProjectError(
            f"missing provenance (release tag) for: {', '.join(missing_tags)}. "
            "Pass --tag per package so every vendor copy is pinned and guarded."
        )

    record_path = project_root / RECORD_DIR / RECORD_FILE
    if not record_path.is_file():
        raise ProjectError(f"{project_root} is not a project. Fix: run `mtk init` here first")
    record = json.loads(record_path.read_text())
    record_packages = record.setdefault("packages", {})

    # Resolve every tag first, so nothing is written on a bad tag.
    planned: list[tuple[str, str, str, str]] = []  # (pkg, url/None, dest, commit)
    for pkg in pkgs:
        url = url_for(pkg)
        dest = vendor_dir(project_root, pkg)
        commit = _tag_head(git, dest, tags[pkg])
        if commit is None and not dest.exists() and url is None:
            raise ProjectError(f"cannot resolve tag {tags[pkg]!r} for {pkg!r}: no source to fetch from")
        planned.append((pkg, url, dest, commit or ""))

    # Re-vendor confirmation is a decision gate (no disk writes here).
    for pkg, _url, dest, _commit in planned:
        existing_meta = dest / VENDOR_META if dest.exists() else None
        if existing_meta is None or not existing_meta.is_file():
            continue
        current = json.loads(existing_meta.read_text()).get("tag", "?")
        if current != tags[pkg] and not confirm(f"re-vendor {pkg} from {current} to {tags[pkg]}"):
            raise ProjectError(
                f"{pkg!r} is vendored at {current!r}; refusing to move it to {tags[pkg]!r} without confirmation"
            )

    # Resolve every tag BEFORE any write into vendor/: clone missing sources
    # into a temp dir, verify each tag, and only then move into place.
    staged: dict[str, Path] = {}
    for pkg, url, dest, _commit in planned:
        if dest.exists():
            if _tag_head(git, dest, tags[pkg]) is None:
                rc, out = git(["git", "-C", str(dest), "fetch", "--quiet", "origin"])
                if rc != 0:
                    raise ProjectError(f"git fetch failed for {pkg!r}: {out}")
            if _tag_head(git, dest, tags[pkg]) is None:
                raise ProjectError(f"tag {tags[pkg]!r} not found for {pkg!r} after fetch")
        else:
            if url is None:
                raise ProjectError(f"cannot resolve tag {tags[pkg]!r} for {pkg!r}: no source to fetch from")
            tmp_root = Path(tempfile.mkdtemp(prefix="mtk-vendor-"))
            rc, out = git(["git", "clone", "--quiet", url, str(tmp_root / "r")])
            if rc != 0:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise ProjectError(f"git clone failed for {pkg!r}: {out}")
            if _tag_head(git, tmp_root / "r", tags[pkg]) is None:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise ProjectError(f"tag {tags[pkg]!r} not found upstream for {pkg!r}")
            staged[pkg] = tmp_root / "r"

    # Every tag resolves. Any prior vendor dir with a different pin is already
    # confirmed; a same-tag re-run just re-checkouts (idempotent).
    wrote: list[str] = []
    for pkg, _url, dest, _commit in planned:
        staged_repo = staged.pop(pkg, None)
        if staged_repo is not None:
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_repo), str(dest))
            shutil.rmtree(staged_repo.parent, ignore_errors=True)  # the mkdtemp wrapper
        head = _tag_head(git, dest, tags[pkg])
        if head is None:
            raise ProjectError(f"tag {tags[pkg]!r} went missing for {pkg!r} before checkout")
        rc, out = git(["git", "-C", str(dest), "checkout", "--quiet", "--detach", head])
        if rc != 0:
            raise ProjectError(f"detaching at {tags[pkg]!r} failed for {pkg!r}: {out}")
        meta = {
            "repo": REPO_NAMES[pkg],
            "tag": tags[pkg],
            "commit": head,
            "vendored": "detached checkout of the release tag",
        }
        (dest / VENDOR_META).write_text(json.dumps(meta, indent=2) + "\n")
        prior = record_packages.get(pkg, {})
        changed_tag = prior.get("tag") != tags[pkg]
        new_entry = {
            "vendor": f"{VENDOR_DIR}/{pkg}-server",
            "tag": tags[pkg],
            "seeded": prior.get("seeded", []),
        }
        if prior.get("deployed") and not changed_tag:
            new_entry["deployed"] = prior["deployed"]  # same pin: the deploy signal stays true
        # a tag change clears deployed: the new pin has not been deployed
        record_packages[pkg] = new_entry
        wrote.append(pkg)

    record_path.write_text(json.dumps(record, indent=2) + "\n")
    for pkg in wrote:
        print(f"vendored {pkg}: {vendor_dir(project_root, pkg)} at {tags[pkg]}")


def vendor_check(
    project_root: Path, pkg: str, git: Callable[[list[str]], tuple[int, str]] = _default_git
) -> tuple[bool, list[str]]:
    """Read-only drift check for a vendored package: (ok, findings).

    A copy passes when its HEAD equals the recorded commit, the recorded tag
    still resolves to that commit, and the worktree is clean. Findings name the
    offending file or fact, and each one ends with where the change belongs:
    deploys/<pkg>-server/ (config/hooks) or an upstream release (code).
    """
    dest = vendor_dir(project_root, pkg)
    findings: list[str] = []
    if not dest.is_dir() or not (dest / ".git").exists():
        exists_note = "exists" if dest.exists() else "never vendored"
        return False, [f"{exists_note} at {dest}; not a vendored git copy. Fix: run `mtk pull {pkg} --tag <release>`"]
    meta_path = dest / VENDOR_META
    if not meta_path.is_file():
        return False, [f"{dest} has no {VENDOR_META} provenance; fix: run `mtk pull {pkg} --tag <release>`"]
    meta = json.loads(meta_path.read_text())

    rc, out = git(["git", "-C", str(dest), "status", "--porcelain", "-uall", "--", ":!.mtk-vendor.json"])
    if rc != 0:
        return False, [f"git status failed in {dest}: {out}"]
    for line in out.splitlines():
        if not line.strip():
            continue
        # porcelain v1: XY + space + path. Use [2:] + lstrip, never a whole-string
        # lstrip first: it would eat a leading-space X column (the EADME.md bug).
        rel = line[2:].lstrip()
        # git quotes paths with special chars as C escapes; unquote faithfully.
        if rel.startswith('"') and rel.endswith('"') and len(rel) >= 2:
            rel = rel[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        if rel.endswith("/"):
            continue  # untracked dir line; -uall keeps its files as separate entries
        findings.append(
            f"vendored copy is modified: {rel} - vendor/ is not a scratch tree; "
            f"changes belong in deploys/{pkg}-server/ (hooks, config) or in an upstream release"
        )

    rc, out = git(["git", "-C", str(dest), "rev-parse", "HEAD"])
    if rc != 0:
        return False, [f"git rev-parse failed in {dest}: {out}"]
    head = out.strip()
    if head != meta.get("commit", ""):
        findings.append(
            f"vendored HEAD {head[:12]} != provenance commit {meta.get('commit', '?')[:12]}; "
            f"fix: re-vendor with `mtk pull {pkg} --tag {meta.get('tag', '?')}`"
        )

    # commit comparison, not "describe --exact-match": two tags can share one
    # commit; the right question is whether the RECORDED tag resolves to HEAD.
    rc, out = git(["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", f"{meta.get('tag', '')}^{{commit}}"])
    if rc != 0 or not out.strip():
        findings.append(
            f"provenance tag {meta.get('tag', '?')!r} does not resolve in the vendored copy; "
            f"fix: re-vendor with `mtk pull {pkg} --tag <release>`"
        )
    elif out.strip() != head:
        findings.append(
            f"tag {meta.get('tag', '?')!r} resolves to {out.strip()[:12]}, not HEAD; "
            f"fix: re-vendor with `mtk pull {pkg} --tag {meta.get('tag', '?')}`"
        )

    return not findings, findings
