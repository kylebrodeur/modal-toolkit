"""Agent files for a project: AGENTS.md (with a managed block) and llms.txt.

Contract:
- Never overwrite a user's AGENTS.md. If none exists, create it from the
  suggestion. If one exists, append or refresh only the marked mtk block.
- Re-running is idempotent: the file is byte-identical after a second run.
- llms.txt follows the llmstxt.org layout: H1 title, blockquote summary,
  then link sections.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import project

BEGIN = "<!-- mtk:begin -->"
END = "<!-- mtk:end -->"


class TestAgentsFreshProject:
    def test_creates_agents_md_with_managed_block(self, tmp_path):
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        text = (tmp_path / "AGENTS.md").read_text()
        assert BEGIN in text and END in text
        assert "mtk deploy vault" in text

    def test_creates_llms_txt(self, tmp_path):
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        text = (tmp_path / "llms.txt").read_text()
        assert text.startswith("# writing-lab")
        assert "> " in text.splitlines()[2]
        assert "vault" in text


class TestAgentsExistingFile:
    def test_appends_block_and_keeps_user_text(self, tmp_path):
        user = "# My project\n\nHouse rules: tests before commits.\n"
        (tmp_path / "AGENTS.md").write_text(user)
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        text = (tmp_path / "AGENTS.md").read_text()
        assert text.startswith(user)
        assert BEGIN in text

    def test_refreshes_only_the_managed_block(self, tmp_path):
        existing = f"# Mine\n\nKeep this line.\n\n{BEGIN}\nold managed text\n{END}\n\nAnd this one.\n"
        (tmp_path / "AGENTS.md").write_text(existing)
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        text = (tmp_path / "AGENTS.md").read_text()
        assert "old managed text" not in text
        assert "Keep this line." in text
        assert "And this one." in text
        assert text.count(BEGIN) == 1


class TestIdempotence:
    def test_second_run_is_byte_identical(self, tmp_path):
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        first = {n: (tmp_path / n).read_bytes() for n in ("AGENTS.md", "llms.txt")}
        project.install_agent_files(tmp_path, slug="writing-lab", packages=["vault"])
        second = {n: (tmp_path / n).read_bytes() for n in ("AGENTS.md", "llms.txt")}
        assert first == second


def test_regenerating_with_a_later_pull_keeps_earlier_packages(tmp_path):
    """cmd_pull passes the FULL record package list: a second pull appends, never erases."""
    import json

    (tmp_path / ".mtk").mkdir()
    (tmp_path / ".mtk" / "config.json").write_text(json.dumps({"slug": "lab", "packages": {}}) + "\n")
    project.install_agent_files(tmp_path, "lab", ["vault"])
    # simulate a second package pulled later, record now holds both
    record = {"slug": "lab", "packages": {"vault": {"tag": "v1.0.0"}, "inference": {"tag": "v1.0.0"}}}
    (tmp_path / ".mtk" / "config.json").write_text(json.dumps(record) + "\n")
    project.install_agent_files(tmp_path, record["slug"], list(record["packages"]))
    text = (tmp_path / "AGENTS.md").read_text()
    assert "mtk deploy vault" in text and "mtk deploy inference" in text
