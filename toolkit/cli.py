"""mtk: the one operator CLI for the Modal Toolkit.

Full lifecycle across all four packages (embedding, inference, vision, finetune):

    mtk setup                            # one-time: write ~/.config/modal-toolkit/config.json
    mtk config                           # inspect + validate the config
    mtk doctor [--pkg ...]               # health + drift report across all four
    mtk warm [--pkg ...] [--all]         # cold-start a package
    mtk shutdown [--pkg ...] [--all]     # scale GPU(s) to zero now
    mtk cost                             # per-package + blended GPU-hour view
    mtk flow                             # the ecosystem flowchart, live state
    mtk dashboard deploy|stop|logs|url   # the fleet dashboard (CPU Modal app)
    mtk vault status|sync|pull-only|...  # ob passthrough INSIDE the vault container (never local sync)

    mtk metrics url | test               # VictoriaMetrics drop-in: effective URL, probe write

    mtk secrets check | create | rotate  # fleet-level Modal Secrets (see README's Secrets section)

Per-package deep work comes from each repo's own command manifest
(`server/mtk-commands.toml`, mounted by toolkit/pkg_commands.py):

    mtk embedding sync | reindex
    mtk vision deploy | warm
    mtk inference <command> ...          # passthrough to the repo's modal-inference CLI
    mtk finetune train | eval | gguf
    mtk vault status | sync | pull-only | ...   # ob INSIDE the vault container

Config lives in ~/.config/modal-toolkit/config.json (mode 600); tokens are
never printed. Set $MODAL_TOOLKIT_CONFIG to use a different path; set
per-package env overrides ($MODAL_BASE_URL, $MODAL_PROXY_TOKEN, ...) to
override the file without editing it.
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from toolkit import config as cfg
from toolkit import pkg_commands
from toolkit import secrets as mtk_secrets
from toolkit.secrets import ManifestSecret, ManifestSpec

TIMEOUT_S = 20  # warm-path probe budget (a hot lane answers in milliseconds)
COLD_TIMEOUT_S = 120  # one retry for a scale-to-zero cold boot (image hydrate + first /health)

# The packages, in dependency order (embedding first; coding = the mci
# private fleet core, joined 2026-10-06 with its secrets manifest; vault =
# modal-vault-server, secrets-only until it gets a config surface).
PACKAGE_ORDER = ("embedding", "inference", "vision", "finetune", "coding", "vault")

# Health paths (mirrors what each server exposes).
HEALTH: dict[str, str] = {
    "embedding": "/health",
    "inference": "/v1/models",  # /health on vLLM has no Ollama equivalent
    "vision": "/health",
    # Finetune is a pipeline, not a server; its "health" is that the Modal App is deployable.
    "finetune": "",
    # Vault serves a public /health (vault + sync triage); /mcp + /admin/* stay bearer-gated.
    "vault": "/health",
}

# GPU hourly rates used by `mtk cost` when computing always-on burn.
GPU_HOURLY = {
    "H200": 2.35,
    "B200": 3.53,
    "H100": 2.10,
    "L40S": 1.10,
    "A10G": 0.60,
    "L4": 0.80,
    "T4": 0.53,
}


def _repos_root() -> Path:
    return cfg.repos_root()


# Package -> repo dir special cases (everything else is modal-<pkg>-server),
# matching toolkit/secrets.py REPO_SPECIAL.
_REPO_SPECIAL = {"finetune": "modal-finetune-server", "vault": "modal-vault-server"}


def _repo_dir(pkg: str) -> Path:
    """The repo path for a package WITHOUT existence checks (mount-time discovery)."""
    return _repos_root() / _REPO_SPECIAL.get(pkg, f"modal-{pkg}-server")


def _pkg_repo(pkg: str) -> Path:
    """Path to one sibling repo; used when shelling into per-package commands."""
    path = _repo_dir(pkg)
    if not path.exists():
        raise SystemExit(f"{pkg} repo not found at {path}; set {cfg.REPO_ENV} or fix the config's repos.root.")
    return path


def _token_headers(pkg: str) -> dict[str, str]:
    got = cfg.section(pkg)
    token = got.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── Fleet commands ──────────────────────────────────────────────────────────


def cmd_setup(args: argparse.Namespace) -> int:
    """Write ~/.config/modal-toolkit/config.json from prompt + argv overrides."""
    data = cfg.load()
    if args.from_env:
        # Bootstrap from a pre-existing local config if present.
        legacy_cfg = Path.home() / ".config" / "mci" / "config.json"
        if legacy_cfg.exists():
            existing = json.loads(legacy_cfg.read_text())
            data.setdefault("inference", {}).update(
                {"base_url": existing.get("base_url", ""), "token": existing.get("token", "")}
            )
            data.setdefault("repos", {}).update({"root": existing.get("repo", "")})
            print(f"Imported {legacy_cfg} into the toolkit config.")
    if args.base_url:
        for pkg in PACKAGE_ORDER:
            data.setdefault(pkg, {})["base_url"] = args.base_url
    if args.token:
        for pkg in PACKAGE_ORDER:
            data.setdefault(pkg, {})["token"] = args.token
    if args.repos_root:
        data.setdefault("repos", {})["root"] = args.repos_root
    p = cfg.write(data)
    print(f"Config written to {p}")
    audit = cfg.validate()
    for pkg, state in audit["packages"].items():
        mark = "ok" if state["ok"] else f"missing {','.join(state['missing'])}"
        print(f"  {pkg:10s}  {mark}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Inspect + validate the toolkit config."""
    audit = cfg.validate()
    print(json.dumps(audit, indent=2))
    return 0


def _probe(pkg: str) -> dict[str, Any]:
    """One package's health + configured-alias check. Never raises."""
    got = cfg.section(pkg)
    base = str(got.get("base_url", "")).rstrip("/")
    health_path = HEALTH.get(pkg, "/health")

    out: dict[str, Any] = {"base_url_set": bool(base), "token_set": bool(got.get("token"))}
    if not base or not health_path:
        if pkg == "finetune":
            # Finetune is a pipeline, not a server. Report the repo presence instead.
            out["repo_present"] = _pkg_repo(pkg).exists()
            out["status"] = "repo" if out["repo_present"] else "missing"
        return out

    url = f"{base}{health_path}"
    try:
        response = httpx.get(url, headers=_token_headers(pkg), timeout=TIMEOUT_S)
        out["http_status"] = response.status_code
        out["status"] = "ok" if response.status_code == 200 else "unhealthy"
        if response.status_code == 200 and pkg == "inference":
            body = response.json()
            models = body.get("data", []) if isinstance(body, dict) else []
            out["served"] = [m.get("id", "") for m in models if isinstance(m, dict)]
    except httpx.TimeoutException as exc:
        # A configured, deployed lane that misses the warm budget is almost
        # always a scale-to-zero cold boot, not an outage. One retry on a
        # cold budget distinguishes that (status "cold") from a real outage.
        try:
            response = httpx.get(url, headers=_token_headers(pkg), timeout=COLD_TIMEOUT_S)
            out["http_status"] = response.status_code
            out["status"] = "ok" if response.status_code == 200 else "unhealthy"
            out["cold"] = True
            if response.status_code == 200 and pkg == "inference":
                body = response.json()
                models = body.get("data", []) if isinstance(body, dict) else []
                out["served"] = [m.get("id", "") for m in models if isinstance(m, dict)]
        except httpx.TimeoutException:
            out["status"] = "cold"
            out["error"] = f"{type(exc).__name__}"
    except (httpx.HTTPError, httpx.StreamError, OSError) as exc:
        out["status"] = "unreachable"
        out["error"] = f"{type(exc).__name__}"
    return out


# ── Secrets helpers (shared by cmd_doctor and cmd_secrets) ──────────────────


def _secrets_summary(st: Any) -> dict[str, Any]:
    """The per-package secrets shape doctor JSON carries: ok + details."""
    return {
        "manifest_found": bool(st.manifest_found),
        "ok": not st.missing and not any(s.drift for s in st.secrets),
        "missing": list(st.missing),
        "drift": [s.drift for s in st.secrets if s.drift],
    }


def _secrets_line(st: dict[str, Any]) -> str:
    """The one-line secrets text doctor prints: ok | missing <name> | drift ..."""
    marks: list[str] = []
    if st.get("drift"):
        marks.extend(st["drift"])
    for name in st.get("missing", []):
        marks.append(f"missing {name}")
    return "; ".join(marks) if marks else "ok"


def _commands_summary(pkg: str) -> dict[str, Any]:
    """What `mtk <pkg>` would offer: the repo's manifest-declared commands.

    Read-only file inspection (no network, no subprocess); a malformed manifest
    surfaces its error string instead of raising, so doctor still reports.
    """
    repo = _repo_dir(pkg)
    if not repo.exists():
        return {"manifest_found": False, "repo_present": False}
    try:
        manifest = pkg_commands.load(repo)
    except pkg_commands.CommandManifestError as exc:
        return {"manifest_found": True, "repo_present": True, "error": f"{exc.args[1]}"}
    if manifest is None:
        return {"manifest_found": False, "repo_present": True}
    if manifest.passthrough is not None:
        return {"manifest_found": True, "repo_present": True, "passthrough": " ".join(manifest.passthrough.argv)}
    return {
        "manifest_found": True,
        "repo_present": True,
        "commands": [spec.name for spec in manifest.commands],
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    """Health + drift report for all four packages (or --pkg subset)."""
    wanted = args.pkg.split(",") if args.pkg else PACKAGE_ORDER
    out: dict[str, Any] = {}
    for pkg in wanted:
        if pkg not in PACKAGE_ORDER:
            print(f"skipping unknown pkg {pkg!r}", file=sys.stderr)
            continue
        out[pkg] = _probe(pkg)
        out[pkg]["commands"] = _commands_summary(pkg)

    # Secrets share ONE workspace list call per doctor run, whatever the
    # package subset; a broken modal CLI degrades the secrets lines without
    # touching package health.
    pkg_secret_state: dict[str, Any] = {}
    secrets_broken: str | None = None
    try:
        pkg_secret_state = mtk_secrets.check_all(wanted)
    except SystemExit as exc:
        secrets_broken = str(exc)
    for pkg in out:
        if secrets_broken is not None:
            out[pkg]["secrets"] = {"secrets_error": secrets_broken}
        elif pkg in pkg_secret_state and pkg_secret_state[pkg].manifest_found:
            out[pkg]["secrets"] = _secrets_summary(pkg_secret_state[pkg])
        else:
            out[pkg]["secrets"] = {"manifest_found": False, "ok": True}

    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for pkg, state in out.items():
        mark = state.get("status", "unknown")
        print(f"{pkg:10s}  {mark}")
        if state.get("served"):
            print(f"           served: {', '.join(state['served'])}")
        if state.get("error"):
            print(f"           error:  {state['error']}")
        if (cmd_state := state.get("commands")) and cmd_state.get("error"):
            print(f"           commands: INVALID MANIFEST: {cmd_state['error']}")
        elif isinstance(cmd_state, dict) and not cmd_state.get("manifest_found") and cmd_state.get("repo_present"):
            print("           commands: (repo ships no mtk-commands.toml)")
        if (secret_state := state.get("secrets")) and (
            not secret_state.get("ok", True) or "secrets_error" in secret_state
        ):
            print(f"           secrets: {_secrets_line(secret_state)}")
    return 0


def _modal_run(pkg: str, *argv: str, timeout: int = 300) -> tuple[bool, str, str]:
    """Subprocess wrapper for `uv run modal ...` inside a package repo."""
    command = ["uv", "run", "modal", *argv]
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command, cwd=_pkg_repo(pkg), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return False, "", f"timed out after {timeout}s"
    elapsed = round(time.perf_counter() - started, 2)
    return result.returncode == 0, f"elapsed={elapsed}s", (result.stdout + result.stderr).strip()


def cmd_status(args: argparse.Namespace) -> int:
    """One table: per package - configured/enabled/secrets-healthy/reachable.

    What a trial user runs FIRST: it reads config state (cheap) and only
    touches the network for the reachability line (--net, default on; use
    --no-net for a config-only glance).
    """
    audit = cfg.validate()
    pkg_secret_state: dict[str, Any] = {}
    secrets_broken: str | None = None
    try:
        pkg_secret_state = mtk_secrets.check_all(list(audit["packages"]))
    except SystemExit as exc:
        secrets_broken = str(exc)
    print(f"{'package':<11} {'configured':<11} {'enabled':<9} {'secrets':<12} {'reachable'}")
    for pkg, state in audit["packages"].items():
        secrets_state = "n/a"
        if secrets_broken is not None:
            secrets_state = "error"
        else:
            pkg_state = pkg_secret_state.get(pkg)
            if pkg_state is not None and pkg_state.manifest_found:
                secrets_state = "ok" if not pkg_state.missing else f"missing {len(pkg_state.missing)}"
        reachable = "n/a"
        if args.net and state["enabled"] and state["ok"]:
            reachable = _reachable_mark(pkg)
        missing_txt = "" if state["ok"] else f" (missing: {', '.join(state['missing'])})"
        print(f"{pkg:<11} {state['ok']!s:<11} {state['enabled']!s:<9} {secrets_state:<12} {reachable}{missing_txt}")
    if secrets_broken is not None:
        print(f"secrets subsystem: {secrets_broken}")
    return 0


def _reachable_mark(pkg: str) -> str:
    """Short-timeout health probe mark: up | down | cold (never long).

    Uses the same HEALTH map as the full probe: inference exposes /v1/models
    (its engine has no /health), so a hardcoded /health here misread every
    inference lane.
    """
    path = HEALTH.get(pkg)
    if not path:
        return "n/a"
    section_state = cfg.section(pkg)
    base = str(section_state.get("base_url", "")).rstrip("/")
    if not base:
        return "no-url"
    try:
        import urllib.request

        with urllib.request.urlopen(base + path, timeout=3) as response:
            return "up" if response.status == 200 else f"http-{response.status}"
    except Exception as exc:
        return "down" if "timed out" not in str(exc).lower() else "cold"


def cmd_shutdown(args: argparse.Namespace) -> int:
    """Scale GPU to zero now (per package or --all)."""
    wanted = []
    if args.all:
        wanted = list(PACKAGE_ORDER)
    elif args.pkg:
        wanted = args.pkg.split(",")
    else:
        raise SystemExit("specify --pkg <name> or --all")

    for pkg in wanted:
        if pkg not in PACKAGE_ORDER:
            print(f"skipping unknown pkg {pkg!r}", file=sys.stderr)
            continue
        if pkg == "inference":
            ok, detail, output = _modal_run(pkg, "run", "server/modal_service.py::gpu_stop_eager", timeout=300)
            mark = "ok" if ok else "failed"
            print(f"{pkg:10s}  shutdown {mark}  {detail}")
            if not ok and output:
                print(f"           {output[:200]}")
        elif pkg == "embedding":
            # Embedding has no GPU worker to stop; cheap CPU-only FastAPI app.
            print(f"{pkg:10s}  (CPU-only FastAPI, nothing to shutdown)")
        elif pkg == "vision":
            # Vision's scaledown is a container-lifecycle concern; it's CPU-warm/zero via autoscaler.
            ok, detail, _ = _modal_run(pkg, "app", "stop", "-y", "modal-vision-server")
            mark = "stopped" if ok else "already-cold/failed"
            print(f"{pkg:10s}  stop {mark}  {detail}")
        elif pkg == "finetune":
            # Finetune has no long-lived server.
            print(f"{pkg:10s}  (no long-lived server)")
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    """Cold-start a package's GPU worker (per package or --all)."""
    wanted = []
    if args.all:
        wanted = list(PACKAGE_ORDER)
    elif args.pkg:
        wanted = args.pkg.split(",")
    else:
        raise SystemExit("specify --pkg <name> or --all")

    for pkg in wanted:
        if pkg not in PACKAGE_ORDER:
            print(f"skipping unknown pkg {pkg!r}", file=sys.stderr)
            continue
        # Warm = fire a cheap authenticated /health probe and let Modal start the container.
        state = _probe(pkg)
        print(f"{pkg:10s}  probe status={state.get('status')} (cold boot starts on the request)")
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """Per-package + blended GPU-hour view (always-on burn + per-package rates)."""
    out: dict[str, Any] = {}
    for pkg in PACKAGE_ORDER:
        got = cfg.section(pkg)
        gpu = str(got.get("gpu", "")).strip()
        # Read GPU per package from the config; default to the package's known default.
        gpu = gpu or ("H200" if pkg == "inference" else "T4" if pkg == "vision" else "L4" if pkg == "embedding" else "")
        rate = GPU_HOURLY.get(gpu)
        if rate is None:
            out[pkg] = {"gpu": gpu or "(unset)", "always_on_usd_per_hour": None}
            continue
        out[pkg] = {
            "gpu": gpu,
            "always_on_usd_per_hour": rate,
            "always_on_usd_per_day": round(rate * 24, 2),
            "always_on_usd_per_month": round(rate * 24 * 30, 2),
        }
    total_per_hour = sum(v["always_on_usd_per_hour"] or 0 for v in out.values() if isinstance(v, dict))
    total_per_month = sum(v.get("always_on_usd_per_month", 0) for v in out.values() if isinstance(v, dict))
    out["total"] = {
        "always_on_usd_per_hour": round(total_per_hour, 2),
        "always_on_usd_per_month": round(total_per_month, 2),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_flow(args: argparse.Namespace) -> int:
    """The ecosystem flowchart, rendered against live state."""
    # Render mermaid-ish output with a live health indicator per node.
    out = {}
    for pkg in PACKAGE_ORDER:
        out[pkg] = _probe(pkg).get("status", "unknown")
    if args.push:
        from toolkit import metrics

        metrics.emit_flow_states(out)
    print("Ecosystem live flow:")
    for pkg, state in out.items():
        mark = "ok" if state == "ok" else "cold" if state in ("unreachable", "unhealthy") else "n/a"
        print(f"  {pkg:10s} [{mark}]")
    print()
    print("  Your data -> embedding (vectors) -> inference (reasoning) -> local/private search")
    print("  Your data -> vision (classify)")
    print("  finetune -> artifacts (adapter/GGUF) -> inference (serve them)")
    return 0


# ── Secrets commands ────────────────────────────────────────────────────────


def _secrets_json(statuses: dict[str, Any]) -> None:
    """Emit the check_all()/doctor secrets shape as JSON."""
    print(json.dumps({pkg: dataclasses.asdict(st) for pkg, st in statuses.items()}, indent=2))


def _confirm_rotation(name: str, pkg: str) -> bool:
    """The rotate confirmation gate: default NO, real stdin, plain word.

    Reads the controlling stdin directly (getpass-style input is wrong here:
    the confirmation SHOULD be visible, only values are hidden). A bare Enter
    or anything that is not `y/yes` declines.
    """
    print(
        f'Rotating "{name}" ({pkg}): existing keys are regenerated and every '
        "live deployment must switch to the new values."
    )
    return input("Rotate for real? [yN] ").strip().lower() in ("y", "yes")


def _resolve_rotation(name: str) -> tuple[str, ManifestSpec, ManifestSecret] | None:
    """Which manifest declares Modal secret `name`: (pkg, spec, secret).

    Manifest reads ONLY: zero subprocess calls, so a typo'd name aborts with
    no side effects in the workspace.
    """
    for pkg in mtk_secrets.PACKAGES:
        spec = mtk_secrets.load_manifest(mtk_secrets._pkg_repo(pkg))
        if spec is None:
            continue
        for secret in spec.secrets:
            if secret.name == name:
                return pkg, spec, secret
    return None


def cmd_secrets(args: argparse.Namespace) -> int:
    """`mtk secrets check | create | rotate` dispatch (design §2)."""
    command = args.secrets_command

    if command == "check":
        statuses = mtk_secrets.check_all(args.pkg.split(",") if args.pkg else None)
        _secrets_json(statuses) if args.json else mtk_secrets._print_table(statuses)
        if args.json:
            return 0  # scripts consume exit codes: --json always 0
        return 1 if any(st.missing for st in statuses.values()) else 0

    if command == "create":
        return mtk_secrets.create(args)

    if command == "rotate":
        return _cmd_secrets_rotate(args)

    raise SystemExit(f"unknown secrets subcommand {command!r}")


def _cmd_secrets_rotate(args: argparse.Namespace) -> int:
    """`mtk secrets rotate <name>`: regenerate ONE manifest-declared secret.

    Its own command body (Task 2's report anticipates this): `create --force`
    would regenerate every secret in the target's package manifest, but the
    design pins rotate to the NAMED secret. Values follow create()'s exact
    rules (token_hex(32) for generated keys, getpass hints for asked keys)
    and land through the same seam create() uses: `_modal_secret_create`.
    Manifest reads happen before the gate, so declining leaves the workspace
    untouched and no value ever prints.
    """
    name = (getattr(args, "name", "") or "").strip()
    if not name:
        print("usage: mtk secrets rotate <secret-name>", file=sys.stderr)
        return 1

    resolved = _resolve_rotation(name)
    if resolved is None:
        print(f"error: no manifest declares secret {name!r}", file=sys.stderr)
        print("       (names come from each repo's server/secrets.toml)", file=sys.stderr)
        return 1
    pkg, _spec, secret = resolved

    if not _confirm_rotation(name, pkg):
        print("aborted: no changes made", file=sys.stderr)
        return 1

    argv: list[str] = list(getattr(args, "modal_argv", None) or mtk_secrets.MODAL_ARGV)
    kv: dict[str, str] = {}
    for env_var, key_spec in secret.keys.items():
        if key_spec.generate == "hex32":
            kv[env_var] = secrets.token_hex(32)
        else:
            kv[env_var] = getpass.getpass(f"    {secret.name} {env_var} ({key_spec.ask or env_var}): ")
    mtk_secrets._modal_secret_create(argv, secret.name, kv)
    print(
        f"rotated {secret.name} ({pkg}): {len(kv)} key(s) regenerated; live deployments must switch to the new values."
    )
    return 0


# ── Metrics commands ────────────────────────────────────────────────────────


def cmd_metrics(args: argparse.Namespace) -> int:
    """VictoriaMetrics drop-in: probe the write endpoint or show the effective URL."""
    from datetime import UTC, datetime

    from toolkit import metrics

    url, source = metrics.effective_url()
    if args.subcommand == "url":
        print(f"{url}  ({source})")
        return 0
    if args.subcommand == "test":
        probe = datetime.now(UTC).isoformat(timespec="seconds")
        tags = {"probe": probe}
        line = metrics.line_protocol("mtk_metrics_probe", 1, tags=tags)
        ok = metrics.write_metric("mtk_metrics_probe", 1, tags=tags)
        if ok:
            print(f"ok: wrote probe point to {url}")
            print(f"    {line}")
            return 0
        print(f"failed: no VictoriaMetrics at {url} (set ${metrics.ENV_URL} to change)", file=sys.stderr)
        return 1
    raise SystemExit(f"unknown metrics subcommand {args.subcommand!r}")


# ── Per-package commands (thin wrappers that shell into each repo) ──────────


def cmd_dashboard(args: argparse.Namespace) -> int:
    """`mtk dashboard deploy|stop|logs|url` - the fleet dashboard Modal app."""
    if args.subcommand == "deploy":
        ok, _detail, out = _modal_run("dashboard", "deploy", "toolkit/dashboard_app.py", timeout=600)
        if not ok:
            print(out)
            return 1
        print(out)
        return 0
    if args.subcommand in ("stop", "logs", "url"):
        name = "modal-toolkit-dashboard"
        subprocess.run(["modal", "secret", "list", "--json"], capture_output=True, text=True, check=False)
        if args.subcommand == "logs":
            ok, _detail, out = _modal_run("dashboard", "app", "logs", name, timeout=60)
            print(out)
            return 0 if ok else 1
        if args.subcommand == "stop":
            ok, _detail, out = _modal_run("dashboard", "app", "stop", name, timeout=120)
            print(out)
            return 0 if ok else 1
        if args.subcommand == "url":
            ok, _detail, out = _modal_run("dashboard", "app", "list", timeout=60)
            print(out)
            return 0 if ok else 1
    return 2


def _libs_run(args: argparse.Namespace) -> int:
    """Attach point: `mtk libs sync|check` -> the libs package's run (vendored hooks live beside it)."""
    from toolkit import libs as libs_module

    return libs_module.run(args)


# ── Per-package commands (declared by each repo's server/mtk-commands.toml) ──


def _pkg_cmd(args: argparse.Namespace) -> int:
    """Run one manifest-declared command (dispatched by kind)."""
    return pkg_commands.run(
        args._pkg,
        args._spec,
        args,
        probe=_probe,
        modal_run=_modal_run,
        pkg_repo=_pkg_repo,
    )


def _pkg_passthrough(args: argparse.Namespace) -> int:
    """Run a package's whole-surface delegation to its own CLI."""
    return pkg_commands.run_passthrough(args._pkg, args._spec, args, pkg_repo=_pkg_repo)


def _mount_package_commands(sub: argparse._SubParsersAction) -> None:
    """Mount one `mtk <pkg>` group per repo that ships a manifest.

    Discovery is repo-presence (the manifest is the signal): a toolkit-only
    clone mounts nothing, and a repo added to the workspace mounts its
    commands with no edit to this file. A malformed manifest is a loud,
    actionable error rather than a silently missing command group.
    """
    for pkg in cfg.PACKAGES:
        try:
            manifest = pkg_commands.load(_repo_dir(pkg))
        except pkg_commands.CommandManifestError as exc:
            manifest_file, message = exc.args
            raise SystemExit(f"invalid command manifest {manifest_file}: {message}") from None
        if manifest is None:
            continue

        group = sub.add_parser(pkg, help=f"{pkg} package commands (from its mtk-commands.toml)")
        if manifest.passthrough is not None:
            spec = manifest.passthrough
            group.set_defaults(func=_pkg_passthrough, _pkg=pkg, _spec=spec)
            continue

        cmd_sub = group.add_subparsers(dest="subcommand", required=True)
        for spec in manifest.commands:
            child = cmd_sub.add_parser(spec.name, help=spec.summary)
            child.set_defaults(func=_pkg_cmd, _pkg=pkg, _spec=spec)


# ── Parser ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mtk", description="Operator CLI for the Modal Toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="write the shared config (per-package sections)")
    setup.add_argument(
        "--from-env",
        action="store_true",
        help="import inference settings from a pre-existing toolkit config if present",
    )
    setup.add_argument("--base-url", help="shared base URL (applied to all four)")
    setup.add_argument("--token", help="shared bearer token (applied to all four)")
    setup.add_argument("--repos-root", help="parent dir holding the four sibling repos")
    setup.set_defaults(func=cmd_setup)

    cfg_p = sub.add_parser("config", help="inspect + validate the toolkit config")
    cfg_p.set_defaults(func=cmd_config)

    status_p = sub.add_parser("status", help="per-package: configured/enabled/secrets/reachable (start here)")
    status_p.add_argument("--net", dest="net", action="store_true", default=True, help="probe reachability (default)")
    status_p.add_argument("--no-net", dest="net", action="store_false", help="config-only glance (no network)")
    status_p.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="health + drift across all four packages")
    doctor.add_argument("--pkg", help="comma-separated package names (default: all)")
    doctor.add_argument("--json", action="store_true", help="emit JSON")
    doctor.set_defaults(func=cmd_doctor)

    warm = sub.add_parser("warm", help="cold-start a package's GPU worker")
    warm.add_argument("--pkg", help="comma-separated package names")
    warm.add_argument("--all", action="store_true")
    warm.set_defaults(func=cmd_warm)

    shutdown = sub.add_parser("shutdown", help="scale GPU(s) to zero now")
    shutdown.add_argument("--pkg", help="comma-separated package names")
    shutdown.add_argument("--all", action="store_true")
    shutdown.set_defaults(func=cmd_shutdown)

    cost_p = sub.add_parser("cost", help="per-package + blended GPU-hour view")
    cost_p.set_defaults(func=cmd_cost)

    flow = sub.add_parser("flow", help="the ecosystem flowchart, live state")
    flow.add_argument("--push", action="store_true", help="push package flow states to VictoriaMetrics")
    flow.set_defaults(func=cmd_flow)

    metrics_p = sub.add_parser("metrics", help="VictoriaMetrics drop-in: probe the endpoint or show the URL")
    metrics_p.add_argument("subcommand", choices=["url", "test"])
    metrics_p.set_defaults(func=cmd_metrics)

    secrets_p = sub.add_parser("secrets", help="fleet-level Modal Secrets: check/create/rotate")
    secrets_sub = secrets_p.add_subparsers(dest="secrets_command", required=True)

    secrets_check = secrets_sub.add_parser("check", help="diff manifests against the Modal workspace (read-only)")
    secrets_check.add_argument("--pkg", help="comma-separated package names (default: all)")
    secrets_check.add_argument("--json", action="store_true", help="emit per-package JSON (exit 0 always)")
    secrets_check.set_defaults(func=cmd_secrets)

    secrets_create = secrets_sub.add_parser("create", help="create missing secrets (generated or prompted)")
    secrets_create.add_argument("--pkg", help="comma-separated package names (default: all)")
    secrets_create.add_argument("--all", action="store_true", help="every manifest-bearing package")
    secrets_create.add_argument(
        "--force",
        action="store_true",
        help="regenerate ALL keys of existing secrets (live deployments must switch)",
    )
    secrets_create.set_defaults(func=cmd_secrets)

    secrets_rotate = secrets_sub.add_parser(
        "rotate",
        help="regenerate an existing secret's keys after confirmation (create --force + gate)",
    )
    secrets_rotate.add_argument("name", help="exact Modal secret name, e.g. modal-vault-secret")
    secrets_rotate.set_defaults(func=cmd_secrets)

    dashboard_p = sub.add_parser("dashboard", help="fleet dashboard app (deploy/stop/logs/url)")
    dashboard_p.add_argument("subcommand", choices=["deploy", "stop", "logs", "url"])
    dashboard_p.set_defaults(func=cmd_dashboard)

    libs_p = sub.add_parser("libs", help="shared-library vending: sync (vendor verbatim) | check (parity, CI gate)")
    libs_sub = libs_p.add_subparsers(dest="libs_command", required=True)
    libs_sync = libs_sub.add_parser("sync", help="refresh vendored copies from modal-shared-libs")
    libs_sync.add_argument("--pkg", help="comma-separated package names (default: all targets)")
    libs_check = libs_sub.add_parser("check", help="parity-check vendored copies (md5)")
    libs_check.add_argument("--pkg", help="comma-separated package names (default: all targets)")
    libs_p.set_defaults(func=_libs_run)

    # Per-package command groups come from each repo's server/mtk-commands.toml.
    _mount_package_commands(sub)

    return p


def main() -> int:
    parser = build_parser()
    args, extras = parser.parse_known_args()
    if extras:
        # A passthrough command forwards the tail verbatim (flags included); any
        # other command has no such escape hatch, so unknown args are an error.
        spec = getattr(args, "_spec", None)
        accepts = isinstance(spec, pkg_commands.PassthroughSpec) or (
            isinstance(spec, pkg_commands.CommandSpec) and spec.passthrough
        )
        if not accepts:
            parser.error(f"unrecognized arguments: {' '.join(extras)}")
        args.args = extras[1:] if extras and extras[0] == "--" else extras
    try:
        return int(args.func(args))
    except SystemExit:
        raise
    except (httpx.HTTPError, OSError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
