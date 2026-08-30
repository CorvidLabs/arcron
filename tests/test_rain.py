"""The rain hub's accounting.

Anyone creates a rain. Arcron calls `draw` on a cadence; too soon, an empty
hub, or a pot that cannot cover a share is a clean no-op, never a failure
that would trip keeper backoff.
"""

from collections.abc import Iterator

import pytest
from algopy import Asset, Global, UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.rain.contract import (
    APP_BASE_MBR,
    COMMIT_DELAY,
    INDEX_MBR,
    MIN_INTERVAL_ROUNDS,
    ONE,
    RAIN_BOX_MBR,
    SEED_WINDOW,
    SPLIT,
    TICKET_MBR,
    TICKET_PREFIX,
    WAVE,
    Label,
    Rain,
)

DRIP = 100_000
INTERVAL = 10
START_ROUND = 1_000


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


def _label(text: str = "rain") -> Label:
    raw = text.encode()[:32].ljust(32, b"\x00")
    return Label.from_bytes(raw)


def _bootstrap(context: AlgopyTestContext, contract: Rain, amount: int = APP_BASE_MBR) -> None:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address,
        amount=amount,
    )
    contract.bootstrap(payment)


def _create(
    context: AlgopyTestContext,
    contract: Rain,
    *,
    label: str = "rain",
    gate_creator=None,
    prize_asset: int = 0,
    drip: int = DRIP,
    interval: int = INTERVAL,
    mode: int = SPLIT,
    wave_cap: int = 0,
    amount: int = RAIN_BOX_MBR,
) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address,
        amount=amount,
    )
    gate = gate_creator if gate_creator is not None else arc4.Address()
    return contract.create_rain(
        payment,
        _label(label),
        gate,
        UInt64(prize_asset),
        UInt64(drip),
        UInt64(interval),
        UInt64(mode),
        UInt64(wave_cap),
    )


def _enter(
    context: AlgopyTestContext,
    rain: Rain,
    rain_id: int,
    *,
    amount: int | None = None,
    gate_asset=None,
    sender=None,
    mode: int = SPLIT,
) -> int:
    if amount is None:
        amount = TICKET_MBR + INDEX_MBR if mode == ONE else TICKET_MBR
    fields = {}
    if sender is not None:
        fields["sender"] = sender
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address,
        amount=amount,
        **fields,
    )
    asset = gate_asset if gate_asset is not None else Asset(0)
    if sender is None:
        return rain.enter(payment, rain_id, asset)
    with context.txn.create_group(active_txn_overrides={"sender": sender}):
        return rain.enter(payment, rain_id, asset)


def _deposit(context: AlgopyTestContext, rain: Rain, rain_id: int, amount: int) -> int:
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(rain).address,
        amount=amount,
    )
    return rain.deposit(payment, rain_id)


def _advance(context: AlgopyTestContext, rounds: int) -> None:
    context.ledger.patch_global_fields(round=UInt64(int(Global.round) + rounds))


def _opt_hub_into(context: AlgopyTestContext, hub: Rain, asset) -> None:
    context.ledger.update_asset_holdings(
        asset, context.ledger.get_app(hub).address, balance=0
    )


@pytest.fixture()
def hub(context: AlgopyTestContext) -> Rain:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    _bootstrap(context, contract)
    return contract


@pytest.fixture()
def split(context: AlgopyTestContext, hub: Rain) -> tuple[Rain, int]:
    rain_id = _create(context, hub, label="split")
    return hub, rain_id


def test_bootstrap_is_once_and_creator_only(context: AlgopyTestContext) -> None:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    rain = Rain()
    _bootstrap(context, rain)
    assert rain.bootstrapped.value == 1
    with pytest.raises(AssertionError, match="Already bootstrapped"):
        _bootstrap(context, rain)


def test_bootstrap_refuses_an_mbr_payment_below_the_app_floor(
    context: AlgopyTestContext,
) -> None:
    contract = Rain()
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=APP_BASE_MBR - 1
    )
    with pytest.raises(AssertionError, match="MBR payment too small"):
        contract.bootstrap(payment)


def test_create_needs_bootstrap(context: AlgopyTestContext) -> None:
    context.ledger.patch_global_fields(round=UInt64(START_ROUND))
    contract = Rain()
    with pytest.raises(AssertionError, match="Not bootstrapped"):
        _create(context, contract)


def test_create_refuses_a_zero_drip(context: AlgopyTestContext, hub: Rain) -> None:
    with pytest.raises(AssertionError, match="Drip must be positive"):
        _create(context, hub, drip=0)


def test_create_assigns_sequential_ids(context: AlgopyTestContext, hub: Rain) -> None:
    assert _create(context, hub, label="one") == 1
    assert _create(context, hub, label="two") == 2
    assert hub.next_rain_id.value == 2


def test_a_ticket_must_pay_its_own_box(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    with pytest.raises(AssertionError, match="MBR payment too small"):
        _enter(context, rain, rain_id, amount=TICKET_MBR - 1)


def test_one_ticket_per_account(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    assert _enter(context, rain, rain_id) == 1
    with pytest.raises(AssertionError, match="Already entered"):
        _enter(context, rain, rain_id)
    rec = rain.rain_of(rain_id)
    assert rec.tickets.native == 1


def test_deposits_accumulate(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    assert _deposit(context, rain, rain_id, 500_000) == 500_000
    assert _deposit(context, rain, rain_id, 250_000) == 750_000


def test_a_deposit_must_be_positive(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    with pytest.raises(AssertionError, match="Amount must be positive"):
        _deposit(context, rain, rain_id, 0)


def test_draw_is_a_no_op_with_no_tickets(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    _deposit(context, rain, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    assert rain.draw() == 0
    assert rain.rain_of(rain_id).pot.native == 1_000_000


def test_draw_is_a_no_op_with_an_empty_pot(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    _enter(context, rain, rain_id)
    _advance(context, INTERVAL)
    assert rain.draw() == 0


def test_draw_is_a_no_op_when_the_pot_cannot_cover_a_share_each(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    _enter(context, rain, rain_id)
    _deposit(context, rain, rain_id, DRIP - 1)
    _advance(context, INTERVAL)
    assert rain.draw() == 0
    assert rain.rain_of(rain_id).pot.native == DRIP - 1


def test_draw_splits_the_drip_across_every_ticket(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    _enter(context, rain, rain_id)
    other = context.any.account()
    _enter(context, rain, rain_id, sender=other)
    _deposit(context, rain, rain_id, 1_000_000)
    _advance(context, INTERVAL)

    assert rain.draw() == 1
    rec = rain.rain_of(rain_id)
    assert rec.cumulative.native == 50_000
    assert rec.pot.native == 900_000
    assert rain.allocation_of(rain_id, arc4.Address(other)) == 50_000


def test_draw_leaves_the_division_remainder_in_the_pot(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    rain.set_rain(rain_id, UInt64(100_000), UInt64(INTERVAL))
    for _ in range(3):
        _enter(context, rain, rain_id, sender=context.any.account())
    _deposit(context, rain, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    rain.draw()
    rec = rain.rain_of(rain_id)
    assert rec.cumulative.native == 33_333
    assert rec.pot.native == 1_000_000 - 99_999


def test_draw_no_ops_until_the_interval_has_passed(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    _enter(context, rain, rain_id)
    _deposit(context, rain, rain_id, 1_000_000)
    assert rain.draw() == 0
    _advance(context, INTERVAL - 1)
    assert rain.draw() == 0
    _advance(context, 1)
    assert rain.draw() == 1
    rec = rain.rain_of(rain_id)
    assert rec.cumulative.native == DRIP
    assert rec.pot.native == 1_000_000 - DRIP


def test_a_late_arriver_does_not_collect_past_rain(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    _enter(context, rain, rain_id)
    _deposit(context, rain, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    rain.draw()
    late = context.any.account()
    _enter(context, rain, rain_id, sender=late)
    assert rain.allocation_of(rain_id, arc4.Address(late)) == 0


def test_claiming_with_no_ticket_is_zero(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    assert rain.claim(rain_id, Asset(0)) == 0


def test_claim_pays_the_owed_share_and_clears_it(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    holder = context.default_sender
    _enter(context, rain, rain_id)
    _deposit(context, rain, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    rain.draw()
    assert rain.allocation_of(rain_id, arc4.Address(holder)) == DRIP
    assert rain.claim(rain_id, Asset(0)) == DRIP
    assert rain.allocation_of(rain_id, arc4.Address(holder)) == 0
    assert rain.claim(rain_id, Asset(0)) == 0


def test_open_entry_ignores_whatever_asset_is_supplied(
    context: AlgopyTestContext, split: tuple[Rain, int]
) -> None:
    rain, rain_id = split
    unrelated = context.any.asset()
    assert _enter(context, rain, rain_id, gate_asset=unrelated) == 1


@pytest.fixture()
def collection(context: AlgopyTestContext):
    creator = context.any.account()
    return creator, [
        context.any.asset(creator=creator),
        context.any.asset(creator=creator),
    ]


def test_a_holder_of_the_collection_may_enter(
    context: AlgopyTestContext, hub: Rain, collection
) -> None:
    creator, assets = collection
    rain_id = _create(context, hub, gate_creator=arc4.Address(creator), label="gated")
    holder = context.any.account(opted_asset_balances={assets[0].id: UInt64(1)})
    assert _enter(context, hub, rain_id, sender=holder, gate_asset=assets[0]) == 1


def test_any_asset_from_the_collection_works_not_just_one(
    context: AlgopyTestContext, hub: Rain, collection
) -> None:
    creator, assets = collection
    rain_id = _create(context, hub, gate_creator=arc4.Address(creator), label="gated")
    holder = context.any.account(opted_asset_balances={assets[1].id: UInt64(1)})
    assert _enter(context, hub, rain_id, sender=holder, gate_asset=assets[1]) == 1


def test_an_asset_from_another_creator_is_refused(
    context: AlgopyTestContext, hub: Rain, collection
) -> None:
    creator, _ = collection
    rain_id = _create(context, hub, gate_creator=arc4.Address(creator), label="gated")
    impostor = context.any.asset()
    outsider = context.any.account(opted_asset_balances={impostor.id: UInt64(1)})
    with pytest.raises(AssertionError, match="not from the collection"):
        _enter(context, hub, rain_id, sender=outsider, gate_asset=impostor)


def test_not_holding_the_asset_is_refused(
    context: AlgopyTestContext, hub: Rain, collection
) -> None:
    creator, assets = collection
    rain_id = _create(context, hub, gate_creator=arc4.Address(creator), label="gated")
    stranger = context.any.account(opted_asset_balances={assets[0].id: UInt64(0)})
    with pytest.raises(AssertionError, match="Hold a token from the collection"):
        _enter(context, hub, rain_id, sender=stranger, gate_asset=assets[0])


def test_claim_on_a_gated_rain_still_needs_the_token(
    context: AlgopyTestContext, hub: Rain, collection
) -> None:
    creator, assets = collection
    rain_id = _create(context, hub, gate_creator=arc4.Address(creator), label="gated")
    holder = context.any.account(opted_asset_balances={assets[0].id: UInt64(1)})
    _enter(context, hub, rain_id, sender=holder, gate_asset=assets[0])
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()

    gone = context.any.account(opted_asset_balances={assets[0].id: UInt64(0)})
    from algopy import op as algopy_op

    key = TICKET_PREFIX + algopy_op.itob(rain_id) + gone.bytes
    # credit 0, wave 0, settled 0
    context.ledger.set_box(hub, key, (0).to_bytes(8, "big") * 3)
    rec = hub.rain_of(rain_id)
    with context.txn.create_group(active_txn_overrides={"sender": gone}):
        with pytest.raises(AssertionError, match="Hold a token from the collection"):
            hub.claim(rain_id, assets[0])

    still = context.any.account(opted_asset_balances={assets[0].id: UInt64(1)})
    still_key = TICKET_PREFIX + algopy_op.itob(rain_id) + still.bytes
    context.ledger.set_box(hub, still_key, (0).to_bytes(8, "big") * 3)
    with context.txn.create_group(active_txn_overrides={"sender": still}):
        assert hub.claim(rain_id, assets[0]) == rec.cumulative.native


def test_an_algo_rain_refuses_asset_deposits(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    asset = context.any.asset()
    transfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=context.ledger.get_app(rain).address,
        asset_amount=10,
    )
    with pytest.raises(AssertionError, match="pays ALGO; use deposit"):
        rain.deposit_asset(transfer, rain_id)


def test_an_asset_rain_refuses_algo_deposits(context: AlgopyTestContext, hub: Rain) -> None:
    asset = context.any.asset()
    _opt_hub_into(context, hub, asset)
    rain_id = _create(context, hub, prize_asset=int(asset.id), label="asa")
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(hub).address, amount=1_000
    )
    with pytest.raises(AssertionError, match="pays an asset; use deposit_asset"):
        hub.deposit(payment, rain_id)


def test_an_asset_pot_grows_by_the_transfer(context: AlgopyTestContext, hub: Rain) -> None:
    asset = context.any.asset()
    _opt_hub_into(context, hub, asset)
    rain_id = _create(context, hub, prize_asset=int(asset.id), label="asa")
    transfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=context.ledger.get_app(hub).address,
        asset_amount=250,
    )
    assert hub.deposit_asset(transfer, rain_id) == 250
    assert hub.rain_of(rain_id).pot.native == 250


def test_the_prize_asset_cannot_buy_a_ticket(context: AlgopyTestContext, hub: Rain) -> None:
    artist = context.any.account()
    nft = context.any.asset(creator=artist)
    prize = context.any.asset(creator=artist)
    _opt_hub_into(context, hub, prize)
    rain_id = _create(
        context, hub, gate_creator=arc4.Address(artist), prize_asset=int(prize.id), label="mix"
    )

    holder = context.any.account(opted_asset_balances={prize.id: UInt64(1)})
    with pytest.raises(AssertionError, match="The prize is not a ticket"):
        _enter(context, hub, rain_id, sender=holder, gate_asset=prize)

    nft_holder = context.any.account(opted_asset_balances={nft.id: UInt64(1)})
    assert _enter(context, hub, rain_id, sender=nft_holder, gate_asset=nft) == 1


def test_set_rain_is_the_rain_creator_only(context: AlgopyTestContext, split: tuple[Rain, int]) -> None:
    rain, rain_id = split
    rain.set_rain(rain_id, UInt64(50_000), UInt64(20))
    rec = rain.rain_of(rain_id)
    assert rec.drip.native == 50_000
    assert rec.interval_rounds.native == 20
    other = context.any.account()
    with context.txn.create_group(active_txn_overrides={"sender": other}):
        with pytest.raises(AssertionError, match="Only the rain's creator"):
            rain.set_rain(rain_id, UInt64(1), UInt64(MIN_INTERVAL_ROUNDS))


def test_wave_rains_on_the_people_who_checked_in(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=WAVE, wave_cap=2, label="gm")
    a = context.default_sender
    b = context.any.account()
    c = context.any.account()
    _enter(context, hub, rain_id, sender=a)
    _enter(context, hub, rain_id, sender=b)
    _enter(context, hub, rain_id, sender=c)
    # Cap is 2; the third enter still gets a ticket but not a seat this drop
    # if the first two already filled it. Enter auto-GMs, so a and b took the
    # seats; c is in for later.
    rec = hub.rain_of(rain_id)
    assert rec.wave_count.native == 2
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    assert hub.draw() == 1
    rec = hub.rain_of(rain_id)
    assert rec.last_share.native == DRIP // 2
    assert rec.wave_count.native == 0
    assert hub.allocation_of(rain_id, arc4.Address(a)) == DRIP // 2
    assert hub.allocation_of(rain_id, arc4.Address(b)) == DRIP // 2
    assert hub.allocation_of(rain_id, arc4.Address(c)) == 0
    assert hub.claim(rain_id, Asset(0)) == DRIP // 2


def test_wave_unclaimed_share_returns_to_the_pot(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=WAVE, wave_cap=10, label="snooze")
    _enter(context, hub, rain_id)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()
    rec = hub.rain_of(rain_id)
    assert rec.pot.native == 1_000_000 - DRIP
    assert rec.wave_unclaimed.native == 1
    # Nobody claimed. Next fire with no GMs puts it back.
    _advance(context, INTERVAL)
    hub.draw()
    rec = hub.rain_of(rain_id)
    assert rec.pot.native == 1_000_000
    assert rec.wave_unclaimed.native == 0


def test_gm_after_a_drop_settles_the_last_share(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=WAVE, wave_cap=10, label="again")
    _enter(context, hub, rain_id)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()
    # Checking in for the next drop also banks the last one.
    assert hub.gm(rain_id, Asset(0)) == 2
    assert hub.allocation_of(rain_id, arc4.Address(context.default_sender)) == DRIP
    assert hub.claim(rain_id, Asset(0)) == DRIP


def test_one_locks_the_drip_and_resolve_credits_the_seed_winner(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=ONE, label="lotto")
    a = context.default_sender
    b = context.any.account()
    _enter(context, hub, rain_id, sender=a, mode=ONE)
    _enter(context, hub, rain_id, sender=b, mode=ONE)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    fire_round = int(Global.round)
    assert hub.draw() == 1
    rec = hub.rain_of(rain_id)
    assert rec.prize_locked.native == DRIP
    assert rec.pot.native == 1_000_000 - DRIP
    commit = int(rec.commit_round.native)
    assert commit == fire_round + COMMIT_DELAY

    with pytest.raises(AssertionError, match="Too early"):
        hub.resolve(rain_id)

    context.ledger.set_block(index=commit, seed=1, timestamp=1)
    context.ledger.patch_global_fields(round=UInt64(commit + 1))
    # seed 1 % 2 tickets = index 1 → second enterer (b)
    assert hub.resolve(rain_id) == 1
    assert hub.allocation_of(rain_id, arc4.Address(a)) == 0
    assert hub.allocation_of(rain_id, arc4.Address(b)) == DRIP
    with context.txn.create_group(active_txn_overrides={"sender": b}):
        assert hub.claim(rain_id, Asset(0)) == DRIP


def test_abandon_returns_the_lock_after_the_window(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=ONE, label="stale")
    _enter(context, hub, rain_id, mode=ONE)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()
    rec = hub.rain_of(rain_id)
    commit = int(rec.commit_round.native)
    context.ledger.patch_global_fields(round=UInt64(commit + SEED_WINDOW))
    with pytest.raises(AssertionError, match="Window still open"):
        hub.abandon(rain_id)
    context.ledger.patch_global_fields(round=UInt64(commit + SEED_WINDOW + 1))
    assert hub.abandon(rain_id) == 1_000_000
    rec = hub.rain_of(rain_id)
    assert rec.prize_locked.native == 0
    assert rec.pot.native == 1_000_000


def test_enter_is_refused_while_a_one_draw_is_open(
    context: AlgopyTestContext, hub: Rain
) -> None:
    rain_id = _create(context, hub, mode=ONE, label="aim")
    _enter(context, hub, rain_id, mode=ONE)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    assert hub.draw() == 1
    # The committed round has passed, so its seed is public. A ticket taken
    # now could be one of a batch sized to make `seed % tickets` land on it.
    _advance(context, COMMIT_DELAY + 1)
    late = context.any.account()
    with pytest.raises(AssertionError, match="Draw open"):
        _enter(context, hub, rain_id, sender=late, mode=ONE)
    assert hub.rain_of(rain_id).tickets.native == 1


def test_resolve_reopens_entry(context: AlgopyTestContext, hub: Rain) -> None:
    rain_id = _create(context, hub, mode=ONE, label="reopen")
    _enter(context, hub, rain_id, mode=ONE)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()
    late = context.any.account()
    with pytest.raises(AssertionError, match="Draw open"):
        _enter(context, hub, rain_id, sender=late, mode=ONE)
    commit = int(hub.rain_of(rain_id).commit_round.native)
    context.ledger.set_block(index=commit, seed=1, timestamp=1)
    context.ledger.patch_global_fields(round=UInt64(commit + 1))
    hub.resolve(rain_id)
    assert _enter(context, hub, rain_id, sender=late, mode=ONE) == 2


def test_abandon_reopens_entry(context: AlgopyTestContext, hub: Rain) -> None:
    rain_id = _create(context, hub, mode=ONE, label="stuck")
    _enter(context, hub, rain_id, mode=ONE)
    _deposit(context, hub, rain_id, 1_000_000)
    _advance(context, INTERVAL)
    hub.draw()
    # Nobody resolves. Entry stays shut for the window, and the permissionless
    # `abandon` is what reopens it, so a dead draw cannot wedge a rain.
    late = context.any.account()
    with pytest.raises(AssertionError, match="Draw open"):
        _enter(context, hub, rain_id, sender=late, mode=ONE)
    commit = int(hub.rain_of(rain_id).commit_round.native)
    context.ledger.patch_global_fields(round=UInt64(commit + SEED_WINDOW + 1))
    hub.abandon(rain_id)
    assert _enter(context, hub, rain_id, sender=late, mode=ONE) == 2


def test_two_rains_can_fire_in_one_draw(context: AlgopyTestContext, hub: Rain) -> None:
    first = _create(context, hub, label="a")
    second = _create(context, hub, label="b")
    _enter(context, hub, first)
    _enter(context, hub, second, sender=context.any.account())
    _deposit(context, hub, first, 1_000_000)
    _deposit(context, hub, second, 1_000_000)
    _advance(context, INTERVAL)
    assert hub.draw() == 2
    assert hub.rain_of(first).draw_id.native == 1
    assert hub.rain_of(second).draw_id.native == 1


def test_draw_on_an_empty_hub_is_zero(context: AlgopyTestContext, hub: Rain) -> None:
    assert hub.draw() == 0


def test_wave_cap_must_be_positive(context: AlgopyTestContext, hub: Rain) -> None:
    with pytest.raises(AssertionError, match="Wave cap must be positive"):
        _create(context, hub, mode=WAVE, wave_cap=0)


def test_split_refuses_a_wave_cap(context: AlgopyTestContext, hub: Rain) -> None:
    with pytest.raises(AssertionError, match="Wave cap is for WAVE rains"):
        _create(context, hub, mode=SPLIT, wave_cap=10)
