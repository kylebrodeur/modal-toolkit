# Quickstart: mtk (the Modal Toolkit CLI)

`mtk` is the one operator CLI for the family. It scaffolds a project, pulls the
package repos, gates secrets, and deploys each package from the project's own
overlay. Start here, then follow the quickstart for each server you use.
Current release: v1.8.0.

## Prerequisites

Install these once on the machine:

- **uv**: Python package and tool manager. https://docs.astral.sh/uv/
- **git**: `mtk pull` clones the package repos.
- **Modal CLI**: `uv tool install modal`, then `modal setup` once to log in.
  `mtk init` checks that a profile is active and the account is reachable.

## 1. Install `mtk` (pick one)

`mtk` is not on PyPI yet. Install it from git.

Global, on your PATH for every project:

```bash
uv tool install git+https://github.com/kylebrodeur/modal-toolkit
mtk --help
```

Ephemeral, run without installing:

```bash
uvx --from git+https://github.com/kylebrodeur/modal-toolkit mtk --help
```

Per project, pinned in the project's environment:

```bash
cd my-project
uv add git+https://github.com/kylebrodeur/modal-toolkit
uv run mtk --help
```

## 2. Set up the toolkit once

```bash
mtk setup --repos-root ~/repos     # enter the token at the prompt, not as a flag
mtk config                         # validate what was written
```

`--repos-root` names the directory that holds your package repos.

## 3. Create a project (or reuse one)

```bash
mtk init my-project                # named: creates ./my-project
cd my-project
```

Or, in an existing project directory, run `mtk init` with no name. The name is the
project's slug, and each app composes as `<slug>-<pkg>`. Pick a slug that is
unique in your Modal workspace. `init` refuses a slug that collides with an
existing app.

## 4. Vendor the packages you use

```bash
mtk pull vault --tag v1.2.2
```

Each vendored package lands in `vendor/<pkg>-server/`: a git checkout pinned to
the exact release tag you pass. The copy is read-only (a drift refuses a
deploy) and records its provenance (repo, tag, commit) next to itself. To
upgrade, pull the same package at a newer tag; `mtk` asks before replacing the
pin.

## 5. Secrets, then deploy

Each package declares its secrets. `mtk deploy` lists any that are missing and
asks you to confirm each one before it creates anything:

```bash
mtk deploy vault
```

Deploy needs `deploys/<pkg>-server/` in your project, with a `deploy.json` that
names the app `<slug>-<pkg>`, and a `deploy.sh`. The per-server quickstarts
show what goes in each overlay. Seedable artifacts (model weights for
inference) are declared in a third overlay file, `seed.json`; stage them with
`mtk seed <pkg>` before the first deploy.

## 6. Health and cost

```bash
mtk doctor              # health and drift across packages
mtk cost                # always-on GPU-hour view
mtk shutdown --pkg <p>  # scale a worker to zero (vision's stop path is an undeploy and asks you to confirm)
```

## Command reference

| Command | What it does |
| :--- | :--- |
| `setup`, `config`, `status` | Write, validate, and summarize the shared config |
| `init [<id>]` | Scaffold a project; checks prerequisites and the account (read-only) |
| `pull <pkg> --tag <release>` | Vendor a tag-pinned copy into vendor/<pkg>-server/ |
| `deploy <pkg>` | Preflight the overlay, gate secrets, run the overlay's `deploy.sh` |
| `secrets check \| create \| rotate` | Manifest vs workspace; create or rotate secrets |
| `libs sync \| check` | Vendor and parity-check the shared libraries |
| `doctor`, `warm`, `shutdown`, `cost`, `flow`, `metrics` | Health, warm, stop, cost, map, telemetry |
| `dashboard deploy \| stop \| logs \| url` | The fleet dashboard app |
| `embedding`, `vision`, `inference`, `finetune`, `vault` | Per-package passthrough groups |

