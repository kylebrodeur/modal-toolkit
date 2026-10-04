# AGENTS.md — modal-toolkit

Agent instructions for THIS repo (v1.3.0 on `main`). The family-wide
rules live in the system workspace's AGENTS.md
(`modal-toolkit-system/AGENTS.md`); this file is the local copy of the
rules that matter when you are standing inside this repo.

## What this repo is

the operator CLI (mtk) for the fleet: doctor/warm/shutdown/cost/flow + dashboard/secrets/metrics/libs. Part of the modal-* family
(https://github.com/kylebrodeur/modal-toolkit). Apache-2.0.

## NON-NEGOTIABLE: deploys never come from this checkout

**Agents MUST NOT run `modal deploy` / `modal run` from this repo.**
This repo is the SOURCE for operator overlays, not a deploy target.
Deploying from here uses this repo's fixed app name + shared
Volume/Secret names, which collide with live lanes and can WIPE their
state (this exact failure happened to the writing-duo vault lane on
2026-10-09).

- Operator/lane deploys run from an overlay dir
  (`deploys/<pkg>/`: `deploy.json` composes `<slug>-<pkg>` app names +
  secret NAMES; `deploy.sh` is the entry point).
- Before ANY account-touching command, run the preflight guard:

  ```bash
  # from the system workspace:
  tools/guards/deploy-provenance.sh <your-overlay-dir>
  ```

- Standalone PUBLIC users cloning this repo directly are the
  exception the design protects: they get honest docs in the README
  and accept the single-tenant default names. AGENTS (which work in
  Kyle's workspace with live lanes) do not get that exemption.
- NEVER reset/reinit/pull-force a checkout to "fix" version confusion;
  the upgrade unit is an upstream TAG:
  `git fetch --tags && git checkout <tag>` (or `mtk pull`).

## Version + upgrade discipline

- The tag on `main` is the release of record; local checkouts track
  tags, not heads. After a family release wave, upgrade by TAG and
  re-run the repo suite (below).
- History: public repos carry the one squashed release commit
  (`feat: initial public release`); iteration commits land normally
  between releases. Do NOT amend/force-push a release without the
  system workspace's release checklist
  (`modal-toolkit-system/tools/release/release-repo.sh`).

## Verification (run before claiming anything works)

```bash
uv run pytest tests -q
```

- Lint/format: `uv run ruff check .` + `uv run ruff format --check .`
- Vendored-copy parity where the repo vendors shared modules:
  `mtk libs check` (from the modal-toolkit sibling; CI runs it).
- Never claim a deploy/health result without running the thing.

## The lifecycle hook seam

- `toolkit/libs/hooks.py` is the VENDORED copy (mtk libs sync); its tags are the CLI-internal set if any — check the module docstring.
## Prose rules (this is a public repo)

No personal identifiers, no em/en dashes in prose, no AI-slop words
(robust / leverage / seamlessly / utilize), "private-first" not
"local-first". README claims must be runnable back (numbers honest,
sourced from runbooks/eval results).
