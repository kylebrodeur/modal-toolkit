"""U6 deploy root: the fleet dashboard as a CPU-only Modal Function.

Wraps `build_fleet_api` with the dashboard config Volume mounted;
`mtk dashboard deploy|stop` manages the app (see toolkit/cli.py).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import modal

from toolkit.dashboard.adapters.embedding import ADAPTER as EMBEDDING
from toolkit.dashboard.adapters.finetune import ADAPTER as FINETUNE
from toolkit.dashboard.adapters.inference import ADAPTER as INFERENCE
from toolkit.dashboard.adapters.vision import ADAPTER as VISION
from toolkit.dashboard.adapters.vm import ADAPTER as VM
from toolkit.dashboard.api import build_fleet_api

CONFIG_VOLUME_NAME = "modal-toolkit-dashboard"
CONFIG_MOUNT = "/config"
CONFIG_FILE = f"{CONFIG_MOUNT}/dashboard-config.json"

ADAPTERS = {"embedding": EMBEDDING, "inference": INFERENCE, "vision": VISION, "finetune": FINETUNE, "vm": VM}


def _load_cfg() -> dict:
    path = Path(CONFIG_FILE)
    if path.exists():
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(path.read_text())
            if isinstance(value, dict):
                return value
    return {}


image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "fastapi>=0.115,<1", "httpx>=0.27,<1", "modal-toolkit"
)

# The dashboard client is a static bundle built from dashboard-client/ by
# `npm run build` (dashboard-client/dist/ is committed). It has to be IN the
# image: the wheel only includes the toolkit/ package, so client-dist/ never
# shipped, and the dashboard served a 404 for /_toolkit and every asset
# under it.
#
# The API serves the bundle from toolkit/client-dist/assets/, so the bundle
# lands beside the package, not at the image root.
image = image.copy_local_file("dashboard-client/index.html", "toolkit/client-dist/index.html").copy_local_file(
    "dashboard-client/dist/dashboard.js", "toolkit/client-dist/assets/dashboard.js"
)

app = modal.App(
    "modal-toolkit-dashboard",
    image=image,
    secrets=[modal.Secret.from_name("modal-toolkit-dashboard-secret")],
)

volume = modal.Volume.from_name(CONFIG_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=image,
    volumes={CONFIG_MOUNT: volume},
    scaledown_window=300,
    max_containers=1,
)
@modal.asgi_app()
def dashboard() -> None:
    cfg = _load_cfg()
    client_dir = Path(__file__).resolve().parent / "client-dist"
    return build_fleet_api(
        package_cfg=cfg.get("packages", cfg),
        adapters=ADAPTERS,
        client_dir=client_dir if client_dir.exists() else None,
    )
