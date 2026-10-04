# Family secrets management — design (`mtk secrets`)

Date: 2026-10-06
Status: approved in chat
Scope: modal-toolkit (new `secrets` verb + doctor hook) + `server/secrets.toml` manifests in the five deployable repos (embedding, inference, vision, finetune, vault). EE has no secrets; toolkit's own manifest declares none (it consumes env/config, not Modal Secrets).

## Problem

Six sibling repos each hand-write their own `modal secret create` snippet with different naming conventions, four declared default secret names do not exist on Kyle's account (live apps drift onto overrides), and nothing verifies consistency. Public users copy bespoke per-repo snippets with no shared pattern.

## Design

### 1. Per-repo manifest: `server/secrets.toml` (committed; key NAMES only, never values)

```toml
# Declarative list of the Modal Secrets this app consumes.
# generate: the tool creates the value (never printed). ask: prompted from the user.
# Values are written to Modal only; nothing touches disk, shells, or logs.

[secret.auth]
name = "modal-vault-secret"
[secret.auth.keys.VAULT_API_TOKEN]
generate = "hex32"
```

- Exactly one `[secret.<id>]` table per Modal Secret the app consumes; `name` matches the repo's committed `Secret.from_name` constant default.
- Keys: `<ENV_VAR_NAME> = { generate = "hex32" }` or `{ ask = "human-friendly hint" }`.
- `mtk secrets` (not the repo) is the only thing that parses these; repo code ignores the file at runtime.

### 2. Toolkit verb: `mtk secrets check | create [--pkg ...] [--all] [--json] [--force]`

- Package set: `embedding, inference, vision, finetune, vault` (vault joins `_pkg_repo` special-case: repo `modal-vault-server`).
- `check`: one `modal secret list --json` (workspace-scoped — secrets are NOT per-repo), diff against all manifests: per package report `ok | missing <SECRET> | manifest absent | drift (repo constant != manifest name)`; exit 1 when anything missing (0 with --json so scripts can consume).
- `create`: for each missing secret, generate values (`secrets.token_hex(32)`) or prompt (`getpass`, print the manifest hint), then one `modal secret create <name> KEY=value ...` per secret. Values never echoed, never logged, never in argv beyond the single CLI call Modal itself requires. Existing secrets are skipped unless `--force` (which regenerates ALL of that secret's keys — flagged clearly as it invalidates live deployments). After create, re-run check and print the table.
- `rotate <secret-name>`: `--force` alias with a confirmation, for the auth-token case.

### 3. Doctor integration

`mtk doctor` gains a `secrets` line per package (`secrets: ok | missing modal-vision-secret`) so "is this fleet deployable?" is one verb. Implemented by the same check function (cheap: one JSON list call for the whole fleet, cached per invocation).

### 4. READMEs (five deployable repos)

Replace each bespoke `modal secret create ...` snippet with the standard flow, two lines: the toolkit path (`mtk secrets check` then `mtk secrets create --pkg <pkg>`) and the purist path (`modal secret create <name> KEY=...` — exact names/keys from the manifest). Add one sentence pointing at the manifest file as the source of truth.

### 5. Best practices baked in (per Modal docs)

- Secrets are workspace-scoped: one account list call is authoritative; nothing per-repo is queried.
- Existence-only verification remotely (Modal never exposes values via API read) — the tool never pretends to verify values.
- `Secret.from_dict`/`from_dotenv` remain the local-dev pattern; the manifest story is only for deploy-time secrets.
- Small, focused secrets per app (auth separated from HF token, as the family already does).
- Values generated with CSPRNG (`secrets.token_hex(32)`), matching the `openssl rand -hex 32` guidance already in the repos.

## Non-goals

- No 1Password integration in v1 (the vault-server worker does its own first-bootstrap provisioning; that seam is unchanged).
- No reading/echoing/rotating individual keys inside existing secrets (Modal can't read them back; --force is the whole story).
- No CI automation (secrets are operator actions; doctor exposes drift instead).
- TK and EE get no manifests (no Modal Secrets consumed).

## Testing

- Toolkit: pytest for manifest parsing (valid/invalid/missing), check logic against a fake `modal` binary shim (JSON list output), create flow with recorded argv (values asserted present-in-argv at CALL time, absence in stdout/logs), --force refusal, vault pkg mapping. Fake shim pattern already proven in the family.
- Repos: no runtime change (manifests are inert data); CI untouched except nothing.
- The five `secrets.toml` files get a schema cross-check test in the toolkit: names must match the constants each repo's committed code declares (grep of the repo is a pytest fixture against the real sibling checkouts — runs only when siblings exist locally, clean-skip otherwise).

## Rollout order

1. Manifests in five repos (inert, no code).
2. `mtk secrets` verb + doctor hook in toolkit (+ tests).
3. README swaps in five repos.
4. Run `mtk secrets check` for real on Kyle's workspace, then `mtk secrets create` for the four missing (his explicit go at that point; creates REAL secrets).
5. Push all repos; CI green; family consistency restored.