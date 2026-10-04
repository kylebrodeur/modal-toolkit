# Family secrets management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `mtk secrets` flow + committed `server/secrets.toml` manifests across the five deployable family repos, so creating/verifying Modal secrets for any package is one command instead of five bespoke README snippets.

**Architecture:** Declarative manifests (key names + generator kinds only) shipped inert in each repo; the toolkit parses them and talks to Modal's workspace-scoped `modal secret` CLI (one `list --json` call is fleet-authoritative). `mtk doctor` reuses the check. READMEs standardize on `mtk secrets check` + `mtk secrets create [--pkg X]` with the raw `modal secret create` line as the purist fallback.

**Tech Stack:** stdlib `tomllib` (manifests), `secrets`/`getpass` (generation + prompts), argparse verb in `toolkit/cli.py`, subprocess fake-binary shim in tests (family-proven pattern), pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-family-secrets-management-design.md`

## Global Constraints

- Manifest values are NEVER written to disk, logs, stdout, or git: generated values exist only in memory and in the single `modal secret create ... KEY=value` argv Modal's CLI requires. Prompts use `getpass`, not `input`.
- `check` is read-only (one `modal secret list --json` + manifest diff) and reports `drift` when a repo's committed `Secret.from_name` default disagrees with its manifest.
- Manifests must stay inert data: repo runtime code never reads them.
- Toolkit style: no em dashes in code comments/prose; Conventional Commits; ruff clean; pytest `-W error` pristine.
- Package set for secrets: `embedding, inference, vision, finetune, vault`. `_pkg_repo` gains `vault -> modal-vault-server`.
- Prose style rules from the family skill apply to all README edits (no `robust/utilize/seamlessly/leverage`, no personal identifiers, no em dashes).
- All tests deterministic + offline (fake modal binary shim; no live Modal calls in CI).

## Shared Interfaces (binding)

```python
# toolkit/secrets.py (Task 2)
@dataclass(frozen=True) class ManifestSecret:
    secret_id: str          # manifest table id, e.g. "auth"
    name: str               # Modal secret name
    keys: dict[str, KeySpec]
@dataclass(frozen=True) class KeySpec:
    env_var: str
    generate: str | None    # "hex32" when the tool generates
    ask: str | None         # prompt hint when the user supplies

def load_manifest(repo_path: Path) -> ManifestSpec       # ManifestSpec(repo=str, secrets=[ManifestSecret, ...])
    # raises ManifestError(file, message) on malformed TOML / missing name / dup ids / invalid generator kind
def check(manifest: ManifestSpec, existing_names: set[str], repo_path: Path) -> PackageStatus
    # PackageStatus(pkg, secrets=[SecretStatus(secret_id, name, exists: bool, drift: str | None)], manifest_found: bool)
    # drift text: "code declares <CONST_NAME> default '<other-name>' but manifest says <name>" (grep-based)
def create(args: argparse.Namespace) -> int              # verb body; per spec section 2 (getpass prompts, token_hex(32), --force semantics, post-create re-check table)
```

```python
# toolkit/cli.py wiring (Task 3)
# sub.add_parser("secrets", ...) with subcommand choices ["check", "create", "rotate"]
#   rotate NAME = create --force --secret NAME + confirmation prompt
# cmd_doctor gains per-package secrets line via secrets.check_all()
def check_all(pkg_filter: list[str] | None = None) -> dict   # {pkg: PackageStatus}; one modal list call
```

**Modal invocation seam (the only subprocess in secrets.py):**
```python
def _modal_secret_names(modal_argv: list[str]) -> set[str]
    # runs modal_argv + ["secret", "list", "--json"], parses JSON, returns {name, ...}
    # modal_argv: tests inject [fake_shim_path]; production: ["uvx", "modal"] (resolved per invocation cwd=repo? NO: workspace-scoped, run from toolkit cwd; --env passthrough NOT in v1)
def _modal_secret_create(modal_argv: list[str], name: str, kv_pairs: dict[str, str]) -> None
    # runs modal_argv + ["secret", "create", name, "K1=v1", ...]; caller must have verified missing (or --force)
```

---

### Task 1: Five `server/secrets.toml` manifests (batched, inert)

**Files:** Create in each repo:
- `/Users/kylebrodeur/workspace/modal-embedding-server/server/secrets.toml` — auth: `embedding-auth` {API_TOKEN=hex32}; hf: `huggingface-secret` {HF_TOKEN=ask "HF token with read + the gated model licenses accepted"}
- `/Users/kylebrodeur/workspace/modal-inference-server/server/secrets.toml` — auth: `inference-auth-secret` {API_TOKEN=hex32}; dashboard: `modal-inference-server-dashboard` {MODAL_INFERENCE_DASHBOARD_TOKEN=hex32}; hf: `modal-inference-server-huggingface` {HF_TOKEN=ask}
- `/Users/kylebrodeur/workspace/modal-vision-server/server/secrets.toml` — auth: `modal-vision-secret` {API_TOKEN=hex32}
- `/Users/kylebrodeur/workspace/modal-finetune-server/server/secrets.toml` — main: `modal-finetune-secrets` {HF_TOKEN=ask}; (gguf's separate `HF_TOKEN` secret name stays declared: hf: `HF_TOKEN` {HF_TOKEN=ask "gguf pipeline fallback, mirrors main"})
- `/Users/kylebrodeur/workspace/modal-vault-server/server/secrets.toml` — auth: `modal-vault-secret` {VAULT_API_TOKEN=hex32}

**Interfaces:** Produces: manifests matching the exact shape in the Spec §1. Values/keys verified against each repo's committed constants in Task 5's cross-check.

- [ ] **Step 1: Write all five files** (no code, no tests: inert data). Cross-check names by grepping each repo for `Secret.from_name` + `SECRET_NAME =` defaults BEFORE writing; manifest `name` must equal the const's DEFAULT value, not any override.
- [ ] **Step 2: Validate TOML parses**: `python3 -c "import tomllib; [tomllib.load(open(p,'rb')) for p in [...]]"` all five.
- [ ] **Step 3: Report** (files + any constant/manifest mismatch found during cross-check → STOP and report instead of silently picking a name).

### Task 2: `toolkit/secrets.py` (manifest parse + check + create, all logic)

**Files:** Create `toolkit/secrets.py`, `tests/test_secrets.py`.

**Interfaces:** Consumes: manifests (Task 1). Produces: everything in Shared Interfaces above.

- [ ] **Step 1: Failing tests**: manifest happy-path + malformed variants (bad TOML, missing name, dup id, unknown generator kind) each asserting ManifestError with file context; check() happy/missing/wrong-name; create() record-argv flow via fake modal shim: --force refusal on existing secret (exit non-zero, no argv), generated keys look like 64-hex and appear exactly once in argv, asked keys arrive via getpass monkeypatch, stdout/stderr contain NO token bytes (assert generated hex value not printed); doctor-style check_all() aggregation with one shim call per invocation regardless of package count; vault pkg mapping.
- [ ] **Step 2: Run → FAIL. Step 3: Implement per Shared Interfaces. Step 4: pytest tests/test_secrets.py -W error green; ruff clean. Report.**

### Task 3: Toolkit CLI wiring + doctor hook + toolkit README

**Files:** Modify `toolkit/cli.py` (add `secrets` parser verb + wire cmd), `toolkit/cli.py cmd_doctor` (secrets line), `README.md` (tool table row + short section), `docs/RUNBOOK.md` (one row for the secrets table if the table shape allows).

**Interfaces:** Consumes: `toolkit/secrets.py` (Task 2).

- [ ] **Step 1: Failing tests** (tests/test_secrets.py or new test_cli_secrets.py): `mtk secrets check` prints per-package table + exit codes; `rotate NAME` confirmation gate (decline path aborts before any Modal call); doctor output contains secrets line.
- [ ] **Step 2: Run → FAIL. Step 3: Implement. Step 4: full `uv run pytest tests -q -W error` green + ruff. Report.**

### Task 4: Five README secret-section swaps (batched by repo)

**Files:** Modify each repo's README.md — replace the bespoke `modal secret create` snippet with: (1) `mtk secrets check` then `mtk secrets create --pkg <pkg>` one-liners, (2) the raw purist `modal secret create <name> <KEY>=$(openssl rand -hex 32)` line matching the manifest, (3) a sentence naming `server/secrets.toml` as the source of truth. NO other README surgery (family section/byline untouched).

**Interfaces:** Consumes: manifests (Task 1 names must match what the README now says).

- [ ] **Step 1: Edit READMEs in the five repos** (`finetune` README mentions two secrets — follow its manifest).
- [ ] **Step 2: Leak grep each edited README** (family scrub list from the skill: brodeur.me, /Users/kyle, hemmingway, pi-vault-mind, modal-coding-inference, PVM_, em dashes) → empty.
- [ ] **Step 3: Report.**

### Task 5: Schema cross-check test (toolkit)

**Files:** Modify `tests/test_secrets.py` — add `test_manifest_names_match_code_constants`: for each of the 5 sibling checkouts IF present on disk (sibling discovery from `_pkg_repo` mapping, `clean-skip` otherwise), parse manifest + grep the repo for `SECRET_NAME` const defaults; assert manifest `name` == const default, and assert every `from_name` call site in the repo names a manifest secret (no undeclared secret consumption).

- [ ] **Step 1: Test first (red against any mismatch; expect green after T1).**
- [ ] **Step 2: Full toolkit suite green + ruff. Report.**

### Task 6: REAL `mtk secrets check` on Kyle's workspace (controller runs, read-only)

- [ ] **Step 1: Run `mtk secrets check`** for real from the toolkit repo. Record per-package result.
- [ ] **Step 2: Report the table to the user with the create-gate question** (which missing secrets to create; live-deploy override names mentioned as the migration note).

### Task 7: Create missing secrets (GATE: explicit user OK on the exact name list)

- [ ] **Step 1: `mtk secrets create` for user-approved subset.** getpass prompts surfaced to the user for "ask" keys when he's at the terminal, OR the tool prints the exact manual `modal secret create` commands for him to run (values stay with the user).
- [ ] **Step 2: re-run check → all ok. Report.**

### Task 8: Push + CI verify

- [ ] **Step 1: Commit per repo** (toolkit: feat commits; manifest/README repos: docs/chore commits) + push all 6.
- [ ] **Step 2: Watch CI** on toolkit + the 5 repos (manifests/README don't run code; CI must stay green). Report final table.