"""`mtk libs`: vendor + parity-check the family's shared modules.

`sync` copies each manifest module from the canonical modal-shared-libs
checkout into every target package tree (VERBATIM bytes + the vendored
header pinning provenance + md5). `check` verifies every vendored copy's
md5 against the canonical file: any hand-edit = drift = failure (CI runs
this via the per-repo pre-commit/CI lane).

Repo discovery: `$MODAL_SHARED_LIBS` > sibling `modal-shared-libs/` next to
the packages (repos root) > fail with the fix line.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from .. import config

VENDORED_HEADER = (
    "# Vendored from modal-shared-libs (libs/{module}) by `mtk libs sync`:\n"
    "# family repo: {pkg}, md5 {md5}. Canonical source of truth; report\n"
    "# fixes there, not here.\n"
)


def shared_libs_root() -> Path:
    """The canonical checkout: $MODAL_SHARED_LIBS > sibling dir > error."""
    env = config.os.getenv(config.SHARED_LIBS_REPO_ENV, "").strip()
    root = Path(env).expanduser() if env else config.repos_root() / "modal-shared-libs"
    if (root / "libs").is_dir():
        return root
    raise SystemExit(
        f"modal-shared-libs not found at {root}; clone it beside the packages "
        f"(or set {config.SHARED_LIBS_REPO_ENV}=/path/to/modal-shared-libs)"
    )


def md5_of(path: Path) -> str:

    return hashlib.md5(path.read_bytes()).hexdigest()


def _body_after_header(text: str) -> str:
    """The vendored body: everything after the 3-line header comment block."""
    marker = "not here.\n"
    if text.startswith("# Vendored from") and marker in text:
        return text.split(marker, 1)[1]
    return text  # headerless copy: the whole text IS the body (parity on bytes)


def check(pkg_filter: list[str] | None = None) -> dict[str, Any]:
    """Parity-check each vendored copy's BODY (sans header) against canonical bytes."""
    root = shared_libs_root()
    out: dict[str, Any] = {}
    repos_root = config.repos_root()
    canonical_md5s: dict[str, str] = {}
    canonical_bodies: dict[str, str] = {}
    for module, spec in config.LIBS_MANIFEST.items():
        canonical = root / "libs" / module
        canonical_text = canonical.read_text(encoding="utf-8")
        canonical_bodies[module] = canonical_text
        canonical_md5s[module] = md5_of(canonical)
        for pkg, subdir in spec["targets"].items():
            if pkg_filter and pkg not in pkg_filter:
                continue
            target = repos_root / pkg / subdir / module
            if not target.exists():
                state = "MISSING"
            else:
                vendored_text = target.read_text(encoding="utf-8")
                header_md5 = None
                if vendored_text.startswith("#"):
                    import re as _re

                    match = _re.search(r"md5 ([0-9a-f]{32})", vendored_text[:600])
                    header_md5 = match.group(1) if match else None
                if (
                    header_md5 == canonical_md5s[module]
                    and _body_after_header(vendored_text) == canonical_bodies[module]
                ):
                    state = "OK"
                elif header_md5 and header_md5 != canonical_md5s[module]:
                    state = "STALE"  # header says older-canonical: hand-edit or upstream moved
                else:
                    state = "DRIFT"  # headerless edit or body mismatch
            out[f"{module}:{pkg}"] = {"state": state, "path": str(target)}
    return out


def sync(pkg_filter: list[str] | None = None) -> dict[str, Any]:
    """Copy canonical modules into every target + write the vendored header as a leading comment."""
    root = shared_libs_root()
    repos_root = config.repos_root()
    out: dict[str, Any] = {}
    for module, spec in config.LIBS_MANIFEST.items():
        canonical = root / "libs" / module
        canonical_md5 = md5_of(canonical)
        canonical_text = canonical.read_text(encoding="utf-8")
        for pkg, subdir in spec["targets"].items():
            if pkg_filter and pkg not in pkg_filter:
                continue
            target_dir = repos_root / pkg / subdir
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / module
            header = VENDORED_HEADER.format(module=module, pkg=pkg, md5=canonical_md5)
            (target_dir / "__init__.py").touch(exist_ok=True)
            target.write_text(header + canonical_text, encoding="utf-8")
            out[f"{module}:{pkg}"] = {"state": "SYNCED", "md5": canonical_md5, "path": str(target)}
    return out


def build_parser() -> argparse.ArgumentParser:
    """Standalone parser (cli.py attaches with set_defaults(func=...))."""
    parser = argparse.ArgumentParser(prog="mtk libs")
    sub = parser.add_subparsers(dest="libs_verb", required=True)
    sync_p = sub.add_parser("sync", help="vendor the shared modules into every package tree")
    sync_p.add_argument("--pkg", help="comma-separated package names (default: all targets)")
    check_p = sub.add_parser("check", help="parity-check vendored copies (CI gate)")
    check_p.add_argument("--pkg", help="comma-separated package names (default: all targets)")
    return parser


def run(args: argparse.Namespace) -> int:
    """Verb body for `mtk libs sync|check`."""
    wanted = args.pkg.split(",") if getattr(args, "pkg", None) else None
    if args.libs_verb == "sync":
        out = sync(wanted)
        for key, state in out.items():
            print(f"{key:40s} {state['state']}  ({state['path']})")
        return 0
    out = check(wanted)
    bad = [key for key, state in out.items() if state["state"] != "OK"]
    for key, state in out.items():
        print(f"{key:40s} {state['state']}")
    if bad:
        raise SystemExit(
            f"vendored-copy drift in {len(bad)} module(s): {', '.join(bad)}; run `mtk libs sync` to refresh"
        )
    return 0
