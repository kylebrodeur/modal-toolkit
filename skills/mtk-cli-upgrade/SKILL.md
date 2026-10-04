---
name: mtk-cli-upgrade
description: The operator CLI's upgrade + usage discipline: tag upgrades, vendored-module parity, and the fleet commands (doctor/secrets/dashboard/metrics/libs).

Use when upgrading the modal-toolkit checkout, running mtk fleet commands, or wiring shared modules into packages.
license: Apache-2.0
metadata:
  author: kylebrodeur
  family: modal-toolkit
  repo: modal-toolkit
---

# toolkit (mtk): upgrade + fleet discipline

```bash
# 0. provenance preflight for deploys (from the system workspace):
tools/guards/deploy-provenance.sh <overlay-dir>

# 1. upgrade by tag + verify
git fetch --tags && git checkout <tag>   # v1.4.1 = current
uv run pytest tests -q                   # 103 must pass
uv run ruff check . && uv run ruff format --check .
mtk libs check                           # six-repo vendored parity
```

Fleet commands quick map (deep help: this repo's README + docs/RUNBOOK.md):

- state: `mtk doctor`, `mtk flow`, `mtk cost`
- power: `mtk warm --all` / `mtk shutdown --all`
- secrets: `mtk secrets check|create|rotate` (rotate invalidates live
  users — ask first)
- observe: `mtk dashboard deploy|stop`, `mtk metrics url|test`
- shared modules: `mtk libs sync|check` (`check` = the CI gate that
  keeps vendored copies byte-identical; `sync` restores)

Deploy rule: even the toolkit repo is source, not a target — the ONE
fleet deploy surface = `mtk dashboard deploy` (the dashboard CPU app),
which is Kyle-run; lanes use their own overlay dirs for the GPU
packages.
