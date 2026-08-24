import hashlib
from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from scripts.keeper_bot import _decode_upkeep
from smart_contracts.keeper.contract import (
    BOX_MBR_FIXED,
    CATCH_UP,
    MAX_CALL_DATA,
    MAX_UPKEEP_FEE,
    MIN_INTERVAL_ROUNDS,
    MIN_UPKEEP_FEE,
    SKIP_AHEAD,
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
    policy: int = CATCH_UP,
    fee_cap: int = 0,
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
        UInt64(policy),
        UInt64(fee_cap),
    )


def _read_upkeep(context: AlgopyTestContext, keeper: Keeper, upkeep_id: int):
    """Decode a box through the bot's decoder, so the two stay in lockstep."""
    return _decode_upkeep(upkeep_id, ctx_box(context, keeper, upkeep_id))


def ctx_box(context: AlgopyTestContext, keeper: Keeper, upkeep_id: int) -> bytes:
    return bytes(context.ledger.get_box(keeper, _upkeep_key(upkeep_id)))


def _fee_paid(context: AlgopyTestContext, keeper: Keeper) -> int:
    """The amount of the payment `execute` just made to the caller."""
    return int(context.txn.last_group.itxn_groups[-1][0].amount)


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


# --- #7: catch-up policy -------------------------------------------------


def test_register_rejects_an_unknown_policy(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Unknown catch-up policy"):
        _register(context, keeper, pulse, b"\x00", policy=SKIP_AHEAD + 1)


def test_catch_up_replays_every_missed_interval(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The default: an upkeep left unattended stays due until it has caught up."""
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), funding=MIN_UPKEEP_FEE * 10
    )
    # Four whole intervals go by with nobody watching.
    now = start + 4 * MIN_INTERVAL_ROUNDS
    context.ledger.patch_global_fields(round=UInt64(now))

    runs = 0
    while _read_upkeep(context, keeper, int(upkeep_id)).next_execution_round <= now:
        keeper.execute(upkeep_id)
        runs += 1

    assert runs == 4
    assert _read_upkeep(context, keeper, int(upkeep_id)).times_executed == 4


def test_skip_ahead_runs_once_and_keeps_the_schedule_phase(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The alternative: drop the backlog, land on the next slot that is still ahead."""
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context,
        keeper,
        pulse,
        _selector("tick()uint64"),
        funding=MIN_UPKEEP_FEE * 10,
        policy=SKIP_AHEAD,
    )
    scheduled = _read_upkeep(context, keeper, int(upkeep_id)).next_execution_round
    now = start + 4 * MIN_INTERVAL_ROUNDS + 3
    context.ledger.patch_global_fields(round=UInt64(now))

    next_due = int(keeper.execute(upkeep_id))

    assert next_due > now, "must land strictly in the future"
    assert next_due - now <= MIN_INTERVAL_ROUNDS, "must land on the *first* future slot"
    assert (next_due - scheduled) % MIN_INTERVAL_ROUNDS == 0, "must keep its phase"

    # And it is done: one execution, not five.
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.times_executed == 1
    with pytest.raises(AssertionError, match="Not due"):
        keeper.execute(upkeep_id)


def test_execute_records_the_round_it_actually_ran_in(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """`last_serviced_round` is the round it ran, not the round it was due."""
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(context, keeper, pulse, _selector("tick()uint64"))
    assert _read_upkeep(context, keeper, int(upkeep_id)).last_serviced_round == start

    late = start + 5 * MIN_INTERVAL_ROUNDS
    context.ledger.patch_global_fields(round=UInt64(late))
    keeper.execute(upkeep_id)

    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.last_serviced_round == late
    assert upkeep.next_execution_round != late, "the schedule is not the service"


# --- #14: overdue fee escalation ----------------------------------------


def test_register_rejects_a_cap_below_the_fee(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Fee cap below the fee"):
        _register(context, keeper, pulse, b"\x00", fee_cap=MIN_UPKEEP_FEE - 1)


def test_register_rejects_fees_above_the_maximum(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    with pytest.raises(AssertionError, match="Fee above maximum"):
        _register(context, keeper, pulse, b"\x00", fee=MAX_UPKEEP_FEE + 1)
    with pytest.raises(AssertionError, match="Fee cap above maximum"):
        _register(context, keeper, pulse, b"\x00", fee_cap=MAX_UPKEEP_FEE + 1)


def test_a_zero_cap_means_the_fee_never_moves(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), funding=MIN_UPKEEP_FEE * 10
    )
    context.ledger.patch_global_fields(round=UInt64(start + 50 * MIN_INTERVAL_ROUNDS))
    keeper.execute(upkeep_id)

    assert _fee_paid(context, keeper) == MIN_UPKEEP_FEE


@pytest.mark.parametrize(
    ("rounds_late", "expected"),
    [
        (0, 4_000),  # on time: one interval since the last service, nothing to escalate
        (2, 5_600),  # a fifth of an interval late
        (5, 8_000),  # halfway
        (9, 11_200),
        (10, 12_000),  # a whole interval late: the ceiling
        (500, 12_000),  # and it holds there
    ],
)
def test_the_fee_rises_linearly_to_the_cap_and_holds(
    rounds_late: int, expected: int
) -> None:
    """Linear from base to cap over one missed interval, then flat."""
    cap = 12_000
    with algopy_testing_context() as ctx:
        local_keeper, local_pulse = Keeper(), Pulse()
        start = 1_000
        ctx.ledger.patch_global_fields(round=UInt64(start))
        upkeep_id = _register(
            ctx,
            local_keeper,
            local_pulse,
            _selector("tick()uint64"),
            funding=cap * 4,
            fee_cap=cap,
        )
        due = _read_upkeep(ctx, local_keeper, int(upkeep_id)).next_execution_round
        ctx.ledger.patch_global_fields(round=UInt64(due + rounds_late))
        local_keeper.execute(upkeep_id)

        assert _fee_paid(ctx, local_keeper) == expected


def test_escalation_is_measured_from_the_last_service_not_the_schedule(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The rule that stops catch-up and escalation multiplying.

    Escalation clears a market. Once a keeper has arrived the market has
    cleared, so the backlog it then drains pays base — the first execution of
    a burst pays the ceiling and no other one does. Measured from the schedule
    instead, every replay would pay the ceiling and a neglected upkeep would
    burn its escrow on work nobody asked for.
    """
    base, cap = MIN_UPKEEP_FEE, MIN_UPKEEP_FEE * 3
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context,
        keeper,
        pulse,
        _selector("tick()uint64"),
        funding=cap * 30,
        fee_cap=cap,
    )
    # Twenty intervals of neglect, then one keeper drains the whole backlog.
    now = start + 21 * MIN_INTERVAL_ROUNDS
    context.ledger.patch_global_fields(round=UInt64(now))

    fees: list[int] = []
    while _read_upkeep(context, keeper, int(upkeep_id)).next_execution_round <= now:
        keeper.execute(upkeep_id)
        fees.append(_fee_paid(context, keeper))

    assert fees[0] == cap, "the execution that cleared the market pays the ceiling"
    assert set(fees[1:]) == {base}, "everything behind it pays base"
    assert sum(fees) == cap + (len(fees) - 1) * base
    # The rule this test exists to defend: measured from the schedule instead,
    # every replay would have paid the ceiling.
    assert sum(fees) < len(fees) * cap


def test_escalation_raises_the_bar_an_upkeep_has_to_clear(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """An upkeep can go dormant at a balance its creator thought was enough."""
    base, cap = MIN_UPKEEP_FEE, MIN_UPKEEP_FEE * 3
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context,
        keeper,
        pulse,
        _selector("tick()uint64"),
        funding=base * 2,  # two runs at the price the creator wrote down
        fee_cap=cap,
    )
    due = _read_upkeep(context, keeper, int(upkeep_id)).next_execution_round
    context.ledger.patch_global_fields(round=UInt64(due + MIN_INTERVAL_ROUNDS))

    with pytest.raises(AssertionError, match="Insufficient funding"):
        keeper.execute(upkeep_id)
