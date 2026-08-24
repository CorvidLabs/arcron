"""A dead man's switch, where the interesting behaviour is the waiting.

`sweep` is called by Arcron on every cadence for the entire life of the
switch, and almost every one of those calls must do nothing, cheaply, without
failing. A failing target trips keeper backoff — and a switch nobody is
watching is not a switch. Most of these tests are about that.
"""

from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.deadman.contract import MIN_INTERVAL_ROUNDS, DeadMan

START_ROUND = 1_000
INTERVAL = 100
ESCROW = 5_000_000


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def switch(context: AlgopyTestContext) -> DeadMan:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    return DeadMan()


@pytest.fixture()
def beneficiary(context: AlgopyTestContext):
    return context.any.account()


def _arm(
    context: AlgopyTestContext,
    switch: DeadMan,
    beneficiary,
    *,
    amount: int = ESCROW,
    interval: int = INTERVAL,
) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(switch).address, amount=amount
    )
    return switch.arm(payment, arc4.Address(beneficiary), UInt64(interval))


# --- arming -----------------------------------------------------------

def test_arming_sets_the_first_deadline(context, switch, beneficiary) -> None:
    assert _arm(context, switch, beneficiary) == START_ROUND + INTERVAL
    assert switch.escrow.value == ESCROW
    assert switch.beneficiary.value == beneficiary


def test_cannot_arm_twice(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    with pytest.raises(AssertionError, match="Already armed"):
        _arm(context, switch, beneficiary)


def test_interval_must_leave_a_keeper_room_to_work(context, switch, beneficiary) -> None:
    # Arcron's own minimum cadence is 10 rounds; a switch that expires faster
    # than it can be watched would fire on a keeper's ordinary lateness.
    with pytest.raises(AssertionError, match="Interval below minimum"):
        _arm(context, switch, beneficiary, interval=MIN_INTERVAL_ROUNDS - 1)


def test_the_owner_cannot_be_their_own_beneficiary(context, switch) -> None:
    with pytest.raises(AssertionError, match="Beneficiary must not be the owner"):
        _arm(context, switch, context.default_sender)


def test_arming_with_nothing_is_pointless_and_rejected(context, switch, beneficiary) -> None:
    with pytest.raises(AssertionError, match="Nothing to release"):
        _arm(context, switch, beneficiary, amount=0)


# --- the quiet path, which is the common one --------------------------

def test_sweep_before_arming_does_nothing(context, switch) -> None:
    assert switch.sweep() == 0


def test_sweep_does_nothing_while_the_owner_is_present(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    for round_number in (START_ROUND, START_ROUND + 1, START_ROUND + INTERVAL - 1):
        context.ledger.patch_global_fields(round=UInt64(round_number))
        assert switch.sweep() == 0
        assert switch.fired_round.value == 0
        assert switch.escrow.value == ESCROW


def test_checking_in_pushes_the_deadline_out(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL - 1))

    assert switch.check_in() == START_ROUND + INTERVAL - 1 + INTERVAL
    assert switch.check_ins.value == 1
    # The round that would have fired it now passes uneventfully.
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    assert switch.sweep() == 0


# --- firing -----------------------------------------------------------

def test_fires_once_the_deadline_passes(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    fire_round = START_ROUND + INTERVAL
    context.ledger.patch_global_fields(round=UInt64(fire_round))

    assert switch.sweep() == fire_round
    assert switch.has_fired() is True
    assert switch.allocated.value == ESCROW
    assert switch.escrow.value == 0


def test_a_fired_switch_is_inert_forever_after(context, switch, beneficiary) -> None:
    """Arcron keeps calling; a fired switch must stop doing anything at all.

    Otherwise the upkeep keeps paying keepers to re-fire a switch that has
    already fired, which is escrow spent on nothing.
    """
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    fired_at = switch.sweep()

    for later in (START_ROUND + INTERVAL + 1, START_ROUND + 10 * INTERVAL):
        context.ledger.patch_global_fields(round=UInt64(later))
        assert switch.sweep() == 0
        assert switch.fired_round.value == fired_at
        assert switch.allocated.value == ESCROW


def test_the_owner_cannot_check_in_after_it_fires(context, switch, beneficiary) -> None:
    # The whole point: going quiet is irreversible.
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    switch.sweep()
    with pytest.raises(AssertionError, match="Already fired"):
        switch.check_in()


def test_only_the_owner_can_check_in(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    with context.txn.create_group(active_txn_overrides={"sender": beneficiary}):
        with pytest.raises(AssertionError, match="Only the owner can check in"):
            switch.check_in()


# --- claiming ---------------------------------------------------------

def test_the_beneficiary_pulls_the_escrow(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    switch.sweep()

    with context.txn.create_group(active_txn_overrides={"sender": beneficiary}):
        assert switch.claim() == ESCROW
    payment = context.txn.last_group.itxn_groups[-1][0]
    assert payment.amount == ESCROW
    assert payment.receiver == beneficiary
    assert switch.allocated.value == 0


def test_nobody_can_claim_before_it_fires(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    with context.txn.create_group(active_txn_overrides={"sender": beneficiary}):
        with pytest.raises(AssertionError, match="Switch has not fired"):
            switch.claim()


def test_a_stranger_cannot_claim(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    switch.sweep()
    with pytest.raises(AssertionError, match="Only the beneficiary can claim"):
        switch.claim()  # the owner, in this context


def test_claiming_twice_is_rejected(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    switch.sweep()
    with context.txn.create_group(active_txn_overrides={"sender": beneficiary}):
        switch.claim()
        with pytest.raises(AssertionError, match="Nothing left to claim"):
            switch.claim()


# --- what an observer sees -------------------------------------------

def test_rounds_remaining_counts_down_then_reads_zero(context, switch, beneficiary) -> None:
    _arm(context, switch, beneficiary)
    assert switch.rounds_remaining() == INTERVAL
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL - 5))
    assert switch.rounds_remaining() == 5
    context.ledger.patch_global_fields(round=UInt64(START_ROUND + INTERVAL))
    assert switch.rounds_remaining() == 0
    switch.sweep()
    assert switch.rounds_remaining() == 0
