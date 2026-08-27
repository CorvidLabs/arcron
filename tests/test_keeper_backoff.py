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
#
# Every string below was copied from a real failure on a real node, because
# the point of these tests is that the classifier meets what algod actually
# writes. The earlier versions asserted against "Runtime error when executing
# Pulse (…)", which no keeper can ever see: algokit-utils renders that phrase
# from the *caller's* own app spec, so the name in it is always "Keeper".

# A race lost to another keeper, rejected by the pool after broadcast.
LOST_RACE = (
    "Txn UE52VS3EFC3CHDWSBSWAKPQO5TNTZSABGUDEYCCEBC2E7VLICYBA had error 'Runtime "
    "error when executing Keeper (appId: 1002) in transaction "
    "UE52VS3EFC3CHDWSBSWAKPQO5TNTZSABGUDEYCCEBC2E7VLICYBA: Not due' at PC 1122: "
    "| TransactionPool.Remember: transaction "
    "UE52VS3EFC3CHDWSBSWAKPQO5TNTZSABGUDEYCCEBC2E7VLICYBA: logic eval error: "
    "assert failed pc=1122. Details: app=1002, pc=1122, opcodes=global Round; "
    "<=; assert"
)
# A target that rejects the call the upkeep registered.
BROKEN_TARGET = (
    "Txn AJ7X6DCHC3Z4TIWF7HYJCLWMDGUOC3I5OFRHM3JWVQD2QZ7FBE5A had error 'inner tx "
    "0 failed: logic eval error: err opcode executed. Details: app=1094, pc=92, "
    "opcodes=txna ApplicationArgs 0; match label3 label4; err; label2:' at PC 1483:"
)


def test_a_target_saying_not_due_is_not_a_lost_race() -> None:
    """A target has a say in this string; it has no say in who failed.

    On-chain failures carry no assert text, but algod disassembles the failing
    program into the error, so a target *can* get chosen words in front of a
    keeper by putting them in a byte constant. What it cannot do is fail
    without the node saying the failure happened in an inner transaction:
    `execute` checks the schedule before it calls anything, so a keeper-side
    refusal never carries that marker and a target-side one always does.
    """
    hostile = (
        "Txn AJ7X6DCHC3Z4TIWF7HYJCLWMDGUOC3I5OFRHM3JWVQD2QZ7FBE5A had error 'inner "
        "tx 0 failed: logic eval error: assert failed pc=42. Details: app=1094, "
        'pc=42, opcodes=pushbytes 0x6e6f742064756500 // "not due"; log; assert\' '
        "at PC 1483:"
    )
    assert is_lost_race(LOST_RACE) is True
    assert is_lost_race(hostile) is False
    assert is_lost_race(BROKEN_TARGET) is False


def test_a_real_keeper_error_is_still_a_failure() -> None:
    """Coming from the keeper contract is necessary, not sufficient."""
    assert (
        is_lost_race(
            "Runtime error when executing Keeper (appId: 1) in transaction 0: "
            "Fee below minimum"
        )
        is False
    )


def test_an_error_with_no_attribution_falls_back_to_the_message() -> None:
    """Not every error shape names an inner transaction; the message is then all
    there is."""
    assert is_lost_race("logic eval error: Not due") is True
    assert is_lost_race("upkeep not found") is True
    assert is_lost_race("something else entirely") is False


# --- what the registry says outranks what the error says ---------------


def test_the_registry_moving_on_is_a_lost_race_whatever_the_error_said(
    state_file,
) -> None:
    """The shape a message-only classifier gets wrong on a public network.

    A losing keeper's transaction is not always refused at broadcast. Its own
    node can accept it, because the winner's has not reached that node yet, and then
    it simply never lands, so what comes back is a timeout that mentions
    neither "not due" nor anything else a keeper could read. Backing off on
    that would punish an upkeep for being popular.
    """
    timed_out = "Wait for transaction id 6XTU7Y3P4KZ2WQ3O4B5MJ6TSBWLNXKGZ timed out"
    assert is_lost_race(timed_out) is False

    backoff = Backoff(state_file)
    assert (
        backoff.record_failure(1, timed_out, 1_000, INTERVAL, advanced=True) is None
    )
    assert backoff.blocked(1, 1_000) is False


def test_the_registry_standing_still_does_not_overrule_a_clear_race(
    state_file,
) -> None:
    """False means "no news", not "nothing happened".

    The winner's transaction sits in the pool for a round before it commits, so
    a keeper refused in that window reads a box that has not moved yet. The
    error is unambiguous there, and it wins.
    """
    backoff = Backoff(state_file)
    assert backoff.record_failure(1, LOST_RACE, 1_000, INTERVAL, advanced=False) is None
    assert backoff.blocked(1, 1_000) is False


def test_a_broken_target_is_backed_off_with_the_registry_agreeing(state_file) -> None:
    backoff = Backoff(state_file)
    entry = backoff.record_failure(1, BROKEN_TARGET, 1_000, INTERVAL, advanced=False)
    assert entry is not None
    assert entry.next_attempt_round == 1_000 + INTERVAL
    assert backoff.blocked(1, 1_000) is True


def test_an_unreadable_registry_leaves_the_message_in_charge(state_file) -> None:
    """A node that will not answer must not turn every failure into a backoff."""
    backoff = Backoff(state_file)
    assert backoff.record_failure(1, LOST_RACE, 1_000, INTERVAL, advanced=None) is None
    assert backoff.record_failure(2, BROKEN_TARGET, 1_000, INTERVAL, advanced=None) is not None
