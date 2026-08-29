"""An execution is a call to `execute`, not a call to the application.

This module exists because the query was hand-rolled once and got that wrong.
It grouped every application call by sender, and reported an account that had
only ever called `register` as a keeper executing upkeeps. The count was real.
The label was false, and it would have gone into a status report as evidence
that an outsider was running a keeper.

The rest is classification: which upkeeps are worth mentioning and which are
fine. Getting that wrong in the other direction matters too, because a health
report that flags everything is one nobody reads.
"""

from __future__ import annotations

import hashlib

import pytest

from scripts.keeper_bot import BONUS_FEE_MICROALGO, EXECUTION_COST_MICROALGO
from scripts.registry_health import LOW_RUNWAY_DAYS, UpkeepHealth, execute_selector


def health(**over) -> UpkeepHealth:
    base = dict(
        upkeep_id=1,
        target_app=769891902,
        times_executed=5,
        net_to_keeper=7_000,
        runway_days=30.0,
        rounds_late=0,
        interval_rounds=1_286,
    )
    return UpkeepHealth(**{**base, **over})


def test_the_execute_selector_is_derived_from_the_abi() -> None:
    # Pasted bytes match nothing silently if the signature ever changes. This
    # asserts the derivation, and pins the value so a change is visible.
    expected = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
    assert execute_selector() == expected
    assert execute_selector().hex() == "5b49cc5c"


def test_the_execute_selector_is_not_the_register_one() -> None:
    """The whole bug, in one assertion.

    `register` is what the account misreported as a keeper had actually called.
    """
    register = hashlib.new(
        "sha512_256",
        b"register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64",
    ).digest()[:4]
    assert execute_selector() != register


class TestPaysNothing:
    def test_an_upkeep_at_the_floor_with_a_bonus_pays_nothing(self) -> None:
        # 4,000 fee, 3,000 to execute, 1,000 more because the bonus transfer is
        # a third inner transaction. Exactly zero, which is what upkeep 73 does.
        net = 4_000 - (EXECUTION_COST_MICROALGO + BONUS_FEE_MICROALGO)
        assert net == 0
        assert health(net_to_keeper=net).pays_nothing

    def test_a_negative_margin_counts_too(self) -> None:
        assert health(net_to_keeper=-500).pays_nothing

    def test_a_single_microalgo_of_margin_does_not(self) -> None:
        # Thin is not the same as nothing, and calling it nothing would be a
        # different claim than the one this makes.
        assert not health(net_to_keeper=1).pays_nothing


class TestRunway:
    def test_below_the_threshold_is_flagged(self) -> None:
        assert health(runway_days=LOW_RUNWAY_DAYS - 0.1).low_runway

    def test_at_the_threshold_is_not(self) -> None:
        assert not health(runway_days=LOW_RUNWAY_DAYS).low_runway


class TestOverdue:
    def test_late_within_one_cycle_is_not_overdue(self) -> None:
        # Losing a race is ordinary. Flagging it would make the report useless
        # on any registry with more than one keeper.
        assert not health(rounds_late=1_285, interval_rounds=1_286).overdue

    def test_late_by_more_than_a_whole_cycle_is(self) -> None:
        assert health(rounds_late=1_287, interval_rounds=1_286).overdue

    def test_a_healthy_upkeep_raises_nothing(self) -> None:
        assert health().flags() == []


def test_flags_accumulate_rather_than_shadow_one_another() -> None:
    # An upkeep can be underpaid *and* nearly out of escrow *and* unserviced,
    # and a report that showed only the first would hide the other two.
    bad = health(net_to_keeper=0, runway_days=1.0, rounds_late=99_999, interval_rounds=1_286)
    assert len(bad.flags()) == 3


@pytest.mark.parametrize("runs", [0, 1, 500])
def test_execution_count_never_affects_the_flags(runs: int) -> None:
    # A busy upkeep and an idle one are judged on the same terms. Runs are
    # reported because they are interesting, not because they are healthy.
    assert health(times_executed=runs).flags() == []
