"""Every skill under skills/ must satisfy the skill spec.

Checks: a SKILL.md exists in each skill directory; frontmatter opens and closes
with ---; name is lowercase letters, digits, and hyphens (max 64) and equals the
directory name; description is present, single-line, and at most 1024 characters;
the body after the frontmatter is non-empty and has no second frontmatter block.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "skills"
REPOS = Path(__file__).resolve().parent.parent.parent  # the system folder (sibling repos)
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _find_skill_dirs(base: Path) -> list[Path]:
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def _skill_dirs() -> list[Path]:
    # the toolkit's own skills plus every sibling server repo's skills/
    dirs = _find_skill_dirs(ROOT)
    for repo in sorted(REPOS.iterdir()):
        skills = repo / "skills"
        if skills.is_dir():
            dirs.extend(_find_skill_dirs(skills))
    return sorted(set(dirs))


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise AssertionError("SKILL.md must open with a --- frontmatter line")
    end = text.index("\n---\n", 4)
    block = text[4:end]
    body = text[end + len("\n---\n") :]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise AssertionError(f"frontmatter line without a key: {line!r}")
        fields[key.strip()] = value.strip().strip('"')
    return fields, body


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_matches_the_spec(skill_dir: Path):
    path = skill_dir / "SKILL.md"
    assert path.is_file(), f"{skill_dir.name}: missing SKILL.md"
    fields, body = _frontmatter(path.read_text())

    name = fields.get("name", "")
    assert NAME_RE.match(name) and len(name) <= 64, f"{skill_dir.name}: invalid name {name!r}"
    assert name == skill_dir.name, f"{skill_dir.name}: name must match the directory, got {name!r}"

    description = fields.get("description", "")
    assert description, f"{skill_dir.name}: description is required"
    assert len(description) <= 1024, f"{skill_dir.name}: description longer than 1024 characters"

    assert body.strip(), f"{skill_dir.name}: body is empty"
    assert "\n---\n" not in body, f"{skill_dir.name}: a second frontmatter block appears in the body"


def test_there_is_at_least_one_skill():
    assert _skill_dirs(), "skills/ must contain at least one skill"


@pytest.mark.parametrize(
    ("skill_rel", "required_form", "banned_api"),
    [
        ("../../modal-vault-server/skills/mtk-vault-setup-hooks/SKILL.md", "hooks.on(", "hooks.observe"),
        ("../../modal-inference-server/skills/mtk-inference-setup-hooks/SKILL.md", "hooks.register(", "hooks.observe"),
    ],
)
def test_hook_samples_use_the_real_seam_api(skill_rel: str, required_form: str, banned_api: str):
    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parent / skill_rel
    if not path.is_file():
        pytest.skip(f"{skill_rel}: sibling server checkout not present (standalone clone)")
    text = path.read_text()
    assert required_form in text, f"{skill_rel}: sample must use {required_form!r}"
    assert banned_api not in text, f"{skill_rel}: sample uses a non-existent API {banned_api!r}"
