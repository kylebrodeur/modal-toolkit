"""Skill discovery for `mtk`: each server repo declares its own skills.

A server repo ships `server/mtk-skills.toml`. Each `[[skill]]` entry names a skill
(`mtk-<pkg>-<stage>`), its path in the repo, its lifecycle stage, when to call it,
and the project state it applies to (configured, deployed, or any). `suggest`
returns only the skills whose package is pulled and whose state condition holds,
so an agent never loads a skill for a package the project does not use.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGES: tuple[str, ...] = ("init-config", "setup-hooks", "build-deploy", "develop")
APPLIES: tuple[str, ...] = ("configured", "deployed", "any")
MANIFEST_REL = Path("server") / "mtk-skills.toml"
REQUIRED = ("name", "path", "stage", "applies", "when", "summary")


@dataclass(frozen=True)
class Skill:
    name: str
    pkg: str
    path: str
    stage: str
    when: str
    applies: str
    summary: str


def _pkg_from_name(name: str, stage: str) -> str | None:
    prefix, suffix = "mtk-", f"-{stage}"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return None
    pkg = name[len(prefix) : -len(suffix)]
    return pkg or None


def load_manifest_with_problems(repo: Path, pkg: str | None = None) -> tuple[list[Skill], list[str]]:
    """Parse a repo's skill manifest. Returns (valid skills, human-readable problems).

    A bad entry is reported and skipped. It never stops the other entries from loading.
    """
    path = repo / MANIFEST_REL
    if not path.is_file():
        return [], []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        return [], [f"{path}: malformed TOML: {exc}"]

    skills: list[Skill] = []
    problems: list[str] = []
    for index, entry in enumerate(data.get("skill", []), start=1):
        where = f"{path} entry {index}"
        missing = [k for k in REQUIRED if not str(entry.get(k, "")).strip()]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}")
            continue
        stage = entry["stage"]
        if stage not in STAGES:
            problems.append(f"{where}: unknown stage {stage!r} (allowed: {', '.join(STAGES)})")
            continue
        applies = entry["applies"]
        if applies not in APPLIES:
            problems.append(f"{where}: unknown applies {applies!r} (allowed: {', '.join(APPLIES)})")
            continue
        name = entry["name"]
        derived = _pkg_from_name(name, stage)
        if derived is None:
            problems.append(f"{where}: name {name!r} must be mtk-<pkg>-{stage}")
            continue
        if pkg is not None and derived != pkg:
            problems.append(f"{where}: name {name!r} is for package {derived!r}, not {pkg!r}")
            continue
        skills.append(
            Skill(
                name=name,
                pkg=derived,
                path=entry["path"],
                stage=stage,
                when=entry["when"],
                applies=applies,
                summary=entry["summary"],
            )
        )
    return skills, problems


def load_manifest(repo: Path, pkg: str | None = None) -> list[Skill]:
    """The valid skills a repo declares. Problems are dropped here; use the
    `_with_problems` variant to surface them."""
    skills, _ = load_manifest_with_problems(repo, pkg)
    return skills


def suggest(
    repos: Iterable[Path],
    pkg_state: Mapping[str, Mapping[str, Any]],
    pkgs_pulled: set[str],
) -> list[Skill]:
    """Skills to offer right now: package pulled, and the skill's condition holds.

    `pkg_state[pkg]` carries `configured` and `deployed` booleans. A missing entry
    counts as neither, so only `any` skills can apply to an unknown package.
    """
    out: list[Skill] = []
    for repo in repos:
        for skill in load_manifest(repo):
            if skill.pkg not in pkgs_pulled:
                continue
            state = pkg_state.get(skill.pkg, {})
            if skill.applies == "configured" and not state.get("configured", False):
                continue
            if skill.applies == "deployed" and not state.get("deployed", False):
                continue
            out.append(skill)
    return out
