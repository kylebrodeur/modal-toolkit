---
name: mtk
description: Use when setting up or operating a Modal Toolkit project with the mtk CLI: init, pull, deploy, seed, skills, doctor, secrets, and the per-package overlays under deploys/.
---

# mtk: the Modal Toolkit CLI

`mtk` scaffolds a project, vendors tag-pinned package copies, gates secrets,
and deploys each package from the project's own overlay. Install with
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
  `deploy.json` (app name `<project>-<pkg>`), a `deploy.sh`, and (for packages
  with staged artifacts) a `seed.json`.
- The vendored copy under `vendor/<pkg>-server/` is read-only: any drift refuses
  a deploy. Customise from the overlay (config, env, hooks, seed.json), or pass
  the explicit `--allow-local-changes`.
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
| `mtk pull <pkg> --tag <release>` | Vendor a tag-pinned copy into vendor/<pkg>-server/ |
| `mtk seed <pkg>` | Stage pre-deploy artifacts (model weights) from the overlay's seed.json |
| `mtk deploy <pkg>` | Deploy from the overlay after the preflight and secrets gate |
| `mtk doctor [--pkg ...]` | Health and drift across the packages in use |
| `mtk status --no-net` | Config-only view per package |
| `mtk secrets check \| create \| rotate` | Manifest vs workspace; create or rotate secrets |
| `mtk shutdown --pkg <p>` | Scale a worker to zero (vision's stop path is an undeploy and asks first) |
| `mtk cost` | Always-on GPU-hour view |

Package names: `vault`, `embedding`, `inference`, `vision`, `finetune`, `vm` (the metrics store).

## Scope: the fleet commands report on what is in use

`mtk doctor`, `mtk status`, and `mtk flow` mention only the packages this
project is actually running: a package pulled into the project, or named in
the config file, or claimed by one of its dedicated env vars. A package
that is none of those gets no line, so there is nothing to read for a
server that was never set up. `mtk doctor --pkg <name>` names a package
explicitly and is always reported.

One URL names one lane. `MODAL_BASE_URL` and `MODAL_PROXY_TOKEN` are
shared, so they only reach a package you have claimed. Use the dedicated
vars (`MODAL_VAULT_BASE_URL`, `MODAL_TOOLKIT_VM_URL`, ...) to claim a
lane outright, and `mtk setup --pkg <name> --base-url ...` to set one from
the CLI.

## Skills

Each server repo declares its own lifecycle skills in
`server/mtk-skills.toml` (names `mtk-<pkg>-<stage>`; stages: `init-config`,
`setup-hooks`, `build-deploy`, `develop`). `mtk skills` lists the skills that
apply to this project right now, filtered by what is vendored, configured, and
deployed; run it after `mtk init` and again before deploy work. When the
suggestion names a skill, load its SKILL.md from the printed path before
working on that package. Skills for packages this project has not vendored
never appear, so an agent never reads facts about a server that is not in use.

## When something fails

- "no active Modal profile": run `modal setup`.
- "cannot reach your Modal account": check network, then `modal setup`.
- "no readable secrets manifest": vendor the package first (`mtk pull <pkg> --tag <release>`). The gate fails closed.
- "app_name must be ...": the overlay's `deploy.json` must name `<project>-<pkg>`.
