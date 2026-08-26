"""Billing arithmetic, and the promise that one subscriber cannot wedge the rest.

The scheduled call must never fail, because a failing hook trips keeper backoff
and the schedule quietly ends. Everything that can fail was moved out of it, so
what is left to test here is that the split holds: `charge` stays inert, and
settlement gets the arithmetic right when a subscriber cannot pay in full.
"""

from collections.abc import Iterator

import pytest
from algopy import arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.subscription.contract import SUBSCRIBER_BOX_MBR, Subscription

PRICE = 50_000
MIN_ROUNDS = 30


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def provider(context: AlgopyTestContext):
    return context.any.account()


@pytest.fixture()
def keeper(context: AlgopyTestContext):
    """Stands in for the Arcron app. Its address is what `charge` checks."""
    return context.any.application()


@pytest.fixture()
def app(context: AlgopyTestContext, provider, keeper) -> Subscription:
    contract = Subscription()
    contract.create(arc4.Address(provider), arc4.UInt64(PRICE), arc4.UInt64(MIN_ROUNDS))
    contract.set_keeper(arc4.UInt64(keeper.id))
    return contract


def _subscribe(
    context: AlgopyTestContext,
    app: Subscription,
    who,
    amount: int,
    *,
    payment_fields: dict | None = None,
) -> None:
    payment = context.any.txn.payment(
        sender=who,
        receiver=context.ledger.get_app(app).address,
        amount=amount,
        **(payment_fields or {}),
    )
    # The payment is an argument, not a declared group member: the mock rejects
    # gtxns and active_txn_overrides together, and the contract's check is that
    # the deposit's sender matches the caller.
    with context.txn.create_group(active_txn_overrides={"sender": who}):
        app.subscribe(payment)


def _charge(context: AlgopyTestContext, app: Subscription, keeper) -> int:
    """Drive one period as the keeper app would, from its application account.

    Advances the round past the cadence first, which is what a keeper calling
    on its registered interval does.
    """
    context.ledger.patch_global_fields(
        round=app.last_charged_round.value + MIN_ROUNDS
    )
    with context.txn.create_group(active_txn_overrides={"sender": keeper.address}):
        return int(app.charge())


def test_charge_only_advances_the_period(context: AlgopyTestContext, app: Subscription, keeper) -> None:
    """The hook's whole job. No boxes, no money, and so nothing to reject."""
    assert _charge(context, app, keeper) == 1
    assert _charge(context, app, keeper) == 2
    assert app.period.value == 2
    assert app.provider_accrued.value == 0


def test_charge_refuses_anybody_but_the_keeper(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    stranger = context.any.account()
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(Exception, match="Only the keeper app"):
            app.charge()


def test_a_subscriber_is_not_billed_for_the_period_they_joined(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """Joining mid-period should not charge for the part already elapsed."""
    _charge(context, app, keeper)
    _charge(context, app, keeper)
    late = context.any.account()
    _subscribe(context, app, late, SUBSCRIBER_BOX_MBR + PRICE * 3)
    assert int(app.settle(arc4.Address(late))) == 0


def test_settlement_bills_every_elapsed_period(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE * 6)
    for _ in range(4):
        _charge(context, app, keeper)
    assert int(app.settle(arc4.Address(grace))) == 4
    assert app.provider_accrued.value == PRICE * 4


def test_a_subscriber_who_runs_out_pays_what_they_can_and_still_owes_the_rest(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """Partial payment must not forgive the periods it did not cover."""
    ada = context.any.account()
    _subscribe(context, app, ada, SUBSCRIBER_BOX_MBR + PRICE * 2)
    for _ in range(4):
        _charge(context, app, keeper)

    assert int(app.settle(arc4.Address(ada))) == 2
    assert app.provider_accrued.value == PRICE * 2
    # Two periods are still owed, so a later settle finds them rather than
    # treating the subscriber as up to date.
    assert int(app.settle(arc4.Address(ada))) == 0
    _charge(context, app, keeper)
    assert int(app.settle(arc4.Address(ada))) == 0


def test_settling_twice_in_one_period_bills_once(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE * 6)
    _charge(context, app, keeper)
    assert int(app.settle(arc4.Address(grace))) == 1
    assert int(app.settle(arc4.Address(grace))) == 0
    assert app.provider_accrued.value == PRICE


def test_a_lapsed_subscriber_does_not_stop_the_period_advancing(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """The property the whole design exists for."""
    ada = context.any.account()
    _subscribe(context, app, ada, SUBSCRIBER_BOX_MBR + PRICE)
    for _ in range(3):
        _charge(context, app, keeper)
    app.settle(arc4.Address(ada))
    # Ada is broke. The schedule is unaffected.
    assert _charge(context, app, keeper) == 4
    assert _charge(context, app, keeper) == 5


def test_a_first_deposit_must_cover_the_box(
    context: AlgopyTestContext, app: Subscription
) -> None:
    """Otherwise the app account subsidises the subscription."""
    who = context.any.account()
    with pytest.raises(Exception, match="First deposit must cover the box"):
        _subscribe(context, app, who, SUBSCRIBER_BOX_MBR - 1)


def test_topping_up_adds_to_the_balance_without_resetting_billing(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE)
    _charge(context, app, keeper)
    _subscribe(context, app, grace, PRICE * 2)
    # Three periods' worth of funding, one period elapsed.
    assert int(app.settle(arc4.Address(grace))) == 1
    assert app.provider_accrued.value == PRICE


def test_only_the_provider_may_claim(context: AlgopyTestContext, app: Subscription, keeper) -> None:
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE * 2)
    _charge(context, app, keeper)
    app.settle(arc4.Address(grace))
    stranger = context.any.account()
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(Exception, match="Only the provider may claim"):
            app.claim()


# --- regressions from the adversarial review ---------------------------


def test_billing_cannot_be_fast_forwarded(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """The finding that generalises beyond this contract.

    Registering an upkeep is permissionless, so anyone can point one at
    `charge` on the shortest interval the keeper allows and pay for it. The
    sender check proves the keeper app called, not that a period elapsed. A
    provider could otherwise fabricate periods for about two minimum fees each
    and settle a subscriber's whole balance to itself.
    """
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE * 10)
    _charge(context, app, keeper)
    assert app.period.value == 1

    # A second call in the same round, from the genuine keeper app. It must
    # not advance, and must not fail: failing would trip keeper backoff, and
    # under CATCH_UP a keeper draining a backlog replays immediately.
    with context.txn.create_group(active_txn_overrides={"sender": keeper.address}):
        assert int(app.charge()) == 1
    assert app.period.value == 1

    # One round short is still short.
    context.ledger.patch_global_fields(
        round=app.last_charged_round.value + MIN_ROUNDS - 1
    )
    with context.txn.create_group(active_txn_overrides={"sender": keeper.address}):
        assert int(app.charge()) == 1
    assert app.period.value == 1

    # On the cadence it was registered with, it goes through as before.
    assert _charge(context, app, keeper) == 2


def test_an_honest_keeper_is_never_refused(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """The hook must keep its never-fails property for a real schedule."""
    for expected in (1, 2, 3, 4, 5):
        assert _charge(context, app, keeper) == expected


def test_a_catch_up_replay_no_ops_rather_than_failing(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """The regression an assert here would have caused.

    Under CATCH_UP an upkeep that fell behind stays due, so a keeper replays
    it in the same round. If charge rejected that, the whole execute would
    revert, the bot would record a failure and back the upkeep off, and
    billing would stop entirely: the exact outcome the never-fail rule exists
    to prevent.
    """
    _charge(context, app, keeper)
    for _ in range(5):
        with context.txn.create_group(active_txn_overrides={"sender": keeper.address}):
            assert int(app.charge()) == 1  # no advance, no rejection
    assert app.period.value == 1
    # And once the cadence really has elapsed, it advances as normal.
    assert _charge(context, app, keeper) == 2


def test_a_lapsed_subscriber_can_still_leave(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """Requiring a full catch-up trapped the people most likely to leave.

    Ada cannot afford the periods she owes, so she could never satisfy
    `paid_through == period`, so her box could never be deleted and its
    minimum balance was stranded permanently.
    """
    ada = context.any.account()
    _subscribe(context, app, ada, SUBSCRIBER_BOX_MBR + PRICE * 2)
    for _ in range(4):
        _charge(context, app, keeper)

    with context.txn.create_group(active_txn_overrides={"sender": ada}):
        refund = int(app.withdraw())

    # She pays for what she could afford and takes back her box MBR.
    assert app.provider_accrued.value == PRICE * 2
    assert refund == SUBSCRIBER_BOX_MBR


def test_leaving_settles_what_is_owed_first(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """Withdrawing must not be a way to outrun the schedule."""
    grace = context.any.account()
    _subscribe(context, app, grace, SUBSCRIBER_BOX_MBR + PRICE * 10)
    for _ in range(3):
        _charge(context, app, keeper)

    with context.txn.create_group(active_txn_overrides={"sender": grace}):
        refund = int(app.withdraw())

    # Three periods billed, seven periods' worth plus the box returned.
    assert app.provider_accrued.value == PRICE * 3
    assert refund == PRICE * 7 + SUBSCRIBER_BOX_MBR


def test_partial_dust_is_returned_rather_than_captured(
    context: AlgopyTestContext, app: Subscription, keeper
) -> None:
    """A balance below one period is the subscriber's, not the provider's."""
    ada = context.any.account()
    _subscribe(context, app, ada, SUBSCRIBER_BOX_MBR + PRICE + 7_000)
    for _ in range(3):
        _charge(context, app, keeper)

    with context.txn.create_group(active_txn_overrides={"sender": ada}):
        refund = int(app.withdraw())

    assert app.provider_accrued.value == PRICE  # one whole period, no more
    assert refund == 7_000 + SUBSCRIBER_BOX_MBR


def test_a_large_price_does_not_brick_the_box(
    context: AlgopyTestContext, provider, keeper
) -> None:
    """`periods * price` used to be computed before capping by balance.

    A price near the uint64 ceiling overflowed on the second period, and the
    AVM panics rather than saturating, so settle and withdraw both failed
    forever and the box could never be deleted. On a contract with no delete
    path that strands the minimum balance permanently.
    """
    huge = 2**63
    contract = Subscription()
    contract.create(arc4.Address(provider), arc4.UInt64(huge), arc4.UInt64(MIN_ROUNDS))
    contract.set_keeper(arc4.UInt64(keeper.id))

    victim = context.any.account()
    _subscribe(context, contract, victim, SUBSCRIBER_BOX_MBR + 1)
    for _ in range(3):
        _charge(context, contract, keeper)

    # Nothing affordable, so nothing is billed, and neither call panics.
    assert int(contract.settle(arc4.Address(victim))) == 0
    with context.txn.create_group(active_txn_overrides={"sender": victim}):
        refund = int(contract.withdraw())
    assert refund == SUBSCRIBER_BOX_MBR + 1


def test_create_refuses_settings_that_cannot_be_undone(
    context: AlgopyTestContext, provider
) -> None:
    """Cheap validation belongs where the contract can never be patched."""
    with pytest.raises(Exception, match="Cadence too long"):
        Subscription().create(
            arc4.Address(provider), arc4.UInt64(PRICE), arc4.UInt64(2**63)
        )
    with pytest.raises(Exception, match="Provider required"):
        Subscription().create(
            arc4.Address(), arc4.UInt64(PRICE), arc4.UInt64(MIN_ROUNDS)
        )
    with pytest.raises(Exception, match="A period must span some rounds"):
        Subscription().create(
            arc4.Address(provider), arc4.UInt64(PRICE), arc4.UInt64(0)
        )


# --- #102: rekey and close must not reach an escrowing transaction ----
#
# Neither harms the contract; both harm only the sender who signed them. What
# this guards against is a malicious front end slipping either into a group a
# user signs without reading closely.

def test_subscribe_rejects_a_rekeyed_deposit(
    context: AlgopyTestContext, app: Subscription
) -> None:
    who = context.any.account()
    with pytest.raises(AssertionError, match="Deposit must not rekey"):
        _subscribe(
            context, app, who, SUBSCRIBER_BOX_MBR + 1,
            payment_fields={"rekey_to": context.any.account()},
        )


def test_subscribe_rejects_a_closing_deposit(
    context: AlgopyTestContext, app: Subscription
) -> None:
    who = context.any.account()
    with pytest.raises(AssertionError, match="Deposit must not close"):
        _subscribe(
            context, app, who, SUBSCRIBER_BOX_MBR + 1,
            payment_fields={"close_remainder_to": context.any.account()},
        )
