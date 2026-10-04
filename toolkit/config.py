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

PACKAGES = ("embedding", "inference", "vision", "finetune", "vault", "vm")

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
    # vm = modal-victoriametrics-server: the metrics store; every endpoint is
    # Basic-Auth-gated by the binary (url + auth pair, no public health).
    "vm": ("base_url", "auth"),
}

# Environment variables that override file config, per package. Keys inside the
# section names map to these env vars when set.
#
# SHARED env vars (the same name across several packages) are the footgun:
# `MODAL_BASE_URL` names one URL, and a naive map hands it to embedding,
# inference, and vision at once, so one export makes three packages look
# configured and the doctor probes the same lane three times. The fix is
# SHARED_ENV: a shared env var only reaches a package the operator has
# actually CLAIMED (the package has its own config section, or its own
# dedicated env var is set). A per-package env var always applies.
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
        "alias": "MODAL_INFERENCE_PROFILE",
        "dashboard_url": "MODAL_INFERENCE_DASHBOARD_URL",
        "dashboard_token": "MODAL_INFERENCE_DASHBOARD_TOKEN",
    },
    "coding": {
        "base_url": "MCI_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "alias": "MODAL_INFERENCE_PROFILE",
    },
    "vision": {
        "base_url": "MODAL_BASE_URL",
        "token": "MODAL_PROXY_TOKEN",
        "model": "MODAL_VISION_MODEL",
        "gpu": "MODAL_VISION_GPU",
    },
    "finetune": {
        "base_model": "MODAL_FINETUNE_BASE",
        "adapter_repo": "MODAL_FINETUNE_ADAPTER",
        "hf_user": "MODAL_FINETUNE_HF_USER",
    },
    "vault": {
        "base_url": "MODAL_VAULT_BASE_URL",
        "token": "MODAL_VAULT_TOKEN",
    },
    "vm": {
        "base_url": "MODAL_TOOLKIT_VM_URL",
        "auth": "MODAL_TOOLKIT_VM_AUTH",
    },
}

# The env vars that more than one package claims. A shared var only reaches a
# package that has been CLAIMED (see `claimed`): the package has a config
# section of its own, or one of its DEDICATED env vars is set.
#
# `MODAL_PROXY_TOKEN` is shared by embedding, inference, coding, and vision,
# and `MODAL_BASE_URL` by embedding, inference, and vision. `MODAL_INFERENCE_PROFILE`
# is shared by inference and coding, and both of those DO have dedicated env
# vars for their other fields, so the claim test still holds.
SHARED_ENV: frozenset[str] = frozenset(
    name
    for pkg_envs in ENV_OVERRIDES.values()
    for name in pkg_envs.values()
    if sum(name in other.values() for other in ENV_OVERRIDES.values()) > 1
)

# The env vars that are DEDICATED to one package. Setting one of these claims
# that package outright, so a shared var can reach it.
DEDICATED_ENV: dict[str, str] = {
    name: pkg for pkg, pkg_envs in ENV_OVERRIDES.items() for name in pkg_envs.values() if name not in SHARED_ENV
}

# Per-package enabled semantics: a section is "enabled" when its required
# keys resolve (configured). An explicit "enabled": false in the config
# disables it regardless (the dashboard card + status honor it). Commands on a
# disabled/unconfigured package fail with the EXACT missing keys + fix line.
ENABLED_OVERRIDES_ENV: dict[str, str] = {
    "embedding": "MODAL_EMBED_ENABLED",
    "inference": "MODAL_INFERENCE_ENABLED",
    "vision": "MODAL_VISION_ENABLED",
    "finetune": "MODAL_FINETUNE_ENABLED",
    "vault": "MODAL_VAULT_ENABLED",
    "vm": "MODAL_TOOLKIT_VM_ENABLED",
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


# The keys a COMMAND needs before it can run per package (subset of FIELDS).
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "embedding": ("base_url", "token"),
    "inference": ("base_url", "token", "alias"),
    "vision": ("base_url", "token"),
    "vault": ("base_url", "token"),
    "vm": ("base_url", "auth"),
    # finetune/coding: command-specific; no single required trio
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
    """Resolve one package's effective settings (file + env overrides).

    A SHARED env var (one name claimed by several packages) only reaches a
    package the operator has CLAIMED. See `claimed` for what claiming means.
    """
    data = load()
    out: dict[str, Any] = dict(data.get(pkg, {}))
    is_claimed = claimed(pkg, data)
    for key, env_name in ENV_OVERRIDES.get(pkg, {}).items():
        if env_name in SHARED_ENV and not is_claimed:
            continue
        value = os.getenv(env_name, "").strip()
        if value:
            out[key] = value
    return out


def claimed(pkg: str, data: dict[str, Any] | None = None) -> bool:
    """Has the operator claimed this package?

    A package is claimed when EITHER:
      - it has a config section of its own (the file names it), OR
      - one of its DEDICATED env vars is set (the environment names it).

    This is the gate on the SHARED env vars. `MODAL_BASE_URL` names one URL;
    handing it to every package that lists it would make embedding, inference,
    and vision all look configured at the same address, and the doctor would
    probe one lane three times. So a shared var only lands on a package the
    operator has already named in one of those two ways.
    """
    if data is None:
        data = load()
    if data.get(pkg):
        return True
    return any(owner == pkg and os.getenv(name, "").strip() for name, owner in DEDICATED_ENV.items())


def config_path() -> Path:
    """The toolkit config file in effect (env override > the default path)."""
    return _path()


def repos_root() -> Path:
    """Where the four sibling repos live. Used by the per-package commands when shelling out."""
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

    The gate every per-package command goes through: names the missing keys and
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
