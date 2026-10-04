"""U6 config plumbing: seed the dashboard config Volume file from the CLI config.

The dashboard app reads `dashboard-config.json` from its Volume;
`mtk dashboard deploy` writes it from the operator's normal
`~/.config/modal-toolkit/config.json` first (one source of truth; the
volume copy is a deploy-time snapshot). Shape:

    {"packages": {"<pkg>": {"base_url": "...", "token": "...",
                            "enabled": true, "app_name": "..."}}}

`enabled`: the section's own `enabled` key, defaulting to true when the
section has a base_url. Tokens come along (the dashboard needs them for
the authed reads); the modal Secret holds the dashboard session token.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config as cfg

PACKAGES_WITH_CARDS = ("embedding", "inference", "vision", "finetune")


def dashboard_config(toolkit_cfg: dict[str, Any]) -> dict[str, Any]:
    """Build the dashboard-config.json body from the toolkit config."""
    packages: dict[str, dict[str, Any]] = {}
    for pkg in PACKAGES_WITH_CARDS:
        section = toolkit_cfg.get(pkg, {})
        if not isinstance(section, dict):
            continue
        entry: dict[str, Any] = {
            "base_url": str(section.get("base_url", "")),
            "token": str(section.get("token", "")),
            "enabled": bool(section.get("enabled", bool(section.get("base_url")))),
        }
        if section.get("app_name"):
            entry["app_name"] = str(section["app_name"])
        packages[pkg] = entry
    return {"packages": packages}


def seed_volume_config(path: Path) -> bool:
    """Write the volume config from the CLI config. True when written."""
    body = dashboard_config(cfg.load())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    return True
