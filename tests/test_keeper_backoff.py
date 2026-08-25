"""The keeper bot's backoff schedule.

Pure logic and a JSON file — no chain — so the schedule, the reset and the
survives-a-restart property can all be asserted directly.
"""

import json

import pytest

from scripts.keeper_backoff import (
    MAX_BACKOFF_ROUNDS,
    MAX_INTERVAL_MULTIPLIER,
    Backoff,
    Entry,
    is_lost_race,
)

INTERVAL = 10
TARGET_REJECTED = "logic eval error: assert failed"


@pytest.fixture()
def state_file(tmp_path):
    return tmp_path / "backoff.json"


def test_backs_off_exponentially_in_intervals(state_file) -> None:
    backoff = Backoff(state_file)
    round_now = 1_000

    # 1, 2, 4, 8 intervals after each consecutive failure.
    for expected_multiplier in (1, 2, 4, 8):
        entry = backoff.record_failure(1, TARGET_REJECTED, round_now, INTERVAL)
        assert entry is not None
        assert entry.next_attempt_round == round_now + expected_multiplier * INTERVAL

    # Then it stops doubling: a broken upkeep is retried forever, just rarely.
    # Retrying is free, so there is no case for backing off indefinitely.
    entry = backoff.record_failure(1, TARGET_REJECTED, round_now, INTERVAL)
    assert entry is not None
    assert entry.next_attempt_round == round_now + MAX_INTERVAL_MULTIPLIER * INTERVAL


def test_a_slow_upkeep_is_still_retried_promptly(state_file) -> None:
    """The wait is capped in rounds, not just in intervals.

    A daily upkeep at 8x its interval would go unretried for over a week. Since
    a failed attempt costs nothing, the only thing that buys is a slow recovery
    once someone fixes the target.
    """
    daily = 30_857
    backoff = Backoff(state_file)
    for _ in range(5):
        entry = backoff.record_failure(1, TARGET_REJECTED, 1_000, daily)
    assert entry is not None
    assert entry.next_attempt_round == 1_000 + MAX_BACKOFF_ROUNDS


def test_blocks_only_until_the_next_attempt_round(state_file) -> None:
    backoff = Backoff(state_file)
    backoff.record_failure(7, TARGET_REJECTED, 1_000, INTERVAL)

    assert backoff.blocked(7, 1_000) is True
    assert backoff.blocked(7, 1_009) is True
    assert backoff.blocked(7, 1_010) is False
    # An upkeep that never failed is never blocked.
    assert backoff.blocked(8, 1_000) is False


def test_success_clears_the_backoff(state_file) -> None:
    backoff = Backoff(state_file)
    backoff.record_failure(3, TARGET_REJECTED, 1_000, INTERVAL)
    backoff.record_failure(3, TARGET_REJECTED, 1_000, INTERVAL)
    assert backoff.entry(3) is not None

    backoff.record_success(3)

    assert backoff.entry(3) is None
    # The next failure starts the schedule over, not where it left off.
    entry = backoff.record_failure(3, TARGET_REJECTED, 2_000, INTERVAL)
    assert entry is not None
    assert entry.failures == 1
    assert entry.next_attempt_round == 2_000 + INTERVAL


@pytest.mark.parametrize(
    "reason",
    [
        "logic eval error: Not due",
        "assert failed: Upkeep not found",
        "NOT DUE",
    ],
)
def test_losing_a_race_is_not_a_failure(state_file, reason: str) -> None:
    """The common case in a healthy network, and it must never back off.

    Another keeper got there first, or the upkeep was cancelled mid-flight.
    Neither means the upkeep is broken, neither costs a fee, and a keeper that
    stopped trying everything it lost a race for would service less and less
    of the registry.
    """
    assert is_lost_race(reason) is True

    backoff = Backoff(state_file)
    assert backoff.record_failure(1, reason, 1_000, INTERVAL) is None
    assert backoff.entry(1) is None
    assert backoff.blocked(1, 1_000) is False


def test_state_survives_a_restart(state_file) -> None:
    first = Backoff(state_file)
    first.record_failure(2, TARGET_REJECTED, 1_000, INTERVAL)
    first.record_failure(2, TARGET_REJECTED, 1_000, INTERVAL)

    # A fresh process — a --once cron invocation, say — sees the same state.
    restarted = Backoff(state_file)
    entry = restarted.entry(2)
    assert entry is not None
    assert entry.failures == 2
    assert restarted.blocked(2, 1_000) is True
    assert entry.reason.startswith("logic eval error")


def test_clear_is_the_operator_escape_hatch(state_file) -> None:
    backoff = Backoff(state_file)
    backoff.record_failure(1, TARGET_REJECTED, 1_000, INTERVAL)
    backoff.record_failure(2, TARGET_REJECTED, 1_000, INTERVAL)

    assert backoff.clear(1) == 1
    assert backoff.blocked(1, 1_000) is False
    assert backoff.blocked(2, 1_000) is True

    assert backoff.clear() == 1
    assert Backoff(state_file).entries == {}


def test_running_without_a_state_file_is_allowed(state_file) -> None:
    ephemeral = Backoff(None)
    ephemeral.record_failure(1, TARGET_REJECTED, 1_000, INTERVAL)
    assert ephemeral.blocked(1, 1_000) is True
    assert not state_file.exists()


def test_unreadable_state_never_stops_the_bot(state_file) -> None:
    state_file.write_text("{ this is not json")
    backoff = Backoff(state_file)
    assert backoff.entries == {}
    # And it recovers by overwriting on the next write.
    backoff.record_failure(1, TARGET_REJECTED, 1_000, INTERVAL)
    assert json.loads(state_file.read_text())["entries"]["1"]["failures"] == 1


def test_blocked_ids_reports_what_is_being_skipped(state_file) -> None:
    backoff = Backoff(state_file)
    backoff.record_failure(5, TARGET_REJECTED, 1_000, INTERVAL)
    backoff.record_failure(9, TARGET_REJECTED, 1_000, INTERVAL)
    backoff.entries[9] = Entry(failures=1, next_attempt_round=1_005, reason="x")

    assert backoff.blocked_ids(1_000) == [5, 9]
    assert backoff.blocked_ids(1_006) == [5]
    assert backoff.blocked_ids(2_000) == []


# --- a target must not be able to disguise itself as a lost race -------


def test_a_target_saying_not_due_is_not_a_lost_race() -> None:
    """The failure Kimi found: the marker was matched anywhere in the string.

    A target's own assert text travels back in the same error, and the target
    chooses that text. One asserting "cooldown not due" was read as another
    keeper having won, so the upkeep was never backed off and the bot retried
    a broken target forever at its own expense.

    algod names the application that failed, and the target does not control
    that.
    """
    target = (
        "Runtime error when executing Pulse (appId: 1004) in transaction 0: "
        "cooldown not due"
    )
    keeper = (
        "Runtime error when executing Keeper (appId: 1002) in transaction 0: Not due"
    )
    assert is_lost_race(keeper) is True
    assert is_lost_race(target) is False


def test_a_real_keeper_error_is_still_a_failure() -> None:
    """Attribution to the keeper is necessary, not sufficient."""
    assert (
        is_lost_race(
            "Runtime error when executing Keeper (appId: 1) in transaction 0: "
            "Fee below minimum"
        )
        is False
    )


def test_an_error_with_no_attribution_falls_back_to_the_message() -> None:
    """Not every error shape names the app; the message is then all there is."""
    assert is_lost_race("logic eval error: Not due") is True
    assert is_lost_race("upkeep not found") is True
    assert is_lost_race("something else entirely") is False
