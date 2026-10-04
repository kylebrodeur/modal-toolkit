"""Shared config for the Modal Toolkit.

One config file, four per-package sections. Layout:

    {
      "embedding": {"base_url": "...", "token": "...", "model": "...", "dim": 768},
      "inference": {"base_url": "...", "token": "...", "alias": "a",
                    "dashboard_url": "...", "dashboard_token": "..."},
      "vision":    {"base_url": "...", "token": "...", "model": "...", "gpu": "T4"},
      "finetune":  {"base_model": "...", "adapter_repo": "...", "hf_user": "..."},
      "repos":     {"root": "/path/to/parent/dir"}
   }

Read path: `~/.config/modal-toolkit/config.json` (mode 600), overridable with
`$MODAL_TOOLKIT_CONFIG`. Every field is also overridable per-call with
environment variables (`$MODAL_BASE_URL`, `$MODAL_PROXY_TOKEN`, etc.), because
operator overrides should never require editing a config file.

The token is never printed. Only presence is ever reported (`set` vs `unset`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH_DEFAULT = Path.home() / ".config" / "modal-toolkit" / "config.json"

PACKAGES = ("embedding", "inference", "vision", "finetune", "vault")

# The provider name a client harness (Pi/OMP) uses to address the inference
# lane. Configurable so different operators can namespace it differently
# (e.g. `modal-inference/my-model` vs `my-org/my-model`). Default matches the
# service's own install_provider.py default.
PROVIDER_DEFAULT = "modal-inference"
PROVIDER_ENV = "MODAL_INFERENCE_PROVIDER"


def provider_name() -> str:
    """Resolve the provider name: env override > config file > default."""
    env = os.getenv(PROVIDER_ENV, "").strip()
    if env:
        return env
    value = str(load().get("inference", {}).get("provider", "")).strip()
    if value:
        return value
    return PROVIDER_DEFAULT


# Fields each package section carries. Used by setup() to prompt the operator
# and by validate() to tell them which keys are missing.
FIELDS: dict[str, tuple[str, ...]] = {
    "embedding": ("base_url", "token", "model", "dim"),
    "inference": ("base_url", "token", "alias", "dashboard_url", "dashboard_token"),
    "vision": ("base_url", "token", "model", "gpu"),
    "finetune": ("base_model", "adapter_repo", "hf_user"),
    # coding = the mci private fleet core (pkg added 2026-10-06). Dashboard
    # rows stay inference-only until the dashboard-split question resolves.
    "coding": ("base_url", "token", "alias"),
    # vault = modal-vault-server: scale-to-zero CPU app; health is public.
    "vault": ("base_url", "token"),
}

# Environment variables that override file config, per package. Keys inside the
# section names map to these env vars when set.
ENV_OVERRIDES: dict[str, dict[str, str]] = {
    "embedding": {
        "base_url": "MODAL_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "model": "MODAL_EMBED_DEFAULT_MODEL",
        "dim": "MODAL_EMBED_DEFAULT_DIM",
    },
    "inference": {
        "base_url": "MODAL_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "alias": "MODEL_PROFILE",
        "dashboard_url": "MODAL_INFERENCE_DASHBOARD_URL",
        "dashboard_token": "MODAL_INFERENCE_DASHBOARD_TOKEN",
    },
    "coding": {
        "base_url": "MCI_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "alias": "MODEL_PROFILE",
    },
    "vision": {
        "base_url": "MODAL_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "model": "MODAL_VISION_MODEL",
        "gpu": "MODAL_VISION_GPU",
    },
    "finetune": {
        "base_model": "FINETUNE_BASE",
        "adapter_repo": "FINETUNE_ADAPTER",
        "hf_user": "HF_USER",
    },
    "vault": {
        "base_url": "VAULT_BASE_URL",
        "token": "VAULT_TOKEN",
    },
}

# Per-package enabled semantics: a section is "enabled" when its required
# keys resolve (configured). An explicit "enabled": false in the config
# disables it regardless (the dashboard card + status honor it). Verbs on a
# disabled/unconfigured package fail with the EXACT missing keys + fix line.
ENABLED_OVERRIDES_ENV: dict[str, str] = {
    "embedding": "MODAL_EMBED_ENABLED",
    "inference": "MODAL_INFERENCE_ENABLED",
    "vision": "MODAL_VISION_ENABLED",
    "finetune": "MODAL_FINETUNE_ENABLED",
    "vault": "VAULT_ENABLED",
}


def enabled(pkg: str) -> bool:
    """The package's enabled state: enabled key (default: configured-ness)."""
    section_data = section(pkg)
    manual = str(section_data.get("enabled", "")).strip().lower()
    if manual in ("true", "1", "yes"):
        return True
    if manual in ("false", "0", "no"):
        return False
    env_name = ENABLED_OVERRIDES_ENV.get(pkg)
    if env_name:
        env_value = os.getenv(env_name, "").strip().lower()
        if env_value:
            return env_value in ("true", "1", "yes")
    required = REQUIRED_KEYS.get(pkg)
    if required is None:
        return bool(section_data)  # no required spec: any section counts
    return all(section_data.get(key) for key in required)


# The keys a VERB needs before it can run per package (subset of FIELDS).
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "embedding": ("base_url", "token"),
    "inference": ("base_url", "token", "alias"),
    "vision": ("base_url", "token"),
    "vault": ("base_url", "token"),
    # finetune/coding: verb-specific; no single required trio
}

# The one path every deploy/run command needs: where the four sibling repos live.
REPO_ENV = "MODAL_TOOLKIT_REPOS"

# The shared-library vendoring manifest: {module: [target repo dirs relative to repos root]}.
# Canonical source: the modal-shared-libs checkout found beside the packages (or
# $MODAL_SHARED_LIBS_REPO). Targets follow each repo's layout:
# server/ for the FastAPI packages, . for the flat ones.
LIBS_MANIFEST: dict[str, dict[str, Any]] = {
    "hooks.py": {
        "targets": {
            "modal-embedding-server": "server/libs",
            "modal-inference-server": "server/libs",
            "modal-vision-server": "server/libs",
            "modal-finetune-server": "server/libs",
            "modal-vault-server": "server/libs",
            "modal-toolkit": "toolkit/libs",
        }
    }
}

SHARED_LIBS_REPO_ENV = "MODAL_SHARED_LIBS"


def _path() -> Path:
    env = os.getenv("MODAL_TOOLKIT_CONFIG", "").strip()
    if env:
        return Path(env).expanduser()
    return CONFIG_PATH_DEFAULT


def load() -> dict[str, Any]:
    """Read the toolkit config, or {} when absent."""
    p = _path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write(data: dict[str, Any]) -> Path:
    """Persist the toolkit config and return the path. chmod 600."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    p.chmod(0o600)
    return p


def section(pkg: str) -> dict[str, Any]:
    """Resolve one package's effective settings (file + env overrides)."""
    data = load()
    out: dict[str, Any] = dict(data.get(pkg, {}))
    for key, env_name in ENV_OVERRIDES.get(pkg, {}).items():
        value = os.getenv(env_name, "").strip()
        if value:
            out[key] = value
    return out


def repos_root() -> Path:
    """Where the four sibling repos live. Used by the per-package verbs when shelling out."""
    env = os.getenv(REPO_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    # Fallback: read the `repos.root` key from the config.
    value = str(load().get("repos", {}).get("root", "")).strip()
    if value:
        return Path(value).expanduser()
    # Last resort: assume this script lives in <root>/modal-toolkit/toolkit/.
    # Sibling repos live next to modal-toolkit/, so the parent of the checkout is the root.
    return Path(__file__).resolve().parents[2]


def require(pkg: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """Return one package's settings, erroring with the EXACT fix when missing.

    The gate every per-package verb goes through: names the missing keys and
    the two real fix lines (`mtk setup --repos-root ...` / the package's env
    overrides from ENV_OVERRIDES), then exits 2.
    """
    got = section(pkg)
    missing = [key for key in keys if not got.get(key)]
    if missing:
        env_names = [ENV_OVERRIDES.get(pkg, {}).get(key, "<set in config>") for key in missing]
        raise SystemExit(
            f"{pkg} is not configured: missing {', '.join(missing)}. Fix with EITHER:\n"
            f"  mtk setup --repos-root <dir-holding-the-repos>   (writes ~/.config/modal-toolkit/config.json)\n"
            f"  export {env_names[0]}=... {' '.join('export ' + n + '=...' for n in env_names[1:])}"
        )
    return got


def validate() -> dict[str, Any]:
    """Audit the config; per-package state: configured/enabled/missing keys."""
    out: dict[str, Any] = {"config_path": str(_path()), "packages": {}}
    for pkg in PACKAGES:
        got = section(pkg)
        missing = [key for key in FIELDS.get(pkg, ()) if not got.get(key)]
        out["packages"][pkg] = {
            "ok": not missing,
            "enabled": enabled(pkg),
            "missing": missing,
            "configured": sorted(got.keys()),
        }
    return out
