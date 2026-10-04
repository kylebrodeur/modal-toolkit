"""Package-list parity across the toolkit modules.

Three modules each carry a package list, and they had walked apart once (vault
was registered in `secrets.PACKAGES` but missing from `config.PACKAGES` and
`cli.PACKAGE_ORDER`, so `mtk secrets create --pkg vault` worked while
`mtk doctor --pkg vault` said "skipping unknown pkg 'vault'"). These tests pin
the relationships so the lists cannot drift silently again.

The intended shape:
  - `cli.PACKAGE_ORDER` is the fleet roll-up order (doctor/status/cost/flow)
    and is the SUPERSET: it additionally carries `coding` (the private mci
    fleet core, no public config surface).
  - `config.PACKAGES` is the public configurable set.
  - `secrets.PACKAGES` covers every manifest-bearing repo: the config set plus
    `coding` (mci ships a secrets manifest but no config section).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli as mtk_cli
from toolkit import config as cfg
from toolkit import secrets as mtk_secrets


def test_cli_package_order_is_superset_of_config_packages():
    assert set(cfg.PACKAGES) <= set(mtk_cli.PACKAGE_ORDER)


def test_secrets_covers_every_config_package():
    assert set(cfg.PACKAGES) <= set(mtk_secrets.PACKAGES)


def test_extra_packages_are_exactly_coding():
    # coding = the private mci core: it ships a secrets manifest, but has no
    # public config section and is not a public deploy package.
    assert set(mtk_cli.PACKAGE_ORDER) - set(cfg.PACKAGES) == {"coding"}
    assert set(mtk_secrets.PACKAGES) - set(cfg.PACKAGES) == {"coding"}


def test_every_config_package_has_a_repo_special_or_plain_name():
    # A package with no REPO_SPECIAL entry must resolve via the plain
    # modal-<pkg>-server pattern; otherwise its repo lookup silently misses.
    for pkg in cfg.PACKAGES:
        name = mtk_secrets.REPO_SPECIAL.get(pkg, f"modal-{pkg}-server")
        assert name.startswith("modal-")
