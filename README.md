# Modal Toolkit

One operator CLI for the **Modal Toolkit**: standalone GPU/research packages that work alone or together: embeddings, LLM inference, vision, LoRA fine-tune, and the hosted vault + MCP memory plane, plus the eval harness that starts the lifecycle.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-pink.svg)](https://github.com/sponsors/kylebrodeur)

## The packages

This CLI orchestrates the packages; each is its own standalone repo and can be used without the toolkit:

- **[modal-embedding-server](https://github.com/kylebrodeur/modal-embedding-server):** GPU-backed embeddings with a monotonic sync protocol for private-first search.
- **[modal-inference-server](https://github.com/kylebrodeur/modal-inference-server):** OpenAI-compatible LLM inference with hot-set routing and scale-to-zero.
- **[modal-vision-server](https://github.com/kylebrodeur/modal-vision-server):** Generic vision classification: pick your model (open_clip or transformers weights), your segmenter (SAM 2.1 or none), and your fast gate (self, cheap CLIP, deterministic script, or external endpoint). The BioCLIP plant stack ships as the example card.
- **[modal-vault-server](https://github.com/kylebrodeur/modal-vault-server):** Hosted Obsidian vault + MCP memory plane: server-side clone via Headless Sync, searchable by MCP-speaking agents.
- **[modal-finetune-server](https://github.com/kylebrodeur/modal-finetune-server):** Profile-driven LoRA fine-tune and GGUF pipeline with an honest eval gate.
- **[modal-toolkit](https://github.com/kylebrodeur/modal-toolkit):** One operator CLI (`mtk`) that runs the fleet: `doctor`, `secrets`, `warm --all`, `shutdown --all`, `cost`, `flow`, `dashboard`.
- **[embed-eval-on-your-vault](https://github.com/kylebrodeur/embed-eval-on-your-vault):** the eval-first pattern (benchmark embedding models on your own data before you deploy) as a single-file, zero-dependency harness.

## What `mtk` adds

A single entry point for **fleet-level commands** that don't belong in any one package:

| Command | Purpose |
| :--- | :--- |
| `mtk setup` | Write the shared config (`~/.config/modal-toolkit/config.json`, mode 600) |
| `mtk config` | Inspect + validate the shared config |
| `mtk status [--no-net]` | One table: configured/enabled/secrets/reachable per package (`--no-net` = config-only glance) |
| `mtk doctor [--pkg ... --json]` | Health + drift across all packages in one shot (mark per package: ok / unhealthy / unreachable / cold / repo) |
| `mtk warm [--pkg ... --all]` | Cold-start a package's GPU worker |
| `mtk shutdown [--pkg ... --all]` | Scale GPU(s) to zero now |
| `mtk cost` | Per-package + blended always-on GPU-hour view |
| `mtk flow` | The ecosystem flowchart, rendered against live state |
| `mtk dashboard deploy \| stop \| logs \| url` | The fleet dashboard: one CPU app, a card per GPU-serving package |
| `mtk secrets` | Fleet-level Modal Secrets: `check` (drift diff), `create`, `rotate` |
| `mtk metrics` | Push app events/rates to VictoriaMetrics (or InfluxDB) from any package or Modal app; stdlib-only writer + test command |
| `mtk libs sync \| check` | Vendor the family's shared stdlib modules (modal-shared-libs) verbatim into each package; `check` is the md5 parity gate CI runs |

Per-package deep work is declared by each repo's own command manifest
(`server/mtk-commands.toml`); `mtk` mounts one command group per repo that
ships one, so adding a command is a repo-side edit:

| Command | Purpose |
| :--- | :--- |
| `mtk embedding sync \| reindex` | Wrapper for the embedding server's own commands |
| `mtk inference <command> ...` | Passthrough to the inference repo's own `modal-inference` CLI |
| `mtk vision deploy \| warm` | Wrapper for the vision server's own commands |
| `mtk finetune train \| eval \| gguf` | Wrapper for the finetune pipeline's own commands |
| `mtk vault status \| sync \| pull-only \| ...` | Passthrough to `ob` inside the vault container (never local sync) |

The groups above mount only when the package's repo is present in the
workspace (and `mtk doctor` lists what each declares); a toolkit-only clone
offers just the fleet-level commands.

## The fleet dashboard

One CPU-only Modal app (`mtk dashboard deploy`) that shows the four GPU
packages (embedding, inference, vision, finetune) as cards, one adapter
each. Each adapter answers two questions: an unauthenticated
health probe for the state pill and one authenticated read for the
headline. The probes differ per card, matching what each server
actually exposes:

- **embedding:** unauth `/health`, then `GET /stats` for the headline
  (collections count + loaded models).
- **inference:** no `/health` exists: the adapter probes unauth
  `/api/tags` first and falls back to unauth `/v1/models`; the headline
  is the served-alias list (the hot set) from the authed read.
- **vision:** unauth `/health`, then the authed headline comes from the
  same `/health` payload (model + segmenter + fast gate); the identify
  surface is POST-only and never probed.
- **finetune:** a job package with no server and no `/health`: the card
  states that nature and makes no remote probe.

Session auth is its own bearer credential
(`MODAL_TOOLKIT_DASHBOARD_TOKEN`, hash-fragment autologin supported).
Billing is fleet-wide: metered month/today, workspace billed vs
credits, per-app split, and a workspace-disabled banner.

```bash
modal secret create modal-toolkit-dashboard-secret MODAL_TOOLKIT_DASHBOARD_TOKEN=$(openssl rand -hex 32)
mtk dashboard deploy   # builds + deploys the CPU app (config Volume reads dashboard-config.json if present)
# open the printed URL ... or visit /_toolkit/login#key=<credential>
mtk dashboard stop     # scale to zero
```

Deploying and keying the dashboard is per-operator: create your own
secret, deploy under your own workspace. Nothing in the packages
depends on this app existing.

The dashboard is read-only across packages (single-writer per package)
and links to each package's own deep surface (the inference dashboard
keeps runtime tuning, catalog, and slot telemetry).

## The fleet at a glance

![mtk doctor output (rendered)](docs/images/doctor.svg)
![mtk cost output (rendered)](docs/images/cost.svg)
![mtk flow output (rendered)](docs/images/flow.svg)

## The lifecycle

![Lifecycle: research to eval to deploy to sync, with mtk underneath](docs/images/lifecycle.svg)

## Quick Start

```bash
uv sync --group dev

# 1. Set up the shared config (per-package URL/token/defaults)
mtk setup --repos-root /path/to/your/repos-dir
# (or import an existing config: mtk setup --from-env)

# 2. Inspect
mtk config

# 3. Health across the fleet
mtk doctor
```

## Configuration

One file, one section per package. Tokens are never printed by any `mtk` command.

```
~/.config/modal-toolkit/config.json
{
  "embedding":  {"base_url": "...", "token": "...", "model": "...", "dim": 768},
  "inference":  {"base_url": "...", "token": "...", "alias": "...", "dashboard_url": "...", "dashboard_token": "..."},
  "vision":     {"base_url": "...", "token": "...", "model": "...", "gpu": "T4"},
  "finetune":   {"base_model": "...", "adapter_repo": "...", "hf_user": "..."},
  "coding":     {"base_url": "...", "token": "...", "alias": "..."},
  "vault":      {"base_url": "...", "token": "..."},
  "repos":      {"root": "/path/to/your/repos-dir"}
}
```

Override anything via env (no config edit required):

- `MODAL_TOOLKIT_CONFIG`: use a different config file path
- `MODAL_TOOLKIT_REPOS`: where the sibling package repos live
- `MODAL_BASE_URL`, `MODAL_PROXY_TOKEN`: the standard per-package overrides
- `MODAL_VISION_GPU`, `MODAL_VISION_MODEL`, `MODAL_FINETUNE_BASE`, `MODAL_FINETUNE_ADAPTER`, etc.

## The vault lane (`mtk vault`)

`mtk vault status | sync | pull-only | sync-on-write | mirror-remote |
continuous | list-remote | list-local | config | logs | exec` is an
operator passthrough to `ob` running INSIDE the `modal-vault-server`
container. The `ob` binary, its login state, and the clone all live in
that container, Volume-backed at `/vault`: every command uses
`modal container exec` against a `modal-vault-server` container id
(waking the scale-to-zero app first) and never syncs, clones, or holds
vault state on the local machine. `pull-only` switches the clone to
pull-only mode; `sync` is the bidirectional mode. One gate applies to
every command: the vault section's `base_url` in the toolkit config.

## Cost model

Modal bills per GPU-hour, never per token. `mtk cost` reports always-on burn per package and the fleet total, using published Modal GPU rates:

```
H200: $2.35/hr    B200: $3.53/hr    H100: $2.10/hr
L40S: $1.10/hr    A10G: $0.60/hr    L4:   $0.80/hr    T4: $0.53/hr
```

The **actual cost** depends on how long containers stay warm; the toolkit's job is to make that visible in one call (and `mtk shutdown --all` the escape hatch).

## Examples

See [`examples/`](examples/) for the full lifecycle: setup → doctor → warm → use → shutdown.

## Operator deploy overlays (`deploys/`)

Upstream repos are **SOURCE, never deploy targets.** Each operator deploys a
private instance from a deploy directory that references the upstream
checkout by path:

```
<operator-repo>/deploys/<pkg>/
├── deploy.json   # instance name (env), knob values, secret NAMES
└── deploy.sh     # exports the env, runs the upstream repo's deploy command
```

- **The rule that matters:** upstream repos are never modified by deploys;
  operator state lives in the overlay, not in the source repo.
- **Secret-name invariant:** `deploy.json`'s secret NAMES must match the
  repo's `server/secrets.toml` manifest — the manifest is the source of
  truth `mtk secrets` acts against, so overlays never invent names.
- **No `mtk deploy` wrapper (by design):** the package's own CLI + the
  operator overlay beat a generic dispatcher.
- Reference implementation: `writing-duo/deploys/embedding-server/`
  (private instance of modal-embedding-server, deployed + verified).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for ground rules and workflow.

## Part of the Modal Toolkit

Seven repos in the same family: the packages this CLI orchestrates (links in "The packages" above), plus the family's other members listed there too.

## Built on Modal

These packages run on [Modal](https://modal.com), the serverless GPU platform. If you build something with them, share it in the [Modal Slack](https://modal.com/slack) community (`#show-and-tell`). Issues and PRs welcome here on GitHub.

## License

Apache-2.0: see [LICENSE](LICENSE).

---

Built by [Kyle Brodeur](https://kylebrodeur.com) · Model-selection deep-dive: [Choose the Right Embedding Model for Your Data](https://kylebrodeur.substack.com/p/choose-embedding-model-for-your-data)
