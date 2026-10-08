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

Per-package deep work (thin wrappers that shell into each repo):

    mtk embedding sync | reindex
    mtk vision deploy | warm
    mtk finetune train | eval | gguf

Config lives in ~/.config/modal-toolkit/config.json (mode 600); tokens are
never printed. Set $MODAL_TOOLKIT_CONFIG to use a different path; set
per-package env overrides ($MODAL_BASE_URL, $MODAL_PROXY_TOKEN, ...) to
override the file without editing it.
"""

from __future__ import annotations

import argparse
import contextlib
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
from toolkit import secrets as mtk_secrets
from toolkit.secrets import ManifestSecret, ManifestSpec

TIMEOUT_S = 20

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


def _pkg_repo(pkg: str) -> Path:
    """Path to one sibling repo; used when shelling into per-package verbs."""
    # Package names and repo names match exactly (modal-embedding-server etc),
    # except finetune (modal-finetune-server) and vault (modal-vault-server),
    # matching toolkit/secrets.py REPO_SPECIAL.
    special = {"finetune": "modal-finetune-server", "vault": "modal-vault-server"}
    repo_name = special.get(pkg, f"modal-{pkg}-server")
    path = _repos_root() / repo_name
    if not path.exists():
        raise SystemExit(f"{pkg} repo not found at {path}; set {cfg.REPO_ENV} or fix the config's repos.root.")
    return path


def _token_headers(pkg: str) -> dict[str, str]:
    got = cfg.section(pkg)
    token = got.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── Fleet verbs ─────────────────────────────────────────────────────────────


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


def cmd_doctor(args: argparse.Namespace) -> int:
    """Health + drift report for all four packages (or --pkg subset)."""
    wanted = args.pkg.split(",") if args.pkg else PACKAGE_ORDER
    out: dict[str, Any] = {}
    for pkg in wanted:
        if pkg not in PACKAGE_ORDER:
            print(f"skipping unknown pkg {pkg!r}", file=sys.stderr)
            continue
        out[pkg] = _probe(pkg)

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


# ── Secrets verbs ───────────────────────────────────────────────────────────


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
    verb = args.secrets_verb

    if verb == "check":
        statuses = mtk_secrets.check_all(args.pkg.split(",") if args.pkg else None)
        _secrets_json(statuses) if args.json else mtk_secrets._print_table(statuses)
        if args.json:
            return 0  # scripts consume exit codes: --json always 0
        return 1 if any(st.missing for st in statuses.values()) else 0

    if verb == "create":
        return mtk_secrets.create(args)

    if verb == "rotate":
        return _cmd_secrets_rotate(args)

    raise SystemExit(f"unknown secrets subcommand {verb!r}")


def _cmd_secrets_rotate(args: argparse.Namespace) -> int:
    """`mtk secrets rotate <name>`: regenerate ONE manifest-declared secret.

    Its own verb body (Task 2's report anticipates this): `create --force`
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


# ── Metrics verbs ───────────────────────────────────────────────────────────


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


# ── Per-package verbs (thin wrappers that shell into each repo) ─────────────


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


def cmd_vault(args: argparse.Namespace) -> int:
    """`mtk vault <verb>`: operator passthrough to `ob` INSIDE the vault server container.

    The ob binary, its login state, and the clone live in the Modal
    container (Volume-backed); every verb runs there - nothing ever
    syncs on this machine (reference: the writing-duo vault-ob.sh).
    """
    app_name = "modal-vault-server"
    if args.subcommand == "sync":
        cfg.require("vault", ("base_url", "token"))
    ob_by_verb = {
        "status": [f"ob sync-status --path {VAULT_CLONE_DIR}"],
        "sync": [f"ob sync --path {VAULT_CLONE_DIR}"],
        "pull-only": [f"ob sync-config --mode pull-only --path {VAULT_CLONE_DIR}"],
        "list-remote": ["ob sync-list-remote"],
        "list-local": ["ob sync-list-local"],
        "config": [f"ob sync-config --path {VAULT_CLONE_DIR}"],
        "exec": [f"ob {args.ob_args} ".rstrip()],
    }
    if args.subcommand == "logs":
        return _vault_logs(app_name)
    commands = ob_by_verb[args.subcommand]
    return _vault_exec(app_name, commands[0])


VAULT_CLONE_DIR = "/vault"


def _vault_url(cfg_pkg: dict[str, Any]) -> str:
    return str(cfg_pkg.get("base_url", "")).rstrip("/")


def _vault_container_id(app_name: str, base_url: str) -> str:
    """Wake the scale-to-zero app, then poll for its live container id."""
    import urllib.request

    with contextlib.suppress(Exception):
        urllib.request.urlopen(base_url + "/health", timeout=120)
    for _ in range(20):
        listing = subprocess.run(["modal", "container", "list"], capture_output=True, text=True, check=False)
        for line in listing.stdout.splitlines():
            if "ta-" in line:
                candidate = line.split("│")[2].strip() if "│" in line else ""
                if candidate.startswith("ta-"):
                    return candidate
        time.sleep(2)
    raise SystemExit(f"no live container for {app_name}; is it deployed?")


def _vault_exec(app_name: str, command: str) -> int:
    section = cfg.load().get("vault", {})
    base_url = _vault_url(section)
    if not base_url:
        raise SystemExit("vault not configured: run mtk setup (or set $VAULT_BASE_URL)")
    cid = _vault_container_id(app_name, base_url)
    proc = subprocess.run(
        ["modal", "container", "exec", cid, "--", "sh", "-c", f"export XDG_CONFIG_HOME=/vault/state; exec {command}"],
        check=False,
    )
    return proc.returncode


def _vault_logs(app_name: str) -> int:
    section = cfg.load().get("vault", {})
    base_url = _vault_url(section)
    if not base_url:
        raise SystemExit("vault not configured: run mtk setup (or set $VAULT_BASE_URL)")
    cid = _vault_container_id(app_name, base_url)
    proc = subprocess.run(
        [
            "modal",
            "container",
            "exec",
            cid,
            "--",
            "sh",
            "-c",
            "tail -n 40 '/vault/state/obsidian-headless/sync/'*'/sync.log' 2>/dev/null || true",
        ],
        check=False,
    )
    return proc.returncode


def cmd_embedding(args: argparse.Namespace) -> int:
    """Per-package dispatch for the embedding server."""
    if args.subcommand == "sync":
        cfg.require("embedding", ("base_url", "token"))
        print("sync-down is a client-side action: see modal-embedding-server README (Phase 4 of the flight path).")
        return 0
    if args.subcommand == "reindex":
        subprocess.run(["uv", "run", "modal", "run", "server/app.py"], cwd=_pkg_repo("embedding"), check=False)
        return 0
    raise SystemExit(f"unknown embedding subcommand {args.subcommand!r}")


def cmd_vision(args: argparse.Namespace) -> int:
    """Per-package dispatch for the vision server."""
    if args.subcommand == "deploy":
        ok, _detail, out = _modal_run("vision", "deploy", "server/app.py", timeout=600)
        print("ok" if ok else f"failed ({out[:200]})")
        return 0 if ok else 1
    if args.subcommand == "warm":
        state = _probe("vision")
        print(f"vision probe status={state.get('status')}")
        return 0
    raise SystemExit(f"unknown vision subcommand {args.subcommand!r}")


def cmd_finetune(args: argparse.Namespace) -> int:
    """Per-package dispatch for the finetune pipeline."""
    mapping = {
        "train": ["run", "server/train_modal.py"],
        "eval": ["run", "eval/run_eval.py"],
        "gguf": ["run", "server/gguf_pipeline_modal.py"],
    }
    if args.subcommand not in mapping:
        raise SystemExit(f"unknown finetune subcommand {args.subcommand!r}")
    argv = mapping[args.subcommand]
    # Forward any extra args (`--` passthrough).
    passthrough = args.args or []
    ok, _detail, output = _modal_run("finetune", *argv, *passthrough, timeout=7200)
    print(output or ("ok" if ok else "failed"))
    return 0 if ok else 1


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
    secrets_sub = secrets_p.add_subparsers(dest="secrets_verb", required=True)

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

    embedding = sub.add_parser("embedding", help="embedding package verbs (sync, reindex)")
    embedding.add_argument("subcommand", choices=["sync", "reindex"])
    embedding.set_defaults(func=cmd_embedding)

    vision = sub.add_parser("vision", help="vision package verbs (deploy, warm)")
    vision.add_argument("subcommand", choices=["deploy", "warm"])
    vision.set_defaults(func=cmd_vision)

    dashboard_p = sub.add_parser("dashboard", help="fleet dashboard app (deploy/stop/logs/url)")
    dashboard_p.add_argument("subcommand", choices=["deploy", "stop", "logs", "url"])
    dashboard_p.set_defaults(func=cmd_dashboard)

    vault = sub.add_parser("vault", help="vault package verbs: ob inside the container (never local sync)")
    vault.add_argument(
        "subcommand", choices=["status", "sync", "pull-only", "list-remote", "list-local", "config", "logs", "exec"]
    )
    vault.add_argument("ob_args", nargs="*", help="passthrough args for the exec/config verbs")
    vault.set_defaults(func=cmd_vault)

    finetune = sub.add_parser("finetune", help="finetune package verbs (train, eval, gguf)")
    finetune.add_argument("subcommand", choices=["train", "eval", "gguf"])
    finetune.add_argument("args", nargs="*", help="passthrough args to the underlying modal command")
    finetune.set_defaults(func=cmd_finetune)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except SystemExit:
        raise
    except (httpx.HTTPError, OSError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
