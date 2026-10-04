"""Per-package command manifests (the "Design A" command host).

Each deployable repo carries an inert ``server/mtk-commands.toml`` declaring the
commands ``mtk <pkg>`` offers, next to its ``server/secrets.toml``. ``mtk`` reads
the manifest at parser-build time and mounts one subcommand per declared
command; execution is a closed set of run kinds implemented here. Adding a
command becomes a repo-side edit (one TOML table), so ``mtk`` stops hard-coding
a wrapper per package.

Manifest shape
--------------
A manifest declares EITHER named ``[[command]]`` tables OR a whole-surface
``[passthrough]`` table (a package that owns its own CLI, e.g. inference) - not
both. An optional ``[defaults]`` table merges into every ``[[command]]`` (a
command's own value wins); vault uses it for its shared kind/app/xdg.

Run kinds (the executor's closed vocabulary):
  modal      ``uv run modal <argv...>`` in the repo checkout
  container  a shell command exec'd INSIDE the package's Modal container
             (wake, pick the app's live container, exec, re-wake on a lost race)
  echo       print ``argv`` joined as operator guidance; exit 0
  probe      one authenticated health probe of the package

Discovery and gating
--------------------
The package group mounts whenever the repo ships a manifest (repo presence is
the signal - a fresh toolkit-only clone mounts nothing). A command's ``requires``
keys are enforced at run time through :func:`toolkit.config.require`, the
exact-fix gate. Deploy-class commands declare no ``requires`` so they stay
available BEFORE the package is configured: the bootstrap order is deploy, then
configure, and hiding deploy until a base_url exists would invert it.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolkit import config as cfg

MANIFEST_NAME = "mtk-commands.toml"

# The run kinds the host implements. A manifest naming anything else is a
# repo-side error (fail closed at load time). Whole-surface delegation to a
# repo's own CLI is a separate manifest shape ([passthrough]), not a kind.
KINDS = ("modal", "container", "echo", "probe")

# Default subprocess budget for the `modal` kind when a command declares none.
DEFAULT_MODAL_TIMEOUT = 1800


class CommandManifestError(Exception):
    """A manifest failed validation. ``args`` = (manifest_file, message)."""


@dataclass(frozen=True)
class CommandSpec:
    """One declared ``[[command]]``."""

    name: str
    summary: str
    kind: str
    argv: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    timeout: int | None = None
    passthrough: bool = False
    app: str = ""  # container kind: Modal app name (default: the repo dir name)
    xdg: str = ""  # container kind: XDG_CONFIG_HOME to export (empty = none)


@dataclass(frozen=True)
class PassthroughSpec:
    """A whole-surface ``[passthrough]`` delegation to the repo's own CLI."""

    argv: tuple[str, ...]
    summary: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    commands: list[CommandSpec] = field(default_factory=list)
    passthrough: PassthroughSpec | None = None


def manifest_path(repo: Path) -> Path:
    """The manifest location inside a repo checkout."""
    return repo / "server" / MANIFEST_NAME


def _as_str_list(path: Path, value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CommandManifestError(str(path), f"{what} must be a list of strings")
    return tuple(value)


def load(repo: Path) -> Manifest | None:
    """Parse ``<repo>/server/mtk-commands.toml``; None when the repo ships none.

    Raises CommandManifestError on malformed TOML, an unknown kind, a command
    missing name/summary/kind, neither or both of commands/passthrough, or a
    kind whose required fields (argv) are absent.
    """
    path = manifest_path(repo)
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CommandManifestError(str(path), f"invalid TOML: {exc}") from None

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise CommandManifestError(str(path), "[defaults] must be a table")

    has_commands = "command" in data
    has_passthrough = "passthrough" in data
    if has_commands and has_passthrough:
        raise CommandManifestError(str(path), "declare EITHER [[command]] entries OR [passthrough], not both")
    if not has_commands and not has_passthrough:
        raise CommandManifestError(str(path), "missing [[command]] entries or a [passthrough] table")

    if has_passthrough:
        body = data["passthrough"]
        if not isinstance(body, dict):
            raise CommandManifestError(str(path), "[passthrough] must be a table")
        argv = _as_str_list(path, body.get("argv"), "[passthrough].argv")
        if not argv:
            raise CommandManifestError(str(path), "[passthrough] requires a non-empty argv")
        summary = body.get("summary")
        if not isinstance(summary, str) or not summary:
            raise CommandManifestError(str(path), "[passthrough] requires a non-empty summary")
        return Manifest(
            passthrough=PassthroughSpec(
                argv=argv,
                summary=summary,
                requires=_as_str_list(path, body.get("requires"), "[passthrough].requires"),
            )
        )

    raw_commands = data["command"]
    if not isinstance(raw_commands, list) or not raw_commands:
        raise CommandManifestError(str(path), "[[command]] entries must be a non-empty list")
    commands: list[CommandSpec] = []
    for body in raw_commands:
        if not isinstance(body, dict):
            raise CommandManifestError(str(path), "each [[command]] must be a table")
        merged: dict[str, Any] = {**defaults, **body}
        name = merged.get("name")
        summary = merged.get("summary")
        kind = merged.get("kind")
        if not isinstance(name, str) or not name:
            raise CommandManifestError(str(path), "a [[command]] is missing a non-empty name")
        if not isinstance(summary, str) or not summary:
            raise CommandManifestError(str(path), f"command {name!r} is missing a non-empty summary")
        if kind not in KINDS:
            raise CommandManifestError(
                str(path), f"command {name!r} has unknown kind {kind!r}; supported: {list(KINDS)}"
            )
        argv = _as_str_list(path, merged.get("argv"), f"command {name!r} argv")
        if kind in ("modal", "container", "echo") and not argv:
            raise CommandManifestError(str(path), f"command {name!r} (kind {kind!r}) requires a non-empty argv")
        timeout = merged.get("timeout")
        if timeout is not None and not isinstance(timeout, int):
            raise CommandManifestError(str(path), f"command {name!r} timeout must be an integer")
        passthrough = merged.get("passthrough", False)
        if not isinstance(passthrough, bool):
            raise CommandManifestError(str(path), f"command {name!r} passthrough must be a boolean")
        commands.append(
            CommandSpec(
                name=name,
                summary=summary,
                kind=kind,
                argv=argv,
                requires=_as_str_list(path, merged.get("requires"), f"command {name!r} requires"),
                timeout=timeout,
                passthrough=passthrough,
                app=str(merged.get("app", "") or ""),
                xdg=str(merged.get("xdg", "") or ""),
            )
        )
    return Manifest(commands=commands)


# ── Container host (the `container` kind) ───────────────────────────────────


def _container_id(app_name: str, base_url: str) -> str:
    """Wake the scale-to-zero app, then poll for ITS live container id.

    The listing is filtered by the App Name column: a multi-app fleet (the
    family itself runs several Modal apps) has many `ta-` rows, and the first
    one is not necessarily ours. A row whose container id is missing/empty keeps
    polling (never returned as a candidate). The caller re-verifies before exec,
    because the container can scale to zero between this poll and exec.
    """
    import urllib.request

    with contextlib.suppress(Exception):
        urllib.request.urlopen(base_url + "/health", timeout=120)
    for _ in range(20):
        listing = subprocess.run(["modal", "container", "list"], capture_output=True, text=True, check=False)
        for line in listing.stdout.splitlines():
            if "│" not in line:
                continue
            cells = [cell.strip() for cell in line.split("│")]
            # Row shape: ['', '<cid>', '<app id>', '<app name>', ...].
            if len(cells) < 4:
                continue
            cid, app_id, listed_app = cells[1], cells[2], cells[3]
            if listed_app != app_name and app_id != app_name:
                continue
            if cid.startswith("ta-"):
                return cid
        time.sleep(2)
    raise SystemExit(f"no live container for {app_name}; is it deployed?")


def _container_exec(app_name: str, base_url: str, command: str) -> int:
    """Exec a shell command in the container, re-waking on a lost race.

    The container can scale to zero between the picker returning and exec
    starting (observed: `'' is not a valid Container ID`). On that error the
    loop wakes the app again and re-picks, up to a few attempts.
    """
    rc = 1
    for _ in range(3):
        cid = _container_id(app_name, base_url)
        proc = subprocess.run(
            ["modal", "container", "exec", cid, "--", "sh", "-c", command],
            capture_output=True,
            text=True,
            check=False,
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        rc = proc.returncode
        if "is not a valid Container ID" not in proc.stderr:
            return rc
        time.sleep(2)
    return rc


def _base_url(pkg: str) -> str:
    """The package's configured base URL, or a clean exit naming the fix."""
    section = cfg.section(pkg)
    base_url = str(section.get("base_url", "")).rstrip("/")
    if not base_url:
        raise SystemExit(
            f"{pkg} is not configured: missing base_url. Run `mtk setup` or set the package's env override."
        )
    return base_url


# ── Executors ───────────────────────────────────────────────────────────────


def _gate(pkg: str, requires: tuple[str, ...]) -> None:
    """Enforce a command's declared config keys with the exact-fix error."""
    if requires:
        cfg.require(pkg, requires)


def run(
    pkg: str,
    spec: CommandSpec,
    args: Any,
    *,
    probe: Callable[[str], dict[str, Any]],
    modal_run: Callable[..., tuple[bool, str, str]],
    pkg_repo: Callable[[str], Path],
) -> int:
    """Execute one declared command by its kind; return the process exit code."""
    _gate(pkg, spec.requires)
    extra: list[str] = list(getattr(args, "args", []) or []) if spec.passthrough else []

    if spec.kind == "echo":
        print(" ".join(spec.argv))
        return 0

    if spec.kind == "probe":
        state = probe(pkg)
        print(f"{pkg} probe status={state.get('status')}")
        return 0

    if spec.kind == "modal":
        ok, _detail, output = modal_run(pkg, *spec.argv, *extra, timeout=spec.timeout or DEFAULT_MODAL_TIMEOUT)
        print(output or ("ok" if ok else "failed"))
        return 0 if ok else 1

    if spec.kind == "container":
        app_name = spec.app or pkg_repo(pkg).name
        base_url = _base_url(pkg)  # fail with the fix line before touching the container
        command = " ".join([*spec.argv, *extra])
        if spec.xdg:
            command = f"export XDG_CONFIG_HOME={spec.xdg}; exec {command}"
        return _container_exec(app_name, base_url, command)

    raise SystemExit(f"unknown command kind {spec.kind!r}")  # unreachable: validated at load


def run_passthrough(
    pkg: str,
    spec: PassthroughSpec,
    args: Any,
    *,
    pkg_repo: Callable[[str], Path],
) -> int:
    """Delegate the package's whole surface to its own console script.

    ``mtk <pkg> <anything...>`` -> ``uv run <argv[0]> <anything...>`` in the repo
    checkout, preserving argv verbatim (the package's own argparse owns parsing).
    """
    _gate(pkg, spec.requires)
    argv = [*spec.argv, *(getattr(args, "args", []) or [])]
    proc = subprocess.run(["uv", "run", *argv], cwd=pkg_repo(pkg), check=False)
    return proc.returncode
