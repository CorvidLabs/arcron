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
    contract.create(arc4.Address(provider), arc4.UInt64(PRICE))
    contract.set_keeper(arc4.UInt64(keeper.id))
    return contract


def _subscribe(context: AlgopyTestContext, app: Subscription, who, amount: int) -> None:
    payment = context.any.txn.payment(
        sender=who, receiver=context.ledger.get_app(app).address, amount=amount
    )
    # The payment is an argument, not a declared group member: the mock rejects
    # gtxns and active_txn_overrides together, and the contract's check is that
    # the deposit's sender matches the caller.
    with context.txn.create_group(active_txn_overrides={"sender": who}):
        app.subscribe(payment)


def _charge(context: AlgopyTestContext, app: Subscription, keeper) -> int:
    """Drive one period as the keeper app would, from its application account."""
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
