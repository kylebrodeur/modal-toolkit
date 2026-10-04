# Runbook: operating the Modal Toolkit

One CLI, six packages (embedding, inference, vision, finetune, coding,
vault), one GPU budget. Everything here is the current-day
operator surface; nothing is aspirational. For a package's internal mechanics
(hot sets, slot gating, GGUF conversion), that package's own docs are
authoritative: this runbook is the cross-cutting view.

---

## 0. Mental model

```
  Your data ──┬─> [embedding]  ──┐
              ├─> [inference]    ├──> private search / answers
              ├─> [vision]       ┘
              └─> [finetune] → adapters/GGUF → [inference]

  mtk is the operator CLI that wraps all of them (the coding and vault
  lanes are state/secrets-first: coding rides the inference surface,
  vault is the ob passthrough below).
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

# The package repos must live in one parent dir for shelling-out to work.
# If they are elsewhere, set $MODAL_TOOLKIT_REPOS.
export MODAL_TOOLKIT_REPOS=/path/to/your/repos-dir

mtk setup
```

`mtk setup` writes `~/.config/modal-toolkit/config.json` (mode 600). You can
edit it directly, or import an existing local config with `mtk setup --from-env`.

## 2. Daily state checks

```bash
mtk status           # one table: configured/enabled/secrets/reachable per package
mtk status --no-net  # same glance without touching the network
mtk doctor           # one line per package, ok/unhealthy/unreachable/cold/repo
mtk doctor --json    # full output including served model list on the inference lane
mtk cost             # per-package always-on $/hr/day/month + blended total
```

`mtk doctor` is the answer to "is anything up?": without it, you're grepping
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
# serving: register the artifact with modal-inference-server (mtk finetune builds artifacts only)
```

`mtk finetune ...` passes any extra args straight to the underlying `modal run/deploy` command, so profile-specific flags work without mtk knowing about them.

### Take things down

```bash
mtk shutdown --pkg inference  # scale-to-zero now (dashboard stays reachable for inference)
mtk shutdown --all            # the escape hatch; embeddings continue (CPU-only anyway)
```

`mtk shutdown` is idempotent: with nothing up it reports that and returns in a few seconds.

## 4. Cost-control rules that persist

- **Scale-to-zero is the default posture.** The GPU packages keep `scaledown_window` at 300s.
- **Before blaming the service for GPU spend, check what is calling it.**
  Anything pointed at a Modal URL is a wake signal, including a shared default
  model-role from a coding-agent provider.
- **Cost is per hour, never per token.** `mtk cost` reports the always-on burn
  separately from token demand; don't conflate them.
- **`mtk shutdown --all`** at the end of a working session is the cheapest
  insurance policy. Re-bringing-up is a `mtk warm --all` away.

## 5. App metrics (VictoriaMetrics drop-in)

`toolkit.metrics` is a stdlib-only writer any package (or your own Modal app)
can use to push app-level events and rates into **your own** VictoriaMetrics
(single-node; the line protocol also lands on InfluxDB). Point it at your VM
with `MODAL_TOOLKIT_VM_URL` (default `http://localhost:8428`). A write is one
POST to `/api/v2/write?precision=s`; 204 means landed. The writer never raises
and never retries: a metrics failure must never fail the host operation; the
caller decides the retry policy.

```python
from toolkit.metrics import write_metric, MetricTimer, bump

bump("embedding_job_done")  # stateless counter point
with MetricTimer("inference_request_seconds"):  # writes elapsed seconds on exit
    ...
write_metric("vision_gate_skip", 1, tags={"gate": "fast"})
```

Name points `<package>_<event>`: `embedding_job_done`, `vision_gate_skip`,
`inference_request_seconds`. Verify plumbing with `mtk metrics url` (effective
URL + its source) and `mtk metrics test` (writes one `mtk_metrics_probe` point;
exit 1 with a clear message when no VM answers).

## 6. The vault lane

`mtk vault status` (or `sync`, `pull-only`, `sync-on-write`,
`mirror-remote`, `continuous`, `list-remote`, `list-local`, `config`,
`logs`, `exec ...`) shells into the running `modal-vault-server`
container and runs `ob` there. The clone and ob login state live in
that container under `/vault` (Volume-backed): nothing ever syncs
locally, the picker targets only `modal-vault-server` containers, and
each command requires the vault section's `base_url` in the toolkit config
(which wakes the scale-to-zero app).

## 7. Shared modules + extension seams

The family shares stdlib modules through [modal-shared-libs](https://github.com/kylebrodeur/modal-shared-libs),
vendored verbatim into each package (toolkit's own `libs/` included):

```bash
mtk libs check             # md5 parity of every vendored copy (what CI runs)
mtk libs sync              # refresh stale copies from the canonical repo
```

`check` failing = someone (or an older ruff/format sweep) touched a vendored
byte; `sync` restores and the repo's tree goes back to identical. The vendored
header names the canonical home + md5.

The same seam pattern powers each package's **lifecycle hooks** (per-package
closed tag sets in that README's "Lifecycle hooks" section): overlay lanes
`hooks.register(...)` / `@hooks.on(...)` to observe boot, request, job, and
verdict boundaries. Errors are contained + reported (`last_errors(tag)`); a
broken lane hook never takes down the host.

## 8. Troubleshooting

| Symptom | Cause → fix |
| :--- | :--- |
| `mtk status`/`mtk doctor` report `cold` for a package | The lane answered slowly or not within the warm budget: almost always a scale-to-zero boot, not an outage. Re-run (one warm/retry cycle usually lands) or `mtk warm --pkg <name>`. |
| `mtk status`/`mtk doctor` report `unreachable` for a package | The URL itself failed (connection/DNS error, not a timeout): check the config's `base_url`, the deployed app, and the network. This is distinct from `cold`. |
| `mtk doctor` reports `unhealthy` for a package | The URL is reachable but not 200: check the package's own auth token and that the deployed alias matches the config. |
| `mtk flow` shows everything cold | Normal posture after `mtk shutdown --all`. Warm what you need. |
| `~/.config/mci/config.json` (local config from another toolkit) | `mtk setup --from-env` imports it. The toolkit config stays at `~/.config/modal-toolkit/config.json`. |
| Repo not found at `<workspace>/<pkg>` | The package repos are siblings of this checkout. Set `$MODAL_TOOLKIT_REPOS` or edit `repos.root` in the config. |
| `mtk status`/`mtk doctor` report `cold` for a package | The lane answered slowly or not within the warm budget: almost always a scale-to-zero boot, not an outage. Re-run (one warm/retry cycle usually lands) or `mtk warm --pkg <name>`. |
| `mtk status`/`mtk doctor` report `unreachable` for a package | The URL itself failed (connection/DNS error, not a timeout): check the config's `base_url`, the deployed app, and the network. |
| Vision boots, then 500s on `/v1/identify` with "unknown encoder backend" or "unknown fast gate kind" | The model card carries an unsupported value. Fix the card JSON, or revert the single-line env override (`MODAL_VISION_*`) and let the card win. |
| Vision classifies wrong after a model swap | The prompt template and similarity/reference constants are model-specific: copy them from the model card you switched *from*, then re-calibrate. See `registry/README.md` in the vision repo. |

## 9. Why not a per-package CLI for every package?

The single-CLI shape is a deliberate call:

- **The interesting ops are fleet-level** (doctor/shutdown/cost/warm). A per-package CLI cannot express "stop everything."
- **The per-package commands are thin.** They shell out to the sibling repo's `modal run`/`deploy`; each repo's own entrypoints are the deep surface.
- **One config file is one place for the token.** Per-package CLI configs would mean per-package secret locations.

The one package that *does* have its own richer CLI is `modal-inference-server`
(`modal-inference ...`), with 14 subcommands for catalog/tuning/doctors. Use it
directly when you need the deep surface; use `mtk` for the fleet view.