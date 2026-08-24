"""The staleness watchdog: rounds compared, values never inspected.

The security claim is that this contract cannot be fed a wrong price because
it never reads one. The tests check the boundary conditions of the comparison,
and the recovery policy the module docstring argues for.
"""

from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.watchdog.contract import MIN_THRESHOLD_ROUNDS, Watchdog

START_ROUND = 1_000
THRESHOLD = 100


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def reporter(context: AlgopyTestContext):
    return context.any.account()


@pytest.fixture()
def watchdog(context: AlgopyTestContext, reporter) -> Watchdog:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Watchdog()
    contract.configure(arc4.Address(reporter), UInt64(THRESHOLD))
    return contract


def _at(context: AlgopyTestContext, round_number: int) -> None:
    context.ledger.patch_global_fields(round=UInt64(round_number))


def _update(context: AlgopyTestContext, watchdog: Watchdog, reporter, value: int) -> int:
    with context.txn.create_group(active_txn_overrides={"sender": reporter}):
        return watchdog.update(UInt64(value))


# --- configuration ----------------------------------------------------

def test_configuring_starts_the_clock(context, watchdog) -> None:
    # A feed that never reports at all must still be caught.
    assert watchdog.updated_round.value == START_ROUND
    assert watchdog.is_stale() is False


def test_threshold_must_exceed_ordinary_keeper_lateness(context, reporter) -> None:
    contract = Watchdog()
    with pytest.raises(AssertionError, match="Threshold below minimum"):
        contract.configure(arc4.Address(reporter), UInt64(MIN_THRESHOLD_ROUNDS - 1))


def test_configure_is_once_only(context, watchdog, reporter) -> None:
    with pytest.raises(AssertionError, match="Already configured"):
        watchdog.configure(arc4.Address(reporter), UInt64(THRESHOLD))


# --- the comparison, at its boundaries --------------------------------

def test_a_fresh_feed_is_not_flagged(context, watchdog) -> None:
    _at(context, START_ROUND + 1)
    assert watchdog.check_freshness() == 0
    assert watchdog.is_stale() is False


def test_exactly_at_the_threshold_is_still_fresh(context, watchdog) -> None:
    # Silence of exactly `threshold` rounds is permitted; more is not.
    _at(context, START_ROUND + THRESHOLD)
    assert watchdog.check_freshness() == 0
    assert watchdog.is_stale() is False


def test_one_round_past_the_threshold_flags_it(context, watchdog) -> None:
    flag_round = START_ROUND + THRESHOLD + 1
    _at(context, flag_round)
    assert watchdog.check_freshness() == flag_round
    assert watchdog.is_stale() is True
    assert watchdog.stale_since.value == flag_round
    assert watchdog.stale_episodes.value == 1


def test_flagging_happens_once_per_episode(context, watchdog) -> None:
    """Arcron keeps calling; a flagged feed must stop doing work.

    Re-flagging every cadence would spend opcode budget and emit an event per
    sweep for a condition that has not changed.
    """
    _at(context, START_ROUND + THRESHOLD + 1)
    watchdog.check_freshness()
    for later in (START_ROUND + THRESHOLD + 2, START_ROUND + 10 * THRESHOLD):
        _at(context, later)
        assert watchdog.check_freshness() == 0
    assert watchdog.stale_episodes.value == 1


def test_checks_are_counted_even_when_nothing_happens(context, watchdog) -> None:
    for offset in (1, 2, 3):
        _at(context, START_ROUND + offset)
        watchdog.check_freshness()
    assert watchdog.checks.value == 3


# --- reporting and recovery -------------------------------------------

def test_an_update_moves_the_clock(context, watchdog, reporter) -> None:
    _at(context, START_ROUND + 50)
    assert _update(context, watchdog, reporter, 42) == START_ROUND + 50
    assert watchdog.reading() == 42
    # The threshold now runs from the new update, not the old one.
    _at(context, START_ROUND + 50 + THRESHOLD)
    assert watchdog.check_freshness() == 0


def test_only_the_reporter_can_update(context, watchdog) -> None:
    with pytest.raises(AssertionError, match="Only the reporter can update"):
        watchdog.update(UInt64(1))


def test_an_update_clears_the_flag_and_records_the_episode(
    context, watchdog, reporter
) -> None:
    _at(context, START_ROUND + THRESHOLD + 1)
    watchdog.check_freshness()
    assert watchdog.is_stale() is True

    recovery_round = START_ROUND + THRESHOLD + 40
    _at(context, recovery_round)
    _update(context, watchdog, reporter, 7)

    assert watchdog.is_stale() is False
    assert watchdog.last_recovery_round.value == recovery_round
    # The episode is remembered, so a cautious consumer can impose its own
    # cool-down without anyone's permission.
    assert watchdog.stale_episodes.value == 1


def test_a_second_outage_is_a_second_episode(context, watchdog, reporter) -> None:
    _at(context, START_ROUND + THRESHOLD + 1)
    watchdog.check_freshness()
    _at(context, START_ROUND + THRESHOLD + 10)
    _update(context, watchdog, reporter, 1)

    second_flag = START_ROUND + 2 * THRESHOLD + 20
    _at(context, second_flag)
    assert watchdog.check_freshness() == second_flag
    assert watchdog.stale_episodes.value == 2


# --- the security claim -----------------------------------------------

def test_the_watchdog_never_reads_the_value(context, watchdog, reporter) -> None:
    """Freshness must not depend on the value, or it could be gamed by it.

    Same silence, wildly different readings, identical verdict — the contract
    compares rounds and nothing else, which is why it cannot be fed a wrong
    price.
    """
    for reading in (0, 1, 2**64 - 1):
        _at(context, START_ROUND + 10)
        _update(context, watchdog, reporter, reading)
        _at(context, START_ROUND + 10 + THRESHOLD)
        assert watchdog.check_freshness() == 0
        _at(context, START_ROUND + 11 + THRESHOLD)
        assert watchdog.check_freshness() != 0
        # Recover for the next iteration.
        _update(context, watchdog, reporter, reading)


def test_rounds_since_update_is_observable(context, watchdog) -> None:
    _at(context, START_ROUND + 37)
    assert watchdog.rounds_since_update() == 37
