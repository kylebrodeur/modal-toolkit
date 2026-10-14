# AGENTS.md (suggested)

This file is a suggestion for projects that use the **Modal Toolkit** (`mtk`).
Copy it to your project as `AGENTS.md`, or merge the parts you want into your
existing one. Nothing here overwrites a file automatically.

## What this is

`mtk` is the operator CLI for the Modal Toolkit family:
<https://github.com/kylebrodeur/modal-toolkit>. It sets up a project, pulls the
package repos, gates secrets, and deploys each package from the project's own
overlay.

## Working in a project that uses `mtk`

- Create or reuse a project with `mtk init`, then `mtk pull <pkg> --tag <release>`
  and `mtk deploy <pkg>`.
- Deploy only through the project's `deploys/<pkg>-server/` overlay. Never run
  `modal deploy` inside a package checkout.
- Check state with `mtk doctor`. Read `mtk status --no-net` for a config-only view.
- Secrets are confirmed before anything is created. Never write secret values
  into a repo or a chat.
- Read the quickstart (`docs/quickstart.md`) before changing deploy or secret behavior.

## Conventions

- Keep changes small and reviewable. Run `uv run pytest` and `uv run ruff check .`
  before pushing.
- Public prose: no em or en dashes, no personal identifiers.
