"""mtk: the one operator CLI for the Modal Toolkit.

Full lifecycle across all four packages (embedding, inference, vision, finetune):

    mtk setup                            # one-time: write ~/.config/modal-toolkit/config.json
    mtk config                           # inspect + validate the config
    mtk doctor [--pkg ...]               # health + drift report across all four
    mtk warm [--pkg ...] [--all]         # cold-start a package
    mtk shutdown [--pkg ...] [--all]     # scale GPU(s) to zero now
    mtk cost                             # per-package + blended GPU-hour view
    mtk flow                             # the ecosystem flowchart, live state

    mtk metrics url | test               # VictoriaMetrics drop-in: effective URL, probe write

Per-package deep work (thin wrappers that shell into each repo):

    mtk embedding sync | reindex
    mtk vision deploy | warm
    mtk finetune train | eval | gguf | serve

Config lives in ~/.config/modal-toolkit/config.json (mode 600); tokens are
never printed. Set $MODAL_TOOLKIT_CONFIG to use a different path; set
per-package env overrides ($MODAL_BASE_URL, $MODAL_PROXY_TOKEN, ...) to
override the file without editing it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from toolkit import config as cfg

TIMEOUT_S = 20

# The four packages, in dependency order (embedding first, finetune last).
PACKAGE_ORDER = ("embedding", "inference", "vision", "finetune")

# Health paths (mirrors what each server exposes).
HEALTH: dict[str, str] = {
    "embedding": "/health",
    "inference": "/v1/models",  # /health on vLLM has no Ollama equivalent
    "vision": "/health",
    # Finetune is a pipeline, not a server; its "health" is that the Modal App is deployable.
    "finetune": "",
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
    # except finetune whose repo is modal-finetune-server.
    repo_name = "modal-finetune-server" if pkg == "finetune" else f"modal-{pkg}-server"
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


def cmd_doctor(args: argparse.Namespace) -> int:
    """Health + drift report for all four packages (or --pkg subset)."""
    wanted = args.pkg.split(",") if args.pkg else PACKAGE_ORDER
    out: dict[str, Any] = {}
    for pkg in wanted:
        if pkg not in PACKAGE_ORDER:
            print(f"skipping unknown pkg {pkg!r}", file=sys.stderr)
            continue
        out[pkg] = _probe(pkg)
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
    print("  research -> finetune (train/eval->gguf) -> inference (serve adapter)")
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
        "serve": ["deploy", "server/modal_serve.py"],
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

    embedding = sub.add_parser("embedding", help="embedding package verbs (sync, reindex)")
    embedding.add_argument("subcommand", choices=["sync", "reindex"])
    embedding.set_defaults(func=cmd_embedding)

    vision = sub.add_parser("vision", help="vision package verbs (deploy, warm)")
    vision.add_argument("subcommand", choices=["deploy", "warm"])
    vision.set_defaults(func=cmd_vision)

    finetune = sub.add_parser("finetune", help="finetune package verbs (train, eval, gguf, serve)")
    finetune.add_argument("subcommand", choices=["train", "eval", "gguf", "serve"])
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
