"""Every project subcommand must route to its handler.

A missing set_defaults(func=...) parses cleanly and then fails at run time with
an AttributeError. This test parses each command and checks it reaches the
right handler, so that failure shows up in CI rather than in a user's shell.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import cli


@pytest.mark.parametrize(
    ("argv", "handler"),
    [
        (["init"], cli.cmd_init),
        (["init", "lab-one", "--agents"], cli.cmd_init),
        (["init", "--no-agents"], cli.cmd_init),
        (["pull", "vault"], cli.cmd_pull),
        (["deploy", "vault"], cli.cmd_deploy),
    ],
)
def test_project_subcommand_routes_to_its_handler(argv, handler):
    args = cli.build_parser().parse_args(argv)
    assert args.func is handler


def test_agents_flags_map_to_explicit_values():
    assert cli.build_parser().parse_args(["init", "--agents"]).agents is True
    assert cli.build_parser().parse_args(["init", "--no-agents"]).agents is False
    assert cli.build_parser().parse_args(["init"]).agents is None
