"""`mtk seed <pkg>`: pre-deploy artifact staging, declared by the project.

The declaration is deploys/<pkg>-server/seed.json (the vendor of the upstream
repo carries the command; the project carries the decision). v1 shape:

    {
      "schemaVersion": 1,
      "<unit>": {"profile": str, "aliases": [str, ...]},
      ...
    }

A "$comment" key at the top level is allowed and never data. Inference's
upstream command is `modal run modal_service.py::bootstrap_model --alias <a>`;
the runner is injected so tests never shell out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from toolkit import project as mtk_project

ProjectError = mtk_project.ProjectError

SEED_SCHEMA = 1
SEED_FILE = "seed.json"


def load_declaration(project_root: Path, pkg: str) -> dict:
    """Parse deploys/<pkg>-server/seed.json, or raise with the fix inline."""
    mtk_project.record_path_of(project_root)  # not-a-project refusal comes first
    mtk_project.check_overlay(project_root, pkg)  # then the overlay preflight
    path = project_root / "deploys" / f"{pkg}-server" / SEED_FILE
    if not path.is_file():
        raise ProjectError(
            f"deploys/{pkg}-server/ has no {SEED_FILE}. Add the units to seed before deploy (see the vendor design doc)"
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ProjectError(f"deploys/{pkg}-server/{SEED_FILE} is not valid JSON: {e}") from e
    if data.get("schemaVersion") != SEED_SCHEMA:
        raise ProjectError(f"{SEED_FILE} schemaVersion must be {SEED_SCHEMA} (got {data.get('schemaVersion')!r})")
    units = {k: v for k, v in data.items() if k != "schemaVersion" and not k.startswith("$")}
    if not units:
        raise ProjectError(f"{SEED_FILE} has no units (empty catalogs seed nothing)")
    out: list[tuple[str, dict]] = []
    for name, spec in units.items():
        if not isinstance(spec, dict) or not spec.get("profile"):
            raise ProjectError(f"{SEED_FILE} unit {name!r} needs a profile")
        aliases = spec.get("aliases")
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ProjectError(f"{SEED_FILE} unit {name!r} needs a non-empty aliases list")
        out.append((name, spec))
    return {"schemaVersion": SEED_SCHEMA, "units": out, "path": path}


def run(
    project_root: Path,
    pkg: str,
    *,
    runner: Callable[[list[str]], object],
    force: bool = False,
    env_extra: dict[str, str] | None = None,
) -> None:
    """Run the upstream seed command once per alias, skipping recorded completions.

    The runner receives (argv, cwd, env): cwd is the vendored server dir, so
    the upstream entrypoint resolves to the project's pinned copy, and env
    carries the overlay's deploy.json env plus the unit profile, so the seed
    writes to THIS project's Volume, never the upstream app's.
    """
    decl = load_declaration(project_root, pkg)
    vendor_server = mtk_project.vendor_dir(project_root, pkg) / "server"
    if not vendor_server.is_dir():
        raise ProjectError(f"{vendor_server} is missing. Fix: vendor {pkg!r} first (`mtk pull {pkg} --tag <release>`)")

    # Overlay env: deploy.json "env" object, the same knobs deploy.sh exports.
    overlay = mtk_project.overlay_dir(project_root, pkg)
    overlay_env: dict[str, str] = {}
    deploy_json = overlay / "deploy.json"
    if deploy_json.is_file():
        overlay_env = {str(k): str(v) for k, v in json.loads(deploy_json.read_text()).get("env", {}).items()}

    # Vendor drift gates a seed exactly as it gates a deploy.
    ok, findings = mtk_project.vendor_check(project_root, pkg)
    if not ok:
        raise ProjectError(f"vendor drift for {pkg!r}; seed refused: " + "; ".join(findings))

    record_path = mtk_project.record_path_of(project_root)
    record = json.loads(record_path.read_text())
    done = record.setdefault("packages", {}).setdefault(pkg, {}).setdefault("seeded", [])

    for unit, spec in decl["units"]:
        env = {**overlay_env, "MODAL_INFERENCE_PROFILE": spec["profile"], **(env_extra or {})}
        for alias in spec["aliases"]:
            key = f"{unit}:{alias}"
            if key in done and not force:
                print(f"seeded already: {key}")
                continue
            argv = ["modal", "run", "modal_service.py::bootstrap_model", "--alias", alias]
            runner((argv, vendor_server, env))
            if key not in done:
                done.append(key)

    record_path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"seeded {pkg}: {', '.join(k for k in done) or 'nothing new'}")
