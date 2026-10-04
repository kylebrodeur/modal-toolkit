# Runbook — operating the Modal Toolkit

One CLI, four packages, one GPU budget. Everything here is the current-day
operator surface; nothing is aspirational. For a package's internal mechanics
(hot sets, slot gating, GGUF conversion), that package's own docs are
authoritative — this runbook is the cross-cutting view.

---

## 0. Mental model

```
  Your data ──┬─> [embedding]  ──┐
              ├─> [inference]    ├──> private search / answers
              ├─> [vision]       ┘
              └─> [finetune] → adapters → [inference serve]

  mtk is the operator CLI that wraps all four.
```

Each package is its own standalone repo and can be used without mtk. mtk exists
because some things only matter at fleet level: "is anything up?", "what is
this costing me idle?", "stop everything".

---

## 1. Install (one-time)

```bash
git clone https://github.com/kylebrodeur/modal-toolkit.git  # this repo
cd modal-toolkit
uv sync --group dev

# The four package repos must live in one parent dir for shelling-out to work.
# If they are elsewhere, set $MODAL_TOOLKIT_REPOS.
export MODAL_TOOLKIT_REPOS=/path/to/your/repos-dir

mtk setup
```

`mtk setup` writes `~/.config/modal-toolkit/config.json` (mode 600). You can
edit it directly, or import an existing mci config with `mtk setup --from-env`.

## 2. Daily state checks

```bash
mtk doctor           # one line per package, ok/unhealthy/unreachable/repo
mtk doctor --json    # full output including served model list on the inference lane
mtk cost             # per-package always-on $/hr/day/month + blended total
```

`mtk doctor` is the answer to "is anything up?" — without it, you're grepping
`modal app list` per package. Run it before you assume anything about state.

## 3. The lifecycle

### Bring things up

```bash
mtk warm --pkg inference      # cold-start the GPU lane; the proxy's warm probe does the rest
mtk warm --all                # bring up everything with a GPU
mtk vision deploy             # full deploy (new profile/settings → re-deploy)
```

### Do per-package work

```bash
mtk embedding reindex         # shells into modal-embedding-server's app.py bulk job
mtk finetune train --profile profiles/gemma4/profile.json
mtk finetune eval --adapter <hf-user>/<name>
mtk finetune gguf --adapter <hf-user>/<name> --outtype q4_k_m
mtk finetune serve --adapter <hf-user>/<name>    # OpenAI-compatible endpoint
```

`mtk finetune ...` passes any extra args straight to the underlying `modal run/deploy` command, so profile-specific flags work without mtk knowing about them.

### Take things down

```bash
mtk shutdown --pkg inference  # scale-to-zero now (dashboard stays reachable for inference)
mtk shutdown --all            # the escape hatch; embeddings continue (CPU-only anyway)
```

`mtk shutdown` is idempotent — with nothing up it reports that and returns in a few seconds.

## 4. Cost-control rules that persist

- **Scale-to-zero is the default posture.** All four packages keep `scaledown_window` at 300s.
- **Before blaming the service for GPU spend, check what is calling it.**
  Anything pointed at a Modal URL is a wake signal, including a shared default
  model-role from a coding-agent provider.
- **Cost is per hour, never per token.** `mtk cost` reports the always-on burn
  separately from token demand; don't conflate them.
- **`mtk shutdown --all`** at the end of a working session is the cheapest
  insurance policy. Re-bringing-up is a `mtk warm --all` away.

## 5. Troubleshooting

| Symptom | Cause → fix |
| :--- | :--- |
| `mtk doctor` reports `unreachable` for a package | That package's container is cold. Run `mtk warm --pkg <name>` to bring it up. |
| `mtk doctor` reports `unhealthy` for a package | The URL is reachable but not 200 — check the package's own auth token and that the deployed alias matches the config. |
| `mtk flow` shows everything cold | Normal posture after `mtk shutdown --all`. Warm what you need. |
| `modal-coding-inference` legacy config | `mtk setup --from-env` imports it; keeps you on `~/.config/modal-toolkit/config.json` going forward. |
| Repo not found at `<workspace>/<pkg>` | The four package repos are siblings of this checkout. Set `$MODAL_TOOLKIT_REPOS` or edit `repos.root` in the config. |

## 6. Why not a per-package CLI for every package?

The single-CLI shape is a deliberate call:

- **The interesting ops are fleet-level** (doctor/shutdown/cost/warm). A per-package CLI cannot express "stop everything."
- **The per-package verbs are thin.** They shell out to the sibling repo's `modal run`/`deploy`; each repo's own entrypoints are the deep surface.
- **One config file is one place for the token.** Four per-package CLI configs would mean four secret locations.

The one package that *does* have its own richer CLI is `modal-inference-server`
(`modal-inference ...`), with 14 subcommands for catalog/tuning/doctors. Use it
directly when you need the deep surface; use `mtk` for the fleet view.