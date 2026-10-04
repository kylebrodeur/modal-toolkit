# Modal Toolkit

One operator CLI for the **Modal Toolkit** — four standalone GPU packages that work alone or together: embeddings, LLM inference, vision, and LoRA fine-tune.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-pink.svg)](https://github.com/sponsors/kylebrodeur)

## The four packages

This CLI orchestrates. Each package is its own standalone repo and can be used without the toolkit:

- **[modal-embedding-server](https://github.com/kylebrodeur/modal-embedding-server):** GPU-backed embeddings with a monotonic sync protocol for private-first search.
- **[modal-inference-server](https://github.com/kylebrodeur/modal-inference-server):** OpenAI-compatible LLM inference with hot-set routing and scale-to-zero.
- **[modal-vision-server](https://github.com/kylebrodeur/modal-vision-server):** Specialized vision classification (BioCLIP-2) with adaptive SAM 2.1 segmentation.
- **[modal-finetune-server](https://github.com/kylebrodeur/modal-finetune-server):** Profile-driven LoRA fine-tune and GGUF pipeline with an honest eval gate.

## What `mtk` adds

A single entry point for **fleet-level verbs** that don't belong in any one package:

| Command | Purpose |
| :--- | :--- |
| `mtk setup` | Write the shared config (`~/.config/modal-toolkit/config.json`, mode 600) |
| `mtk config` | Inspect + validate the shared config |
| `mtk doctor [--pkg ... --json]` | Health + drift across all four packages in one shot |
| `mtk warm [--pkg ... --all]` | Cold-start a package's GPU worker |
| `mtk shutdown [--pkg ... --all]` | Scale GPU(s) to zero now |
| `mtk cost` | Per-package + blended always-on GPU-hour view |
| `mtk flow` | The ecosystem flowchart, rendered against live state |

Per-package deep work (thin wrappers that shell into each sibling repo):

```
mtk embedding sync | reindex
mtk vision deploy | warm
mtk finetune train | eval | gguf | serve
```

## Quick Start

```bash
uv sync --group dev

# 1. Set up the shared config (per-package URL/token/defaults)
mtk setup --repos-root /path/to/your/repos-dir
# (or import an existing mci config: mtk setup --from-env)

# 2. Inspect
mtk config

# 3. Health across the fleet
mtk doctor
```

## Configuration

One file, four per-package sections. Tokens are never printed by any `mtk` verb.

```
~/.config/modal-toolkit/config.json
{
  "embedding":  {"base_url": "...", "token": "...", "model": "...", "dim": 768},
  "inference":  {"base_url": "...", "token": "...", "alias": "...", "dashboard_url": "...", "dashboard_token": "..."},
  "vision":     {"base_url": "...", "token": "...", "model": "...", "gpu": "T4"},
  "finetune":   {"base_model": "...", "adapter_repo": "...", "hf_user": "..."},
  "repos":      {"root": "/path/to/your/repos-dir"}
}
```

Override anything via env (no config edit required):

- `MODAL_TOOLKIT_CONFIG` — use a different config file path
- `MODAL_TOOLKIT_REPOS` — where the four sibling repos live
- `MODAL_BASE_URL`, `MODAL_PROXY_TOKEN` — the standard per-package overrides
- `MODAL_VISION_GPU`, `MODAL_VISION_MODEL`, `FINETUNE_BASE`, `FINETUNE_ADAPTER`, etc.

## Cost model

Modal bills per GPU-hour, never per token. `mtk cost` reports always-on burn per package and the fleet total, using published Modal GPU rates:

```
H200: $2.35/hr    B200: $3.53/hr    H100: $2.10/hr
L40S: $1.10/hr    A10G: $0.60/hr    L4:   $0.80/hr    T4: $0.53/hr
```

The **actual cost** depends on how long containers stay warm; the toolkit's job is to make that visible in one call (and `mtk shutdown --all` the escape hatch).

## Examples

See [`examples/`](examples/) for the full lifecycle: setup → doctor → warm → use → shutdown.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for ground rules and workflow.

## Part of the Modal Toolkit

This is the fifth standalone repo in the same family. The other four are the packages it orchestrates; see links above.

## License

Apache-2.0 — see [LICENSE](LICENSE).

---

Built by [Kyle Brodeur](https://kylebrodeur.com) · Model-selection deep-dive: [Choose the Right Embedding Model for Your Data](https://kylebrodeur.substack.com/p/choose-embedding-model-for-your-data)