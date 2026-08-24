import hashlib
from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.keeper.contract import (
    BOX_MBR_FIXED,
    MAX_CALL_DATA,
    MIN_INTERVAL_ROUNDS,
    MIN_UPKEEP_FEE,
    Keeper,
)
from smart_contracts.pulse.contract import Pulse


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def keeper(context: AlgopyTestContext) -> Keeper:
    return Keeper()


@pytest.fixture()
def pulse(context: AlgopyTestContext) -> Pulse:
    return Pulse()


def _selector(signature: str) -> bytes:
    return hashlib.new("sha512_256", signature.encode()).digest()[:4]


def _upkeep_key(upkeep_id: int) -> bytes:
    return b"u" + upkeep_id.to_bytes(8, "big")


def _register(
    context: AlgopyTestContext,
    keeper: Keeper,
    target,
    call_data: bytes,
    *,
    interval: int = MIN_INTERVAL_ROUNDS,
    fee: int = MIN_UPKEEP_FEE,
    funding: int | None = None,
    mbr: int | None = None,
) -> int:
    app_address = context.ledger.get_app(keeper).address
    if mbr is None:
        mbr = BOX_MBR_FIXED + 400 * len(call_data)
    if funding is None:
        funding = fee * 5
    mbr_payment = context.any.txn.payment(receiver=app_address, amount=mbr)
    funding_payment = context.any.txn.payment(receiver=app_address, amount=funding)
    return keeper.register(
        mbr_payment,
        funding_payment,
        context.ledger.get_app(target),
        arc4.DynamicBytes(call_data),
        UInt64(interval),
        UInt64(fee),
    )


def test_register(context: AlgopyTestContext, keeper: Keeper, pulse: Pulse) -> None:
    call_data = _selector("tick()uint64")
    upkeep_id = _register(context, keeper, pulse, call_data)

    assert upkeep_id == 0
    assert keeper.next_upkeep_id.value == 1
    assert context.ledger.box_exists(keeper, _upkeep_key(0))

    # Second registration gets id 1.
    assert _register(context, keeper, pulse, call_data) == 1


def test_register_rejects_low_interval(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Interval below minimum"):
        _register(
            context, keeper, pulse, b"\x00", interval=MIN_INTERVAL_ROUNDS - 1
        )


def test_register_rejects_low_fee(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Fee below minimum"):
        _register(context, keeper, pulse, b"\x00", fee=MIN_UPKEEP_FEE - 1)


def test_register_rejects_empty_call_data(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Call data size out of bounds"):
        _register(context, keeper, pulse, b"")


def test_register_rejects_oversize_call_data(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Call data size out of bounds"):
        _register(context, keeper, pulse, b"x" * (MAX_CALL_DATA + 1))


def test_register_rejects_low_funding(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Funding must cover"):
        _register(
            context, keeper, pulse, b"\x00\x01", funding=MIN_UPKEEP_FEE - 1
        )


def test_register_charges_the_real_box_mbr() -> None:
    """The MBR collected must cover what the box actually costs the app.

    Derived from the encoded box itself rather than restating the formula:
    an upkeep whose escrow the app cannot pay out is worse than one that was
    never registered (regression — the contract used to undercharge by 800
    µALGO, so the final execution failed with "balance below min").
    """
    for call_data in (_selector("tick()uint64"), b"\x01", b"x" * MAX_CALL_DATA):
        with algopy_testing_context() as ctx:
            local_keeper = Keeper()
            local_pulse = Pulse()
            upkeep_id = _register(ctx, local_keeper, local_pulse, call_data)

            key = _upkeep_key(int(upkeep_id))
            encoded = ctx.ledger.get_box(local_keeper, key)
            actual_mbr = 2_500 + 400 * (len(key) + len(encoded))

            assert BOX_MBR_FIXED + 400 * len(call_data) == actual_mbr


def test_register_rejects_low_mbr(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    call_data = _selector("tick()uint64")
    short = BOX_MBR_FIXED + 400 * len(call_data) - 1
    with pytest.raises(AssertionError, match="MBR payment too small"):
        _register(context, keeper, pulse, call_data, mbr=short)


def test_execute_happy_path(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    start_round = 1_000
    context.ledger.patch_global_fields(round=UInt64(start_round))
    call_data = _selector("tick()uint64")
    upkeep_id = _register(context, keeper, pulse, call_data)

    # Not due yet.
    with pytest.raises(AssertionError, match="Not due"):
        keeper.execute(upkeep_id)

    # Advance past the due round and execute.
    context.ledger.patch_global_fields(
        round=UInt64(start_round + MIN_INTERVAL_ROUNDS)
    )
    next_due = keeper.execute(upkeep_id)

    assert next_due == start_round + 2 * MIN_INTERVAL_ROUNDS
    # The keeper submitted the registered inner app call to Pulse...
    # (the mock records but does not execute inner calls; Pulse's counter is
    # verified end-to-end on TestNet)
    appl_itxn = context.txn.last_group.itxn_groups[-2][0]
    assert appl_itxn.app_id == context.ledger.get_app(pulse)
    assert appl_itxn.app_args(0) == call_data
    # ...and paid the executor from escrow.
    pay_itxn = context.txn.last_group.itxn_groups[-1][0]
    assert pay_itxn.amount == MIN_UPKEEP_FEE
    assert pay_itxn.receiver == context.default_sender

    # Immediately re-executing fails: next window is in the future.
    with pytest.raises(AssertionError, match="Not due"):
        keeper.execute(upkeep_id)


def test_execute_rejects_insufficient_funding(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    start_round = 1_000
    context.ledger.patch_global_fields(round=UInt64(start_round))
    call_data = _selector("tick()uint64")
    upkeep_id = _register(
        context, keeper, pulse, call_data, funding=MIN_UPKEEP_FEE
    )

    # One execution drains the escrow (funding == exactly one fee).
    context.ledger.patch_global_fields(
        round=UInt64(start_round + MIN_INTERVAL_ROUNDS)
    )
    keeper.execute(upkeep_id)

    # The next window's execution must fail: nothing left to pay with.
    context.ledger.patch_global_fields(
        round=UInt64(start_round + 2 * MIN_INTERVAL_ROUNDS)
    )
    with pytest.raises(AssertionError, match="Insufficient funding"):
        keeper.execute(upkeep_id)


def test_execute_rejects_unknown_upkeep(
    context: AlgopyTestContext, keeper: Keeper
) -> None:
    with pytest.raises(AssertionError, match="Upkeep not found"):
        keeper.execute(UInt64(42))


def test_top_up(context: AlgopyTestContext, keeper: Keeper, pulse: Pulse) -> None:
    call_data = _selector("tick()uint64")
    upkeep_id = _register(context, keeper, pulse, call_data, funding=MIN_UPKEEP_FEE)

    top_up_payment = context.any.txn.payment(
        receiver=context.ledger.get_app(keeper).address, amount=5_000
    )
    new_balance = keeper.top_up(upkeep_id, top_up_payment)
    assert new_balance == MIN_UPKEEP_FEE + 5_000


def test_cancel(context: AlgopyTestContext, keeper: Keeper, pulse: Pulse) -> None:
    call_data = _selector("tick()uint64")
    funding = MIN_UPKEEP_FEE * 3
    upkeep_id = _register(context, keeper, pulse, call_data, funding=funding)

    box_mbr = BOX_MBR_FIXED + 400 * len(call_data)
    refund = keeper.cancel(upkeep_id)
    assert not context.ledger.box_exists(keeper, _upkeep_key(0))

    # Remaining escrow *and* the released box MBR go back to the creator.
    assert refund == funding + box_mbr
    refund_itxn = context.txn.last_group.itxn_groups[-1][0]
    assert refund_itxn.amount == funding + box_mbr
    assert refund_itxn.receiver == context.default_sender

    with pytest.raises(AssertionError, match="Upkeep not found"):
        keeper.cancel(upkeep_id)
