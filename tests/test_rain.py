"""The rain draw's accounting.

The scheduled call is the one that must never misbehave: Arcron calls `draw`
on every cadence whether or not there is anything to draw for, so the quiet
path has to be a clean no-op rather than a failure that would trip keeper
backoff. Most of what follows is about that, and about the money adding up.

Resolution needs an inner call to a beacon, which mocks record without
executing. That half lives in scripts/rain_demo.py on LocalNet.
"""

from collections.abc import Iterator

import pytest
from algopy import Asset, UInt64, arc4, op
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.rain.contract import (
    ALLOCATION_PREFIX,
    ASSET_OPT_IN_MBR,
    BEACON_WINDOW,
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
    """Open entry, ALGO prize: the original shape, still the default."""
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), UInt64(0))
    return contract


def _enter(
    context: AlgopyTestContext,
    rain: Rain,
    amount: int = TICKET_MBR,
    gate_asset=None,
) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address, amount=amount
    )
    return rain.enter(payment, gate_asset if gate_asset is not None else Asset(0))


def _deposit(context: AlgopyTestContext, rain: Rain, amount: int) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address, amount=amount
    )
    return rain.deposit(payment)


# --- configuration ----------------------------------------------------

def test_configure_is_once_and_creator_only(context: AlgopyTestContext) -> None:
    rain = Rain()
    rain.configure(UInt64(BEACON_APP), arc4.Address(), UInt64(0))
    assert rain.beacon_app.value == BEACON_APP
    with pytest.raises(AssertionError, match="Already configured"):
        rain.configure(UInt64(123), arc4.Address(), UInt64(0))


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
    # Arcron will call again on the next cadence, before anyone resolved.
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
        # Ungated draw, so the gate asset is not consulted.
        rain.claim(context.any.asset())


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


# --- gating and asset prizes ------------------------------------------


@pytest.fixture()
def collection(context: AlgopyTestContext):
    """A minting account, and two assets it created."""
    creator = context.any.account()
    return creator, [
        context.any.asset(creator=creator),
        context.any.asset(creator=creator),
    ]


@pytest.fixture()
def gated(context: AlgopyTestContext, collection) -> Rain:
    creator, _ = collection
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(creator), UInt64(0))
    return contract


def test_open_entry_ignores_whatever_asset_is_supplied(
    context: AlgopyTestContext, rain: Rain
) -> None:
    """An ungated draw must not start caring what you hold."""
    unrelated = context.any.asset()
    assert _enter(context, rain, gate_asset=unrelated) == 0


def test_a_ticket_is_worthless_once_the_token_has_moved_on(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    """The whole point of asking the gate a second time.

    A ticket is a box that never expires, and `enter` only ever asked whether
    the buyer held a collection token at that moment. Walking one token
    through ten accounts therefore bought ten permanent tickets, each of which
    diluted every honest holder, and `examples/community-rain.md` promised one
    entry per NFT held. Asking again at `claim` does not un-buy those tickets;
    it makes them uncollectable by anyone who no longer holds the token, which
    is nine of those ten accounts.
    """
    creator, assets = collection
    # Entered while holding the token, then passed it on: opted in, zero held.
    passed_through = context.any.account(opted_asset_balances={assets[0].id: UInt64(0)})
    # A won-but-unclaimed allocation. `resolve` cannot produce one here,
    # because the beacon call is recorded rather than executed under the
    # mocks, so the state is written directly.
    context.ledger.set_box(
        gated, ALLOCATION_PREFIX + passed_through.bytes, op.itob(UInt64(1_000))
    )

    with context.txn.create_group(active_txn_overrides={"sender": passed_through}):
        with pytest.raises(AssertionError, match="Hold a token from the collection"):
            gated.claim(assets[0])


def test_the_prize_asset_is_not_a_gate_token_at_claim_either(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    """`enter` refuses the prize as a ticket, so `claim` has to as well.

    A project usually mints its prize from the same account as its collection,
    which is exactly the case the gate is checked against. Without this, a
    past winner holding nothing but prize tokens satisfies the claim gate
    while holding no collection token at all, which is a permanent exemption
    for the one group the rule is aimed at.
    """
    creator, assets = collection
    prize = context.any.asset(creator=creator)
    contract = Rain()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract.configure(UInt64(BEACON_APP), arc4.Address(creator), prize.id)

    holder = context.any.account(opted_asset_balances={prize.id: UInt64(5)})
    context.ledger.set_box(contract, ALLOCATION_PREFIX + holder.bytes, op.itob(UInt64(1_000)))

    with context.txn.create_group(active_txn_overrides={"sender": holder}):
        with pytest.raises(AssertionError, match="The prize is not a ticket"):
            contract.claim(prize)


def test_a_winner_still_holding_the_token_can_collect(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    """The other half: the check must not lock out an honest winner.

    A gate that refuses everybody is not a gate, and this is the case that
    proves the refusal above is about the token having moved rather than about
    the check being unpassable.
    """
    creator, assets = collection
    winner = context.any.account(opted_asset_balances={assets[0].id: UInt64(1)})
    context.ledger.set_box(
        gated, ALLOCATION_PREFIX + winner.bytes, op.itob(UInt64(1_000))
    )

    with context.txn.create_group(active_txn_overrides={"sender": winner}):
        assert gated.claim(assets[0]) == 1_000


def test_a_holder_of_the_collection_may_enter(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    creator, assets = collection
    holder = context.any.account(opted_asset_balances={assets[0].id: UInt64(1)})
    payment = context.any.txn.payment(
        sender=holder, receiver=context.ledger.get_app(gated).address, amount=TICKET_MBR
    )
    with context.txn.create_group(active_txn_overrides={"sender": holder}):
        assert gated.enter(payment, assets[0]) == 0


def test_any_asset_from_the_collection_works_not_just_one(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    """A collection is many assets by one creator, which is the whole point."""
    creator, assets = collection
    holder = context.any.account(opted_asset_balances={assets[1].id: UInt64(1)})
    payment = context.any.txn.payment(
        sender=holder, receiver=context.ledger.get_app(gated).address, amount=TICKET_MBR
    )
    with context.txn.create_group(active_txn_overrides={"sender": holder}):
        assert gated.enter(payment, assets[1]) == 0


def test_an_asset_from_another_creator_is_refused(
    context: AlgopyTestContext, gated: Rain
) -> None:
    """Holding *an* NFT is not holding one of these."""
    impostor = context.any.asset()
    outsider = context.any.account(opted_asset_balances={impostor.id: UInt64(1)})
    payment = context.any.txn.payment(
        sender=outsider, receiver=context.ledger.get_app(gated).address, amount=TICKET_MBR
    )
    with context.txn.create_group(active_txn_overrides={"sender": outsider}):
        with pytest.raises(AssertionError, match="not from the collection"):
            gated.enter(payment, impostor)


def test_not_holding_the_asset_is_refused(
    context: AlgopyTestContext, gated: Rain, collection
) -> None:
    """Naming a collection asset you do not hold must not get you a ticket."""
    creator, assets = collection
    stranger = context.any.account(opted_asset_balances={assets[0].id: UInt64(0)})
    payment = context.any.txn.payment(
        sender=stranger, receiver=context.ledger.get_app(gated).address, amount=TICKET_MBR
    )
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(AssertionError, match="Hold a token from the collection"):
            gated.enter(payment, assets[0])


def test_an_algo_draw_refuses_asset_deposits_and_the_reverse(
    context: AlgopyTestContext, rain: Rain
) -> None:
    """The pot is denominated one way or the other, never both."""
    asset = context.any.asset()
    transfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=context.ledger.get_app(rain).address,
        asset_amount=10,
    )
    with pytest.raises(AssertionError, match="pays ALGO; use deposit"):
        rain.deposit_asset(transfer)


def test_an_asset_draw_refuses_algo_deposits(context: AlgopyTestContext) -> None:
    asset = context.any.asset()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), asset.id)
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=1_000
    )
    with pytest.raises(AssertionError, match="pays an asset; use deposit_asset"):
        contract.deposit(payment)


def test_an_asset_pot_grows_by_the_transfer(context: AlgopyTestContext) -> None:
    asset = context.any.asset()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), asset.id)
    transfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=context.ledger.get_app(contract).address,
        asset_amount=250,
    )
    assert contract.deposit_asset(transfer) == 250
    assert contract.pot.value == 250


def test_the_wrong_asset_is_refused(context: AlgopyTestContext) -> None:
    prize, other = context.any.asset(), context.any.asset()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), prize.id)
    transfer = context.any.txn.asset_transfer(
        xfer_asset=other,
        asset_receiver=context.ledger.get_app(contract).address,
        asset_amount=10,
    )
    with pytest.raises(AssertionError, match="Wrong asset"):
        contract.deposit_asset(transfer)


@pytest.fixture()
def asset_rain_pair(context: AlgopyTestContext):
    """A configured asset draw, and the asset it pays in."""
    prize = context.any.asset()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), prize.id)
    return contract, prize


# --- regressions from the adversarial review ---------------------------


def test_configure_is_refused_once_a_pot_exists(context: AlgopyTestContext) -> None:
    """Nobody may repoint the denomination under people who already funded it."""
    contract = Rain()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract.configure(UInt64(BEACON_APP), arc4.Address(), UInt64(0))
    _deposit(context, contract, 100_000)
    contract.beacon_app.value = UInt64(0)  # pretend the one-shot latch is open
    with pytest.raises(AssertionError, match="before the pot is funded"):
        contract.configure(UInt64(BEACON_APP), arc4.Address(), UInt64(999))


def test_entering_and_depositing_need_configuration_first(
    context: AlgopyTestContext,
) -> None:
    """Otherwise the pot's unit is decided after money is already in it."""
    contract = Rain()
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    with pytest.raises(AssertionError, match="Not configured"):
        _enter(context, contract)
    with pytest.raises(AssertionError, match="Not configured"):
        _deposit(context, contract, 100_000)


def test_the_prize_asset_cannot_buy_a_ticket(context: AlgopyTestContext) -> None:
    """A project mints its collection and its prize from the same account."""
    artist = context.any.account()
    nft = context.any.asset(creator=artist)
    prize = context.any.asset(creator=artist)
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(artist), prize.id)

    holder = context.any.account(opted_asset_balances={prize.id: UInt64(1)})
    payment = context.any.txn.payment(
        sender=holder, receiver=context.ledger.get_app(contract).address, amount=TICKET_MBR
    )
    with context.txn.create_group(active_txn_overrides={"sender": holder}):
        with pytest.raises(AssertionError, match="The prize is not a ticket"):
            contract.enter(payment, prize)

    # The collection itself still works.
    nft_holder = context.any.account(opted_asset_balances={nft.id: UInt64(1)})
    nft_payment = context.any.txn.payment(
        sender=nft_holder,
        receiver=context.ledger.get_app(contract).address,
        amount=TICKET_MBR,
    )
    with context.txn.create_group(active_txn_overrides={"sender": nft_holder}):
        assert contract.enter(nft_payment, nft) == 0


def test_a_draw_past_the_beacon_window_can_be_abandoned(
    context: AlgopyTestContext, rain: Rain
) -> None:
    """Otherwise one unresolved draw locks the pot on an immutable contract."""
    _enter(context, rain)
    _deposit(context, rain, 100_000)
    rain.draw()
    assert rain.draw_open.value == 1
    commit = rain.commit_round.value

    # Still inside the window: resolving is the right answer, not abandoning.
    context.ledger.patch_global_fields(round=commit + 1)
    with pytest.raises(AssertionError, match="beacon can still answer"):
        rain.abandon()

    context.ledger.patch_global_fields(round=commit + BEACON_WINDOW + 1)
    with pytest.raises(AssertionError, match="window has closed"):
        rain.resolve()
    # The prize plus the reservation `draw` took for a box that never existed.
    returned = rain.abandon()
    assert returned == 100_000
    assert rain.draw_open.value == 0
    assert rain.pot.value == 100_000
    # And the pot can be drawn again rather than being locked forever.
    assert rain.draw() > 0


def test_a_rugable_prize_asset_is_refused(context: AlgopyTestContext) -> None:
    """An issuer who kept clawback can empty the pot whenever they like.

    `pot` would go on claiming the tokens are there, every claim would fail on
    insufficient balance, and none of it is fixable on a contract with no
    update path. Freeze is the same story with the pot stranded rather than
    stolen, and a manager can set either back, so all three are refused.
    """
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))

    def app_for(asset) -> Rain:
        contract = Rain()
        contract.configure(UInt64(BEACON_APP), arc4.Address(), asset.id)
        return contract

    for field, message in (
        ("clawback", "clawback address"),
        ("freeze", "freeze address"),
        ("manager", "manager address"),
    ):
        rugger = context.any.account()
        asset = context.any.asset(**{field: rugger})
        contract = app_for(asset)
        payment = context.any.txn.payment(
            receiver=context.ledger.get_app(contract).address, amount=ASSET_OPT_IN_MBR
        )
        with pytest.raises(Exception, match=message):
            contract.opt_in_prize_asset(asset, payment)


def test_a_prize_asset_frozen_by_default_is_refused(context: AlgopyTestContext) -> None:
    """A frozen holding can receive tokens but can never send them.

    `default_frozen` is fixed when the asset is created and no address can
    change it afterwards, so an asset that starts frozen with its freeze
    address already renounced passes every other check here and still traps
    the prize: the pot opts in, accepts the tokens, and can never pay a
    winner. That combination looks like the safest possible asset from the
    outside, having renounced clawback, freeze and manager, which is exactly
    what makes it worth refusing on its own.
    """
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    asset = context.any.asset(default_frozen=True)
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), asset.id)
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=ASSET_OPT_IN_MBR
    )
    with pytest.raises(Exception, match="frozen by default"):
        contract.opt_in_prize_asset(asset, payment)


def test_a_clean_prize_asset_is_accepted(context: AlgopyTestContext) -> None:
    """The check must not reject an ordinary immutable token."""
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    clean = context.any.asset()
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), clean.id)
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=ASSET_OPT_IN_MBR
    )
    assert int(contract.opt_in_prize_asset(clean, payment)) == clean.id


def test_the_opt_in_refuses_a_different_asset(context: AlgopyTestContext) -> None:
    """Otherwise a clean asset could be shown to pass the check for a dirty one."""
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    prize, decoy = context.any.asset(), context.any.asset()
    contract = Rain()
    contract.configure(UInt64(BEACON_APP), arc4.Address(), prize.id)
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=ASSET_OPT_IN_MBR
    )
    with pytest.raises(Exception, match="Wrong asset"):
        contract.opt_in_prize_asset(decoy, payment)


def test_an_asset_draw_will_not_open_without_algo_for_the_winners_box(
    context: AlgopyTestContext, asset_rain_pair
) -> None:
    """Invariant 12, which had no test.

    An asset draw reserves nothing from the pot, so the ALGO for the winner's
    allocation box has to already be in the app account. Without this check
    `resolve` would fail on minimum balance with the draw open, and a draw
    that cannot be resolved can never be reopened.
    """
    contract, prize = asset_rain_pair
    _enter(context, contract)
    contract.deposit_asset(
        context.any.txn.asset_transfer(
            xfer_asset=prize,
            asset_receiver=context.ledger.get_app(contract).address,
            asset_amount=500,
        )
    )

    app = context.ledger.get_app(contract)
    # Spendable ALGO below one allocation box: the draw declines rather than
    # opening one it cannot resolve.
    context.ledger.update_account(app.address, balance=UInt64(200_000), min_balance=UInt64(190_000))
    assert int(contract.draw()) == 0
    assert contract.draw_open.value == 0

    # Funded, it opens.
    context.ledger.update_account(app.address, balance=UInt64(500_000), min_balance=UInt64(100_000))
    assert int(contract.draw()) > 0


def test_tick_with_refuses_an_increment_that_could_wedge_the_counter(
    context: AlgopyTestContext,
) -> None:
    """The bound that stops one call making every later tick overflow."""
    from smart_contracts.pulse.contract import MAX_BEATS_PER_TICK, Pulse

    pulse = Pulse()
    assert int(pulse.tick_with(UInt64(MAX_BEATS_PER_TICK), arc4.String("ok"))) == MAX_BEATS_PER_TICK
    with pytest.raises(Exception, match="Too many beats"):
        pulse.tick_with(UInt64(MAX_BEATS_PER_TICK + 1), arc4.String("no"))
