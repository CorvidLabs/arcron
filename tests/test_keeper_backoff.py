"""The keeper bot's backoff schedule.

Pure logic and a JSON file — no chain — so the schedule, the reset and the
survives-a-restart property can all be asserted directly.
"""

import json

import pytest

from scripts.keeper_backoff import (
    MAX_BACKOFF_ROUNDS,
    MAX_INTERVAL_MULTIPLIER,
    TARGET_REFUSAL_BACKOFF_ROUNDS,
    Backoff,
    Entry,
    failure_site,
    is_lost_race,
    is_target_refusal,
)

INTERVAL = 10
# A keeper-side refusal: no inner transaction is named, so `execute` itself is
# what said no and nothing the target does can change the answer.
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
    """Still backed off, but by a round rather than by a whole interval.

    The upkeep is skipped — that half of the old behaviour is what stops a
    dead target being simulated on every scan for ever — and the wait is now
    the first step of the *rounds* ramp, because a refusal from the target's
    own program is the one failure a keeper cannot tell from a cooldown. The
    assertion used to read `1_000 + INTERVAL`; it is `1_000 + 1` on purpose.
    """
    backoff = Backoff(state_file)
    entry = backoff.record_failure(1, BROKEN_TARGET, 1_000, INTERVAL, advanced=False)
    assert entry is not None
    assert entry.next_attempt_round == 1_001
    assert entry.target_refusal is True
    assert backoff.blocked(1, 1_000) is True
    assert backoff.blocked(1, 1_001) is False


def test_an_unreadable_registry_leaves_the_message_in_charge(state_file) -> None:
    """A node that will not answer must not turn every failure into a backoff."""
    backoff = Backoff(state_file)
    assert backoff.record_failure(1, LOST_RACE, 1_000, INTERVAL, advanced=None) is None
    assert backoff.record_failure(2, BROKEN_TARGET, 1_000, INTERVAL, advanced=None) is not None


# --- a target that refused is not a target that is broken --------------
#
# `docs/reviews/2026-09-01-opus-5-audit-verification.md` §3. One blocked
# attempt used to send this keeper away for `1 x interval` rounds, capped at
# MAX_BACKOFF_ROUNDS — about an hour — and a conditional target (an oracle
# rejecting a stale update, a rebalancer on an epoch, anything with a cooldown)
# is indistinguishable from a permanently broken one at the moment it refuses.
# So an attacker bought an hour of every honest keeper's absence for one
# application call, and, worse, an upkeep with escalation off and nobody
# attacking it went unwatched for the same hour.
#
# The schedule now branches on *where* the failure happened, which is the one
# thing about a failure that a target cannot choose.

# A target on a cooldown, refusing the way a real one does: an assert with no
# message on chain, attributed by algod to the inner transaction.
COOLDOWN_REFUSAL = (
    "Txn 6XTU7Y3P4KZ2WQ3O4B5MJ6TSBWLNXKGZC3Z4TIWF7HYJCLWMDGUO had error 'inner tx "
    "0 failed: logic eval error: assert failed pc=249. Details: app=770082145, "
    "pc=249, opcodes===; !; assert. Details: app=769891898, pc=1483, "
    "opcodes=intc_1 // 0; itxn_field Fee; itxn_submit; label36:' at PC 1483:"
)


def test_one_refusal_costs_a_round_not_an_hour(state_file) -> None:
    """The finding, as an assertion.

    A single refusal from a conditional target used to remove this keeper for
    `1 x interval` rounds; on the live registry's 1,286-round cadences that is
    the whole hour the attack in §3 needs. It is one round.
    """
    hourly = 1_286  # the cadence eleven live upkeeps run on
    backoff = Backoff(state_file)
    entry = backoff.record_failure(1, COOLDOWN_REFUSAL, 1_000, hourly, advanced=False)

    assert entry is not None
    assert entry.next_attempt_round == 1_001
    assert backoff.blocked(1, 1_001) is False, "back in the race the very next round"


def test_the_refusal_ramp_doubles_in_rounds_and_stops_well_short_of_an_hour(
    state_file,
) -> None:
    """1, 2, 4 … rounds, and then flat. Never intervals, never the hour cap.

    The ceiling is what bounds the harm: whatever a target does, and however
    many times it does it, this keeper is never more than
    TARGET_REFUSAL_BACKOFF_ROUNDS away from the next attempt. At the measured
    2.752 s/round that is under three minutes, against the 59 minutes
    MAX_BACKOFF_ROUNDS buys.
    """
    hourly = 1_286
    backoff = Backoff(state_file)
    waits = []
    for _ in range(12):
        entry = backoff.record_failure(1, COOLDOWN_REFUSAL, 1_000, hourly, advanced=False)
        assert entry is not None
        waits.append(entry.next_attempt_round - 1_000)

    assert waits[:7] == [1, 2, 4, 8, 16, 32, 64]
    assert set(waits[7:]) == {TARGET_REFUSAL_BACKOFF_ROUNDS}
    assert TARGET_REFUSAL_BACKOFF_ROUNDS < MAX_BACKOFF_ROUNDS // 10


def test_a_refusal_never_waits_longer_than_the_upkeep_can_afford(state_file) -> None:
    """Twelve live upkeeps run every 20 rounds; 64 would cost them windows."""
    fast = 20
    backoff = Backoff(state_file)
    for _ in range(10):
        entry = backoff.record_failure(1, COOLDOWN_REFUSAL, 1_000, fast, advanced=False)
    assert entry is not None
    assert entry.next_attempt_round == 1_000 + fast


def test_a_keeper_side_failure_keeps_the_old_schedule(state_file) -> None:
    """The half of the behaviour that was right, and is unchanged.

    Our own references wrong, a fee below the minimum, an account that cannot
    pay: the same call fails the same way until an operator does something, so
    retrying sooner buys nothing and an hour is the right wait. The split is
    not "be gentler"; it is "be gentler about the one failure that is
    conditional on chain state".
    """
    backoff = Backoff(state_file)
    for expected in (1, 2, 4, 8):
        entry = backoff.record_failure(2, TARGET_REJECTED, 1_000, INTERVAL)
        assert entry is not None
        assert entry.next_attempt_round == 1_000 + expected * INTERVAL
        assert entry.target_refusal is False


def test_the_registry_moving_on_clears_a_streak_of_refusals(state_file) -> None:
    """An execution is proof the target works, so the streak has to go.

    Without this the ramp survived the evidence against it: an upkeep that had
    refused six times and was then executed by somebody kept its entry, and the
    next failure carried on doubling from seven rather than starting again.
    """
    backoff = Backoff(state_file)
    for _ in range(6):
        backoff.record_failure(3, COOLDOWN_REFUSAL, 1_000, INTERVAL, advanced=False)
    assert backoff.entry(3) is not None

    assert backoff.record_failure(3, COOLDOWN_REFUSAL, 1_010, INTERVAL, advanced=True) is None

    assert backoff.entry(3) is None
    assert backoff.blocked(3, 1_010) is False
    entry = backoff.record_failure(3, COOLDOWN_REFUSAL, 1_020, INTERVAL, advanced=False)
    assert entry is not None and entry.failures == 1


def test_a_streak_remembers_when_it_started(state_file) -> None:
    """So the bot can say how long an upkeep has gone unserviced, not just that
    it has. Nothing metered that before, which is the second half of the
    finding: an upkeep with escalation off goes quiet and no report notices."""
    backoff = Backoff(state_file)
    backoff.record_failure(4, COOLDOWN_REFUSAL, 5_000, INTERVAL, advanced=False)
    backoff.record_failure(4, COOLDOWN_REFUSAL, 5_001, INTERVAL, advanced=False)
    entry = backoff.record_failure(4, COOLDOWN_REFUSAL, 5_003, INTERVAL, advanced=False)

    assert entry is not None
    assert entry.since_round == 5_000
    assert entry.failures == 3


def test_next_attempt_round_answers_for_an_upkeep_that_never_failed(state_file) -> None:
    """Zero, not an exception: the cache and the scan clock both ask this of
    every upkeep in the registry, and most of them have never failed."""
    backoff = Backoff(state_file)
    assert backoff.next_attempt_round(9) == 0
    backoff.record_failure(9, COOLDOWN_REFUSAL, 1_000, INTERVAL, advanced=False)
    assert backoff.next_attempt_round(9) == 1_001


# --- the site is recorded, and never scheduled on ----------------------


def test_the_site_names_the_targets_failing_instruction() -> None:
    """The innermost frame, not the keeper's own.

    algod appends a `Details:` clause per frame, innermost first, so the pair
    after the inner-transaction marker is the target's. The keeper's own
    `app=769891898, pc=1483` follows it and is the same for every upkeep in the
    registry, so reporting that one would say nothing at all.
    """
    assert failure_site(COOLDOWN_REFUSAL) == "app=770082145 pc=249"
    assert failure_site(BROKEN_TARGET) == "app=1094 pc=92"
    assert failure_site(LOST_RACE) == "", "a keeper-side refusal has no target frame"
    assert failure_site("") == ""


def test_the_site_does_not_change_the_schedule(state_file) -> None:
    """A hostile target must not get to choose how often it is retried.

    Resetting the ramp when the failing pc moves was considered and rejected:
    a cooldown refuses at the same assert a break does, so it separates
    nothing, and a target that rotated its failing instruction could hold this
    keeper at a one-round retry and spend its request budget for it. Two
    different sites, the same ramp.
    """
    moved = COOLDOWN_REFUSAL.replace("pc=249", "pc=612")
    backoff = Backoff(state_file)
    first = backoff.record_failure(5, COOLDOWN_REFUSAL, 1_000, INTERVAL, advanced=False)
    second = backoff.record_failure(5, moved, 1_000, INTERVAL, advanced=False)

    assert first is not None and second is not None
    assert first.site != second.site
    assert (first.failures, second.failures) == (1, 2)
    assert (second.next_attempt_round - 1_000) == 2


def test_is_target_refusal_is_the_single_definition() -> None:
    """`registry_health.classify_failure` answers the same question, and used
    to spell it differently. One of the two drifting is how a report comes to
    say TARGET REVERTS about something the bot is treating as its own bug."""
    from scripts.registry_health import classify_failure

    assert is_target_refusal(COOLDOWN_REFUSAL) is True
    assert is_target_refusal(LOST_RACE) is False
    assert classify_failure(COOLDOWN_REFUSAL) == "TARGET REVERTS"
