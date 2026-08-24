"""The rain draw's accounting.

The scheduled call is the one that must never misbehave: Archon calls `draw`
on every cadence whether or not there is anything to draw for, so the quiet
path has to be a clean no-op rather than a failure that would trip keeper
backoff. Most of what follows is about that, and about the money adding up.

Resolution needs an inner call to a beacon, which mocks record without
executing — that half lives in scripts/rain_demo.py on LocalNet.
"""

from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.rain.contract import (
    ALLOCATION_MBR,
    BEACON_DELAY,
    TICKET_MBR,
    Rain,
)

BEACON_APP = 600_011_887
START_ROUND = 1_000


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def rain(context: AlgopyTestContext) -> Rain:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP))
    return contract


def _enter(context: AlgopyTestContext, rain: Rain, amount: int = TICKET_MBR) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address, amount=amount
    )
    return rain.enter(payment)


def _deposit(context: AlgopyTestContext, rain: Rain, amount: int) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address, amount=amount
    )
    return rain.deposit(payment)


# --- configuration ----------------------------------------------------

def test_configure_is_once_and_creator_only(context: AlgopyTestContext) -> None:
    rain = Rain()
    rain.configure(UInt64(BEACON_APP))
    assert rain.beacon_app.value == BEACON_APP
    with pytest.raises(AssertionError, match="Already configured"):
        rain.configure(UInt64(123))


# --- tickets and pot --------------------------------------------------

def test_tickets_are_numbered_from_zero(context: AlgopyTestContext, rain: Rain) -> None:
    assert _enter(context, rain) == 0
    assert _enter(context, rain) == 1
    assert rain.tickets.value == 2


def test_a_ticket_must_pay_its_own_box(context: AlgopyTestContext, rain: Rain) -> None:
    with pytest.raises(AssertionError, match="MBR payment too small"):
        _enter(context, rain, amount=TICKET_MBR - 1)


def test_deposits_accumulate(context: AlgopyTestContext, rain: Rain) -> None:
    assert _deposit(context, rain, 500_000) == 500_000
    assert _deposit(context, rain, 250_000) == 750_000


def test_a_deposit_must_be_positive(context: AlgopyTestContext, rain: Rain) -> None:
    with pytest.raises(AssertionError, match="Amount must be positive"):
        _deposit(context, rain, 0)


# --- the scheduled call, which must never blow up ---------------------

def test_draw_is_a_no_op_with_no_tickets(context: AlgopyTestContext, rain: Rain) -> None:
    _deposit(context, rain, 1_000_000)
    assert rain.draw() == 0
    assert rain.draw_open.value == 0
    assert rain.pot.value == 1_000_000  # untouched


def test_draw_is_a_no_op_with_an_empty_pot(context: AlgopyTestContext, rain: Rain) -> None:
    _enter(context, rain)
    assert rain.draw() == 0
    assert rain.draw_open.value == 0


def test_draw_is_a_no_op_when_the_pot_only_covers_the_reservation(
    context: AlgopyTestContext, rain: Rain
) -> None:
    # A prize of zero is not a prize; the reservation alone must not trigger one.
    _enter(context, rain)
    _deposit(context, rain, ALLOCATION_MBR)
    assert rain.draw() == 0


def test_draw_is_a_no_op_while_one_is_already_open(
    context: AlgopyTestContext, rain: Rain
) -> None:
    _enter(context, rain)
    _deposit(context, rain, 1_000_000)
    assert rain.draw() == 1
    # Archon will call again on the next cadence, before anyone resolved.
    assert rain.draw() == 0
    assert rain.draw_id.value == 1


def test_draw_locks_the_prize_and_a_future_beacon_round(
    context: AlgopyTestContext, rain: Rain
) -> None:
    _enter(context, rain)
    _enter(context, rain)
    _deposit(context, rain, 1_000_000)

    assert rain.draw() == 1
    assert rain.draw_open.value == 1
    assert rain.prize.value == 1_000_000 - ALLOCATION_MBR
    assert rain.pot.value == 0
    assert rain.tickets_snapshot.value == 2
    # The deciding round is in the future, so nobody can know the winner yet.
    assert rain.commit_round.value == START_ROUND + BEACON_DELAY


def test_a_later_deposit_belongs_to_the_next_draw(
    context: AlgopyTestContext, rain: Rain
) -> None:
    _enter(context, rain)
    _deposit(context, rain, 1_000_000)
    rain.draw()
    assert _deposit(context, rain, 400_000) == 400_000
    assert rain.prize.value == 1_000_000 - ALLOCATION_MBR


# --- resolution guards (the beacon call itself needs a real AVM) -------

def test_resolve_needs_an_open_draw(context: AlgopyTestContext, rain: Rain) -> None:
    with pytest.raises(AssertionError, match="No draw is open"):
        rain.resolve()


def test_resolve_waits_for_the_beacon_round(context: AlgopyTestContext, rain: Rain) -> None:
    _enter(context, rain)
    _deposit(context, rain, 1_000_000)
    rain.draw()
    for round_number in (START_ROUND, START_ROUND + BEACON_DELAY):
        context.ledger.patch_global_fields(round=UInt64(round_number))
        with pytest.raises(AssertionError, match="Beacon round has not passed"):
            rain.resolve()


# --- claiming ---------------------------------------------------------

def test_claiming_nothing_is_rejected(context: AlgopyTestContext, rain: Rain) -> None:
    with pytest.raises(AssertionError, match="Nothing allocated to you"):
        rain.claim()


def test_allocation_of_an_unknown_account_is_zero(
    context: AlgopyTestContext, rain: Rain
) -> None:
    stranger = context.any.account()
    assert rain.allocation_of(arc4.Address(stranger)) == 0


def test_reservation_covers_the_allocation_box(context: AlgopyTestContext, rain: Rain) -> None:
    """The prize is the pot less exactly one allocation box.

    Resolving must never fail for want of minimum balance, so the box the
    winner's allocation will live in is paid for when the draw opens.
    """
    _enter(context, rain)
    _deposit(context, rain, 1_000_000)
    rain.draw()
    assert rain.prize.value + ALLOCATION_MBR == 1_000_000
    # And that reservation is exactly what a box of that shape costs.
    assert ALLOCATION_MBR == 2_500 + 400 * (1 + 32 + 8)
