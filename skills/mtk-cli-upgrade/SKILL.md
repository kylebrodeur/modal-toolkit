---
name: mtk-cli-upgrade
description: Use when upgrading the modal-toolkit checkout, running mtk fleet commands (doctor, secrets, dashboard, metrics, libs), or wiring shared modules into packages.
license: Apache-2.0
metadata:
  author: kylebrodeur
  family: modal-toolkit
  repo: modal-toolkit
---

# mtk: upgrade and fleet discipline

## Upgrade by tag, then verify

```bash
git fetch --tags && git checkout <tag>        # upgrade unit is the tag, never a branch
uv run pytest tests -q                        # must pass before you use the new tag
uv run ruff check . && uv run ruff format --check .
mtk libs check                                # vendored shared-module parity
```

## Deploy rules

- Deploys go through a project overlay: `deploys/<pkg>-server/` with a `deploy.json`
  (app name `<project>-<pkg>`) and a `deploy.sh`. Run `mtk deploy <pkg>`.
- Never run `modal deploy` inside a package or toolkit checkout. Checkouts are source.
- The provenance preflight refuses a plain checkout:
  `tools/guards/deploy-provenance.sh <overlay-dir>` (from the system workspace).

## Fleet commands

- State: `mtk doctor`, `mtk flow`, `mtk cost`
- Power: `mtk warm --all`, `mtk shutdown --all`. A vision stop is an undeploy and asks first.
- Secrets: `mtk secrets check | create | rotate`. Rotation invalidates live
  deployments, so ask before running it.
- Observe: `mtk dashboard deploy | stop | logs | url`, `mtk metrics url | test`
- Shared modules: `mtk libs sync | check`. `check` is the CI gate that keeps
  vendored copies byte-identical. `sync` restores them.

## Agent files

`mtk init` asks whether to add `AGENTS.md` and `llms.txt`. An existing
`AGENTS.md` is changed only inside the `<!-- mtk:begin -->` and
`<!-- mtk:end -->` markers.
