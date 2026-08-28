"""`docs/why.md` must agree with the arithmetic it claims to be doing.

That page has had its numbers wrong three times, and each time the same way: it
states a basis, quotes figures derived from that basis, and nothing recomputed
the derivation when the basis moved. A reader caught each one.

- a multiplier repeated for two different fees, overstating one by 2.5x
- a crossover quoted against a host the page does not recommend
- a claim about the ALGO price that was simply false

This does not check the page is persuasive or that the basis is the right one.
It checks that the figures follow from the basis, which is the only part a test
can own, and it is the part that broke every time.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from scripts.why_figures import HOSTS, compute

WHY = pathlib.Path(__file__).resolve().parent.parent / "docs" / "why.md"


@pytest.fixture(scope="module")
def page() -> str:
    return WHY.read_text()


@pytest.fixture(scope="module")
def figures():
    return compute()


def test_the_page_states_the_basis_it_uses(page: str, figures) -> None:
    # Without this the rest is unfalsifiable: figures with no stated basis
    # cannot be checked against one.
    assert f"{figures.seconds_per_round} s/round" in page
    assert f"{figures.executions_per_month:.0f} times a month" in page


def test_the_monthly_costs_match(page: str, figures) -> None:
    assert f"~{figures.floor_algo:.2f} ALGO" in page
    assert f"~{figures.suggested_algo:.2f} ALGO" in page


def test_the_headline_multiples_match(page: str, figures) -> None:
    # The error that got through twice: one multiple quoted for two fees.
    floor = figures.multiple("fly.io")
    suggested = figures.multiple_suggested("fly.io")
    assert f"{floor:.1f}x" in page
    assert f"{suggested:.1f}x" in page
    assert abs(floor - suggested) > 1, (
        "the floor and suggested multiples have converged, so a page quoting "
        "one for both would no longer be visibly wrong"
    )


def test_the_saving_that_the_crossover_divides_by_matches(page: str, figures) -> None:
    # The crossover divides by what self-hosting saves, not by what Arcron
    # costs. Getting this wrong is what produced a crossover of 26 on a page
    # whose own table said 10.
    assert f"${figures.saving_usd:.3f}" in page


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_every_crossover_matches(page: str, figures, host: str) -> None:
    assert f"| {figures.crossover(host):.0f} |" in page or f"**{figures.crossover(host):.0f} upkeeps**" in page


def test_the_parity_prices_match(page: str, figures) -> None:
    for host in ("fly.io", "Hetzner"):
        price = figures.parity_algo_price(host)
        assert f"${price:.2f}" in page, f"parity price against {host} is not on the page"


def test_the_stale_block_time_is_not_presented_as_the_basis(page: str) -> None:
    """2.66 came from a 45 second sample, which is about 17 rounds.

    It is not a rounding difference from the measured figure, it is a number
    with no support, and it was labelled "measured" on a page whose whole
    argument rests on it.

    The page may still *mention* it, and should: recording that a figure was
    wrong is how a reader knows the correction happened rather than wondering
    whether the numbers were always these. What it must not do is state it as
    the basis or call it measured, which is what these two patterns catch.
    """
    for stale in ("2.66 s/round", "measured 2.66"):
        assert stale not in page, (
            f"{stale!r} is on the page as a live figure. It came from a 45 "
            "second sample and the measured value is 2.752 on MainNet."
        )


def test_the_page_says_which_network_the_block_time_is_from(page: str) -> None:
    # TestNet and MainNet differ by about 2%, so an unqualified block time is
    # ambiguous in a way that changes every figure below it.
    assert "MainNet" in page and "TestNet" in page
