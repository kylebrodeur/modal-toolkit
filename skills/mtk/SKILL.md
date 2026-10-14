---
name: mtk
description: Use when setting up or operating a Modal Toolkit project with the mtk CLI: init, pull, deploy, doctor, secrets, and the per-package overlays under deploys/.
---

# mtk: the Modal Toolkit CLI

`mtk` scaffolds a project, pulls the package repos, gates secrets, and deploys
each package from the project's own overlay. Install with
`uv tool install modal-toolkit` (needs uv, git, and the Modal CLI).

## Project flow

```bash
mtk setup --repos-root ~/repos   # once: shared config, token at the prompt
mtk init my-project              # scaffold; checks prerequisites and the account
cd my-project
mtk pull vault --tag v1.2.2       # vendor a tag-pinned copy into vendor/vault-server/
mtk deploy vault                 # preflight overlay, confirm secrets, run deploy.sh
mtk doctor --pkg vault           # health and drift
```

## Rules

- Deploy only through `deploys/<pkg>-server/` in the project. The overlay has a
  `deploy.json` (app name `<project>-<pkg>`) and a `deploy.sh`.
- Never run `modal deploy` inside a package checkout.
- `mtk deploy` lists missing secrets and asks before creating each one. Do not
  pass a secret value on the command line.
- `init` never creates apps or secrets. It only checks prerequisites and reads
  the app list to refuse a name collision.
- `init` asks whether to add `AGENTS.md` and `llms.txt`. Pass `--agents` or
  `--no-agents` in scripts. An existing `AGENTS.md` is only changed inside the
  `<!-- mtk:begin -->` and `<!-- mtk:end -->` markers.

## Commands

| Command | Use |
| :--- | :--- |
| `mtk init [<id>]` | Create a project record and `deploys/` |
| `mtk pull <pkg> ...` | Clone package repos; existing checkouts are left alone |
| `mtk deploy <pkg>` | Deploy from the overlay after the preflight and secrets gate |
| `mtk doctor [--pkg ...]` | Health and drift across packages |
| `mtk status --no-net` | Config-only view per package |
| `mtk secrets check \| create \| rotate` | Manifest vs workspace; create or rotate secrets |
| `mtk shutdown --pkg <p>` | Scale a worker to zero (vision's stop path is an undeploy and asks first) |
| `mtk cost` | Always-on GPU-hour view |

Package names: `vault`, `embedding`, `inference`, `vision`, `finetune`.

## When something fails

- "no active Modal profile": run `modal setup`.
- "cannot reach your Modal account": check network, then `modal setup`.
- "no readable secrets manifest": vendor the package first (`mtk pull <pkg> --tag <release>`). The gate fails closed.
- "app_name must be ...": the overlay's `deploy.json` must name `<project>-<pkg>`.
