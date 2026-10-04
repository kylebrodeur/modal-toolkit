"""The `mtk cost` rate table must match Modal's published list prices.

`mtk cost` reports always-on burn using a table of GPU hourly rates.
That table is a hardcoded snapshot of Modal's published per-second
prices converted to an hourly figure, so it can drift out of date the
way any hardcoded price can. These tests pin the table against the
per-second rates and the date it was read, so a future edit has to
update the source AND the date together rather than quietly going stale.

The rates themselves are at https://modal.com/pricing. Modal's list
prices change, and a regional multiplier can apply on top, so this is a
published-price snapshot for comparison, not a quote.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from toolkit.cli import GPU_HOURLY

# Modal's published per-second list prices, read 2026-10-22.
# See https://modal.com/pricing.
MODAL_PER_SECOND = {
    "H200": 0.001261,
    "B200": 0.001736,
    "H100": 0.001097,
    "L40S": 0.000542,
    "A10G": 0.000306,
    "L4": 0.000222,
    "T4": 0.000164,
}

# The date the table was read from modal.com/pricing. Bump this when the
# table is refreshed, so a stale table fails the test rather than
# quietly under-reporting the bill.
RATES_READ_ON = "2026-10-22"

SECONDS_PER_HOUR = 3600


def test_rates_cover_exactly_the_published_gpus() -> None:
    assert set(GPU_HOURLY) == set(MODAL_PER_SECOND)


@pytest.mark.parametrize("gpu", sorted(MODAL_PER_SECOND))
def test_hourly_rate_matches_the_published_per_second_price(gpu: str) -> None:
    """The hourly figure must be the published per-second rate times 3600."""
    expected = round(MODAL_PER_SECOND[gpu] * SECONDS_PER_HOUR, 2)
    assert GPU_HOURLY[gpu] == pytest.approx(expected, abs=0.01), (
        f"{gpu}: the table says ${GPU_HOURLY[gpu]}/hr but modal.com/pricing "
        f"publishes ${MODAL_PER_SECOND[gpu]}/sec (= ${expected}/hr)"
    )


def test_rates_are_not_stale() -> None:
    """A rate table is a snapshot; it must say which snapshot it is."""
    source = Path(__file__).resolve().parents[1] / "toolkit" / "cli.py"
    text = source.read_text(encoding="utf-8")
    assert RATES_READ_ON in text, "the rate table must carry the date it was read from modal.com/pricing"


def test_readme_rate_table_matches_the_code() -> None:
    """The README prints the same table; the two must not drift apart."""
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    block = re.search(r"```\n(H200:.*?)\n```", text, re.S)
    assert block is not None, "the README should print the rate table"
    printed = dict((gpu, float(price)) for gpu, price in re.findall(r"(\w+):\s+\$([\d.]+)/hr", block.group(1)))
    assert printed == GPU_HOURLY, f"the README's rate table and GPU_HOURLY disagree; README={printed} code={GPU_HOURLY}"
