"""Treasury distribution: the arithmetic, and the promise that nothing strands.

Two things must hold on every scheduled call. It must never fail — a failing
hook trips keeper backoff and ends the schedule. And it must never allocate
more than it snapshotted, or the treasury would owe money it does not have.
"""

from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.treasury.contract import (
    MAX_RECIPIENTS,
    TOTAL_SHARE_BPS,
    Recipient,
    Treasury,
)

MBR = 200_000


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def parties(context: AlgopyTestContext):
    return [context.any.account() for _ in range(3)]


def _configure(context: AlgopyTestContext, treasury: Treasury, splits) -> int:
    recipients = arc4.DynamicArray[Recipient](
        *[
            Recipient(
                who=arc4.Address(who), share_bps=arc4.UInt64(bps), owed=arc4.UInt64(0)
            )
            for who, bps in splits
        ]
    )
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(treasury).address, amount=MBR
    )
    return treasury.configure(payment, recipients)


@pytest.fixture()
def treasury(context: AlgopyTestContext, parties) -> Treasury:
    contract = Treasury()
    _configure(context, contract, [(parties[0], 5_000), (parties[1], 3_000), (parties[2], 2_000)])
    return contract


def _deposit(context: AlgopyTestContext, treasury: Treasury, amount: int) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(treasury).address, amount=amount
    )
    return treasury.deposit(payment)


# --- configuration ----------------------------------------------------

def test_shares_must_total_ten_thousand(context, parties) -> None:
    contract = Treasury()
    with pytest.raises(AssertionError, match="Shares must total"):
        _configure(context, contract, [(parties[0], 5_000), (parties[1], 4_000)])


def test_a_share_must_be_positive(context, parties) -> None:
    contract = Treasury()
    with pytest.raises(AssertionError, match="A share must be positive"):
        _configure(context, contract, [(parties[0], TOTAL_SHARE_BPS), (parties[1], 0)])


def test_recipients_are_fixed_forever(context, treasury, parties) -> None:
    # No lever exists to redirect the money — that is the governance property.
    with pytest.raises(AssertionError, match="Already configured"):
        _configure(context, treasury, [(parties[0], TOTAL_SHARE_BPS)])


def test_recipient_count_is_bounded(context, parties) -> None:
    contract = Treasury()
    many = [(parties[0], 1) for _ in range(MAX_RECIPIENTS + 1)]
    with pytest.raises(AssertionError, match="Recipient count out of bounds"):
        _configure(context, contract, many)


# --- the scheduled call -----------------------------------------------

def test_distributing_an_empty_treasury_does_nothing(context, treasury) -> None:
    assert treasury.distribute() == 0
    assert treasury.distributions.value == 0


def test_distribution_splits_by_share(context, treasury, parties) -> None:
    _deposit(context, treasury, 1_000_000)
    assert treasury.distribute() == 1_000_000

    assert treasury.owed_to(arc4.Address(parties[0])) == 500_000
    assert treasury.owed_to(arc4.Address(parties[1])) == 300_000
    assert treasury.owed_to(arc4.Address(parties[2])) == 200_000
    assert treasury.balance.value == 0
    assert treasury.distributions.value == 1


def test_allocations_accumulate_across_distributions(context, treasury, parties) -> None:
    for _ in range(3):
        _deposit(context, treasury, 1_000_000)
        treasury.distribute()
    assert treasury.owed_to(arc4.Address(parties[0])) == 1_500_000
    assert treasury.distributions.value == 3


def test_a_remainder_carries_rather_than_stranding(context, treasury, parties) -> None:
    """Integer division must not lose µALGO or invent them.

    7 does not divide by 50/30/20, so something has to happen to the dust; it
    stays in the treasury for next time.
    """
    _deposit(context, treasury, 7)
    allocated = treasury.distribute()

    owed = sum(
        treasury.owed_to(arc4.Address(party)) for party in parties
    )
    assert allocated == owed
    assert allocated < 7
    # Nothing vanished: what was not allocated is still awaiting distribution.
    assert treasury.balance.value == 7 - allocated


def test_never_allocates_more_than_it_snapshotted(context, treasury, parties) -> None:
    for amount in (1, 3, 999, 1_000_001, 2**32):
        _deposit(context, treasury, amount)
        before = treasury.balance.value
        allocated = treasury.distribute()
        assert allocated <= before
        assert treasury.balance.value == before - allocated


def test_a_deposit_after_the_snapshot_belongs_to_the_next_round(context, treasury) -> None:
    _deposit(context, treasury, 1_000_000)
    treasury.distribute()
    assert _deposit(context, treasury, 400_000) == 400_000
    assert treasury.distribute() == 400_000


# --- claiming ---------------------------------------------------------

def test_a_recipient_pulls_their_allocation(context, treasury, parties) -> None:
    _deposit(context, treasury, 1_000_000)
    treasury.distribute()

    with context.txn.create_group(active_txn_overrides={"sender": parties[1]}):
        assert treasury.claim() == 300_000
    payment = context.txn.last_group.itxn_groups[-1][0]
    assert payment.amount == 300_000
    assert payment.receiver == parties[1]
    assert treasury.owed_to(arc4.Address(parties[1])) == 0


def test_claiming_twice_finds_nothing(context, treasury, parties) -> None:
    _deposit(context, treasury, 1_000_000)
    treasury.distribute()
    with context.txn.create_group(active_txn_overrides={"sender": parties[0]}):
        treasury.claim()
        with pytest.raises(AssertionError, match="Nothing owed to you"):
            treasury.claim()


def test_a_stranger_cannot_claim(context, treasury) -> None:
    _deposit(context, treasury, 1_000_000)
    treasury.distribute()
    stranger = context.any.account()
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(AssertionError, match="Not a recipient"):
            treasury.claim()


def test_an_unclaimed_allocation_never_blocks_a_distribution(
    context, treasury, parties
) -> None:
    # A recipient who never claims must not stall the schedule for anyone else.
    _deposit(context, treasury, 1_000_000)
    treasury.distribute()
    _deposit(context, treasury, 1_000_000)
    assert treasury.distribute() == 1_000_000
    assert treasury.owed_to(arc4.Address(parties[0])) == 1_000_000


def test_a_very_large_pot_still_distributes(
    context: AlgopyTestContext, treasury: Treasury, parties
) -> None:
    """`snapshot * share_bps` overflowed above about 1.84 million ALGO.

    The AVM panics rather than saturating, so `distribute` would have failed
    forever from that point. `claim` only pays what is already owed, so
    everything still in `balance` would have been stranded on a contract with
    no delete path.
    """
    huge = 2**64 // TOTAL_SHARE_BPS + 1  # the smallest pot that used to overflow
    treasury.balance.value = UInt64(huge)
    treasury.configured.value = UInt64(1)

    allocated = int(treasury.distribute())
    assert allocated > 0
    # 50/30/20 of the pot, with the division remainder left behind.
    assert int(treasury.owed_to(arc4.Address(parties[0]))) == huge // 2
    assert allocated <= huge, "never allocate more than was snapshotted"


def test_the_split_is_unchanged_for_ordinary_amounts(
    context: AlgopyTestContext, treasury: Treasury, parties
) -> None:
    """Reordering the arithmetic must not change any answer that already worked."""
    treasury.balance.value = UInt64(1_000_000)
    treasury.configured.value = UInt64(1)
    treasury.distribute()
    assert int(treasury.owed_to(arc4.Address(parties[0]))) == 500_000
    assert int(treasury.owed_to(arc4.Address(parties[1]))) == 300_000
    assert int(treasury.owed_to(arc4.Address(parties[2]))) == 200_000


def test_configure_refuses_a_recipient_nobody_can_claim_as(
    context: AlgopyTestContext, parties
) -> None:
    """An address nobody can send from can never pull its allocation."""
    from algopy import Account

    treasury = Treasury()
    with pytest.raises(Exception, match="A recipient is required"):
        _configure(context, treasury, [(parties[0], 5_000), (Account(), 5_000)])


def test_configure_refuses_the_same_recipient_twice(
    context: AlgopyTestContext, parties
) -> None:
    """`claim` stops at the first match, so a duplicate's share is unreachable."""
    treasury = Treasury()
    with pytest.raises(Exception, match="appears twice"):
        _configure(context, treasury, [(parties[0], 5_000), (parties[0], 5_000)])
