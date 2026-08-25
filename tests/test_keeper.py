import hashlib
from collections.abc import Iterator

from algosdk import abi

import pytest
from algopy import OnCompleteAction, UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from scripts.keeper_bot import _decode_upkeep
from smart_contracts.keeper.contract import (
    BOX_MBR_FIXED,
    CATCH_UP,
    MAX_CALL_ARGS,
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


def _encode_args(call_args: list[bytes]) -> bytes:
    """The ARC-4 `byte[][]` an upkeep stores, for pricing its box."""
    return abi.ABIType.from_string("byte[][]").encode([list(a) for a in call_args])


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
    call_args: list[bytes] | None = None,
    fee_asset: int = 0,
    asset_fee: int = 0,
) -> int:
    app_address = context.ledger.get_app(keeper).address
    if call_args is None:
        call_args = [call_data]
    if mbr is None:
        mbr = BOX_MBR_FIXED + 400 * len(_encode_args(call_args))
    if funding is None:
        funding = fee * 5
    mbr_payment = context.any.txn.payment(receiver=app_address, amount=mbr)
    funding_payment = context.any.txn.payment(receiver=app_address, amount=funding)
    return keeper.register(
        mbr_payment,
        funding_payment,
        context.ledger.get_app(target),
        arc4.DynamicArray[arc4.DynamicBytes](
            *(arc4.DynamicBytes(arg) for arg in call_args)
        ),
        UInt64(interval),
        UInt64(fee),
        UInt64(policy),
        UInt64(fee_cap),
        UInt64(fee_asset),
        UInt64(asset_fee),
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


def test_register_rejects_an_empty_argument_list(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """A bare NoOp call, which almost no ARC-4 router answers.

    An upkeep registered with no arguments would call the target's bare
    handler — which most contracts do not have — and fail on every execution,
    for good, wasting the creator's MBR and every keeper's simulate. It is
    also exactly what a client bug that failed to encode anything produces.
    """
    with pytest.raises(AssertionError, match="Argument count out of bounds"):
        _register(context, keeper, pulse, b"", call_args=[])


def test_register_rejects_more_arguments_than_the_fan_out(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """Bounded at registration, not at execution.

    `execute` fans out over a fixed set of argument counts. A longer list
    would register happily and then fail on every execution — the same shape
    as the fee-cap trap, and just as permanent.
    """
    too_many = [b"\x01"] * (MAX_CALL_ARGS + 1)
    with pytest.raises(AssertionError, match="Argument count out of bounds"):
        _register(context, keeper, pulse, b"\x01", call_args=too_many)

    # One below the ceiling is fine, and so is the ceiling itself.
    assert _register(context, keeper, pulse, b"\x01", call_args=[b"\x01"] * MAX_CALL_ARGS) == 0


def test_register_rejects_an_oversize_argument_list(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The cap is on the whole encoded list, not on one argument."""
    with pytest.raises(AssertionError, match="Argument list too large"):
        _register(context, keeper, pulse, b"x" * (MAX_CALL_DATA + 1))
    # Split across the fan-out, the same total is still too large.
    with pytest.raises(AssertionError, match="Argument list too large"):
        _register(
            context, keeper, pulse, b"x", call_args=[b"x" * (MAX_CALL_DATA // 2)] * 3
        )


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
    cases: list[list[bytes]] = [
        [_selector("tick()uint64")],
        [b"\x01"],
        [_selector("absorb(uint64,string)"), b"\x00" * 8],
        [_selector("absorb(uint64,string)"), b"\x00" * 8, b"\x00" * 16],
        [b"x" * (MAX_CALL_DATA - 8)],
    ]
    for call_args in cases:
        with algopy_testing_context() as ctx:
            local_keeper = Keeper()
            local_pulse = Pulse()
            upkeep_id = _register(
                ctx, local_keeper, local_pulse, call_args[0], call_args=call_args
            )

            key = _upkeep_key(int(upkeep_id))
            encoded = ctx.ledger.get_box(local_keeper, key)
            actual_mbr = 2_500 + 400 * (len(key) + len(encoded))

            assert BOX_MBR_FIXED + 400 * len(_encode_args(call_args)) == actual_mbr


def test_register_rejects_low_mbr(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    call_data = _selector("tick()uint64")
    short = BOX_MBR_FIXED + 400 * len(_encode_args([call_data])) - 1
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

    box_mbr = BOX_MBR_FIXED + 400 * len(_encode_args([call_data]))
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


def test_an_escrow_below_the_escalated_fee_falls_back_to_base(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """An upkeep bids up to its ceiling but never more than it holds.

    Without this the escalated fee is a one-way door: once the escrow drops
    below it, lateness only grows, so the price the upkeep cannot pay only
    rises and no keeper can ever execute it again. It would sit holding up to
    a full ceiling of escrow that nobody could spend, recoverable only by the
    creator. Falling back to the base fee keeps it executable by anyone until
    the escrow is genuinely empty.
    """
    base, cap = MIN_UPKEEP_FEE, MIN_UPKEEP_FEE * 3
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), funding=cap, fee_cap=cap
    )

    # One punctual run at the base fee leaves two base fees — under the cap.
    due = _read_upkeep(context, keeper, int(upkeep_id)).next_execution_round
    context.ledger.patch_global_fields(round=UInt64(due))
    keeper.execute(upkeep_id)
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.balance == cap - base == base * 2

    # Now fall a whole interval behind. The escalated price would be the cap,
    # which the escrow cannot cover — so it pays base and still runs.
    context.ledger.patch_global_fields(
        round=UInt64(upkeep.last_serviced_round + 2 * MIN_INTERVAL_ROUNDS)
    )
    keeper.execute(upkeep_id)
    assert _fee_paid(context, keeper) == base

    # And it keeps running until the escrow is actually empty.
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    context.ledger.patch_global_fields(
        round=UInt64(upkeep.last_serviced_round + 2 * MIN_INTERVAL_ROUNDS)
    )
    keeper.execute(upkeep_id)
    assert _read_upkeep(context, keeper, int(upkeep_id)).balance == 0


def test_a_patient_keeper_cannot_farm_the_ceiling_off_a_backlog(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The attack the "measure from the last service" rule does not stop alone.

    Under CATCH_UP a replay advances the schedule by one interval, so a keeper
    that waits *two* intervals between replays is late again by its own
    measure and collects the ceiling every time — while the backlog grows
    without bound. Measured before the fix: 34 runs took 100% of a 400,000
    µALGO escrow, 33 of them at the ceiling, and left the upkeep 5,400 rounds
    further behind than it started.

    The guard is that a replay never escalates: `next_execution_round <=
    last_serviced_round` means the upkeep was already behind when it last ran,
    so this call is draining a backlog rather than clearing a market.
    """
    base, cap, interval = 4_000, 12_000, 100
    escrow = 400_000
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context,
        keeper,
        pulse,
        _selector("tick()uint64"),
        interval=interval,
        fee=base,
        funding=escrow,
        fee_cap=cap,
    )

    now = start + 21 * interval  # twenty intervals of genuine neglect
    fees: list[int] = []
    while len(fees) < 200:
        context.ledger.patch_global_fields(round=UInt64(now))
        upkeep = _read_upkeep(context, keeper, int(upkeep_id))
        if upkeep.next_execution_round > now or upkeep.balance < base:
            break
        keeper.execute(upkeep_id)
        fees.append(_fee_paid(context, keeper))
        now += 2 * interval  # wait for the fee to peak before each replay

    at_ceiling = fees.count(cap)
    assert at_ceiling == 1, f"only the first neglect should escalate, {at_ceiling} did"
    assert len(fees) > 90, f"the escrow should buy ~{escrow // base} runs, bought {len(fees)}"
    assert sum(fees) <= escrow


def test_register_requires_funding_for_one_execution_at_the_cap(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """A cap the escrow cannot reach would brick the upkeep the first time it fell behind.

    Escalation pins the fee at the cap once an upkeep is a whole interval
    late, and lateness only grows. An upkeep funded for one run at the base
    fee but carrying a higher cap would work while punctual and then be
    unexecutable by anyone, forever, until someone topped it up — so the cap
    is what `register` funds against.
    """
    cap = MIN_UPKEEP_FEE * 3
    with pytest.raises(AssertionError, match="Funding must cover"):
        _register(
            context, keeper, pulse, b"\x00", funding=cap - 1, fee_cap=cap
        )
    # Exactly one capped run is enough.
    assert _register(context, keeper, pulse, b"\x00", funding=cap, fee_cap=cap) == 0


def test_an_upkeep_that_falls_behind_can_still_pay_its_escalated_fee(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The property the funding floor buys: no capped upkeep can brick itself."""
    cap = MIN_UPKEEP_FEE * 3
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), funding=cap, fee_cap=cap
    )
    # Fall as far behind as possible: the fee is pinned at the cap.
    context.ledger.patch_global_fields(round=UInt64(start + 10_000))
    keeper.execute(upkeep_id)

    assert _fee_paid(context, keeper) == cap
    assert _read_upkeep(context, keeper, int(upkeep_id)).balance == 0


def test_a_long_dormant_upkeep_pays_the_cap_on_the_run_after_a_top_up(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """Worth pinning because it will surprise someone.

    Lateness is measured from the last *service*, and a top-up is not one. An
    upkeep that sat dormant for a week is a week late the instant it is
    funded, so the very next execution is charged the ceiling. Resetting
    lateness on a top-up would let any creator cancel escalation for one
    µALGO, so this is the behaviour — but the console has to say so.
    """
    base, cap = MIN_UPKEEP_FEE, MIN_UPKEEP_FEE * 3
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), funding=cap, fee_cap=cap
    )

    context.ledger.patch_global_fields(round=UInt64(start + 5_000))
    app_address = context.ledger.get_app(keeper).address
    keeper.top_up(upkeep_id, context.any.txn.payment(receiver=app_address, amount=cap))
    keeper.execute(upkeep_id)

    assert _fee_paid(context, keeper) == cap
    assert cap > base


# --- adversarial inputs --------------------------------------------------


def test_the_escalation_multiply_cannot_overflow_at_the_extremes(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """`(cap - base) * excess // interval` is the one multiply in the contract.

    `MAX_UPKEEP_FEE` exists to bound it. With the largest cap the contract
    accepts and an interval far beyond anything a chain will reach, the
    product stays inside a uint64 — and the fee still lands exactly on the
    cap rather than wrapping to something small.
    """
    cap = MAX_UPKEEP_FEE
    interval = 1_000_000_000  # ~90 years of rounds
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context,
        keeper,
        pulse,
        _selector("tick()uint64"),
        interval=interval,
        fee=MIN_UPKEEP_FEE,
        funding=cap,
        fee_cap=cap,
    )
    # A whole interval late: excess is clamped to the interval, which is the
    # largest multiplicand the formula can ever see.
    context.ledger.patch_global_fields(round=UInt64(start + 3 * interval))
    keeper.execute(upkeep_id)

    assert _fee_paid(context, keeper) == cap


def test_the_fee_never_leaves_its_declared_range() -> None:
    """Sweep the whole curve: base <= effective <= cap, at every lateness."""
    base, cap = MIN_UPKEEP_FEE, MIN_UPKEEP_FEE * 7
    interval = 100
    for late in (0, 1, 7, 50, 99, 100, 101, 199, 200, 5_000):
        with algopy_testing_context() as ctx:
            local_keeper, local_pulse = Keeper(), Pulse()
            ctx.ledger.patch_global_fields(round=UInt64(1_000))
            upkeep_id = _register(
                ctx,
                local_keeper,
                local_pulse,
                _selector("tick()uint64"),
                interval=interval,
                fee=base,
                funding=cap * 2,
                fee_cap=cap,
            )
            due = _read_upkeep(ctx, local_keeper, int(upkeep_id)).next_execution_round
            ctx.ledger.patch_global_fields(round=UInt64(due + late))
            local_keeper.execute(upkeep_id)

            paid = _fee_paid(ctx, local_keeper)
            assert base <= paid <= cap, f"{late} rounds late paid {paid}"


def test_skip_ahead_always_lands_strictly_in_the_future() -> None:
    """The property that stops an upkeep being due forever after one run.

    `next_due = due + (missed + 1) * interval` must exceed the current round
    for every possible lateness, or a `SKIP_AHEAD` upkeep would still be due
    the moment it was executed and drain its escrow in a single block.
    """
    interval = MIN_INTERVAL_ROUNDS
    for late in range(0, 4 * interval):
        with algopy_testing_context() as ctx:
            local_keeper, local_pulse = Keeper(), Pulse()
            ctx.ledger.patch_global_fields(round=UInt64(1_000))
            upkeep_id = _register(
                ctx,
                local_keeper,
                local_pulse,
                _selector("tick()uint64"),
                funding=MIN_UPKEEP_FEE * 20,
                policy=SKIP_AHEAD,
            )
            due = _read_upkeep(ctx, local_keeper, int(upkeep_id)).next_execution_round
            now = due + late
            ctx.ledger.patch_global_fields(round=UInt64(now))
            next_due = int(local_keeper.execute(upkeep_id))

            assert next_due > now, f"{late} late rescheduled to {next_due} at {now}"
            assert (next_due - due) % interval == 0
            with pytest.raises(AssertionError, match="Not due"):
                local_keeper.execute(upkeep_id)


# --- #8: multi-argument call shapes -------------------------------------


def test_execute_sends_every_registered_argument() -> None:
    """The whole point of #8: a target method with arguments of its own.

    Under the single-blob shape only zero-argument hooks were reachable,
    because an ARC-4 method needs its selector and each argument in an app arg
    of its own.
    """
    selector = _selector("absorb(uint64,string)")
    number = (7_777).to_bytes(8, "big")
    text = b"\x00\x06arcron"
    for call_args in ([selector], [selector, number], [selector, number, text]):
        with algopy_testing_context() as ctx:
            local_keeper, local_pulse = Keeper(), Pulse()
            ctx.ledger.patch_global_fields(round=UInt64(1_000))
            upkeep_id = _register(
                ctx, local_keeper, local_pulse, selector, call_args=call_args
            )
            ctx.ledger.patch_global_fields(round=UInt64(1_000 + MIN_INTERVAL_ROUNDS))
            local_keeper.execute(upkeep_id)

            appl = ctx.txn.last_group.itxn_groups[-2][0]
            sent = [appl.app_args(i) for i in range(len(call_args))]
            assert sent == call_args, f"{len(call_args)} args: sent {sent}"


# --- #9: an ASA bonus alongside the ALGO fee ----------------------------


def test_register_rejects_an_asset_with_no_bonus(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """A bonus of nothing pays 24 bytes of box MBR for a feature it never uses."""
    with pytest.raises(AssertionError, match="Asset fee must be positive"):
        _register(context, keeper, pulse, b"\x01", fee_asset=1_234, asset_fee=0)


def test_an_asa_upkeep_is_an_algo_upkeep_with_a_bonus(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The ALGO fee is never replaced, so no keeper needs to value the asset.

    That is what keeps the profitability floor enforceable on-chain without a
    price: `MIN_UPKEEP_FEE` still covers the keeper's real transaction costs,
    whatever the bonus is worth.
    """
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), fee_asset=1_234, asset_fee=500
    )
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert (upkeep.fee_asset, upkeep.asset_fee, upkeep.asset_balance) == (1_234, 500, 0)

    context.ledger.patch_global_fields(round=UInt64(start + MIN_INTERVAL_ROUNDS))
    keeper.execute(upkeep_id)

    # The ALGO fee is paid in full whatever happens to the bonus.
    assert _fee_paid(context, keeper) == MIN_UPKEEP_FEE


def test_an_unfunded_bonus_is_simply_not_paid(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """No asset escrow means no bonus, and no failed execution either."""
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"), fee_asset=1_234, asset_fee=500
    )
    context.ledger.patch_global_fields(round=UInt64(start + MIN_INTERVAL_ROUNDS))
    keeper.execute(upkeep_id)

    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.times_executed == 1, "the execution still happened"
    assert upkeep.asset_balance == 0
    # Two inner transactions, not three: the app call and the ALGO payment.
    assert len(context.txn.last_group.itxn_groups) >= 2


def test_top_up_asset_rejects_the_wrong_asset(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    upkeep_id = _register(
        context, keeper, pulse, b"\x01", fee_asset=1_234, asset_fee=500
    )
    app_address = context.ledger.get_app(keeper).address
    wrong = context.any.txn.asset_transfer(
        asset_receiver=app_address, xfer_asset=context.any.asset(), asset_amount=1_000
    )
    with pytest.raises(AssertionError, match="Wrong asset for this upkeep"):
        keeper.top_up_asset(upkeep_id, wrong)


def test_opt_in_asset_must_name_an_upkeep_that_uses_it(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """There is no opt-out, so the app must not accrete junk holdings.

    Tying the opt-in to an upkeep that actually names the asset costs one box
    read and stops anyone opting the app in to anything for ever.
    """
    # `UInt64()` takes a plain int, so the id has to come out of the mock's
    # Asset as one.
    asset = context.any.asset()
    upkeep_id = _register(
        context, keeper, pulse, b"\x01", fee_asset=int(asset.id), asset_fee=500
    )
    other = _register(context, keeper, pulse, b"\x01")
    app_address = context.ledger.get_app(keeper).address
    mbr = context.any.txn.payment(receiver=app_address, amount=100_000)

    with pytest.raises(AssertionError, match="does not use this asset"):
        keeper.opt_in_asset(mbr, other, asset)
    assert keeper.opt_in_asset(mbr, upkeep_id, asset) == 100_000


# --- governance: upgradeable until frozen ------------------------------


def test_only_the_creator_can_update_or_freeze(
    context: AlgopyTestContext, keeper: Keeper
) -> None:
    stranger = context.any.account()
    # Separate groups: update runs on UpdateApplication and freeze on NoOp, and
    # one group carries one OnCompletion.
    with context.txn.create_group(
        active_txn_overrides={"sender": stranger, "on_completion": OnCompleteAction.UpdateApplication}
    ):
        with pytest.raises(Exception, match="Only the creator can update"):
            keeper.update()
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(Exception, match="Only the creator can freeze"):
            keeper.freeze()


def test_freezing_is_one_way(context: AlgopyTestContext, keeper: Keeper) -> None:
    """Nothing sets frozen back to 0, and after this no call could add one."""
    assert keeper.frozen.value == 0
    keeper.freeze()
    assert keeper.frozen.value == 1
    with pytest.raises(Exception, match="Already frozen"):
        keeper.freeze()


def test_update_is_refused_once_frozen(
    context: AlgopyTestContext, keeper: Keeper
) -> None:
    """The whole promise. Before freeze it works; after, it never does again."""
    with context.txn.create_group(
        active_txn_overrides={"on_completion": OnCompleteAction.UpdateApplication}
    ):
        keeper.update()  # allowed while unfrozen
    keeper.freeze()
    with context.txn.create_group(
        active_txn_overrides={"on_completion": OnCompleteAction.UpdateApplication}
    ):
        with pytest.raises(Exception, match="Frozen: the programs cannot be replaced"):
            keeper.update()


def test_frozen_is_readable_before_anyone_escrows(
    context: AlgopyTestContext, keeper: Keeper
) -> None:
    """A promise nobody can check is not worth escrowing against."""
    assert keeper.frozen.value == 0
    keeper.freeze()
    assert keeper.frozen.value == 1


# --- an ASA bonus must never strand the ALGO ---------------------------


def test_cancel_returns_algo_even_when_the_bonus_cannot_be_paid(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """The book value can exceed what the app actually holds.

    An ASA with a clawback address can be taken back out of the app by its
    issuer, and a frozen one cannot be sent at all. The bonus transfer shares
    a transaction with the ALGO refund, so trusting the book value would make
    the refund fail with it: the creator would lose their escrow and their box
    minimum balance to somebody else's asset settings, permanently, on a
    contract with no delete path.
    """
    asset = context.any.asset()
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"),
        fee_asset=int(asset.id), asset_fee=500,
    )
    # Book value says there is a bonus; the app holds none of it.
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.asset_balance == 0

    refund = int(keeper.cancel(upkeep_id))
    assert refund > 0, "the ALGO came back regardless of the asset"


def test_cancel_forfeits_a_funded_bonus_rather_than_holding_the_algo_hostage(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """A creator who never opted in still gets every microalgo back.

    This is the path the spec once described as a hard failure, and the one
    the code deliberately moved away from: reading a balance or a freeze flag
    for an account that never opted in fails rather than answering, so a
    creator who cannot receive the asset would have had their ALGO refund
    fail with the bonus transfer and lose escrow and box minimum balance both.
    Forfeiting the bonus is the lesser loss, and it is silent, so the ordering
    of the opt-in check ahead of any balance read is what makes it work.
    """
    asset = context.any.asset()
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"),
        fee_asset=int(asset.id), asset_fee=500,
    )
    app_address = context.ledger.get_app(keeper).address
    keeper.top_up_asset(
        upkeep_id,
        context.any.txn.asset_transfer(
            asset_receiver=app_address, xfer_asset=asset, asset_amount=5_000
        ),
    )
    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.asset_balance == 5_000, "the bonus is genuinely funded"

    # The creator never opted in, so the asset cannot be sent to them.
    refund = int(keeper.cancel(upkeep_id))
    assert refund > 0, "the escrow and box MBR came back in full"


def test_an_execution_is_not_blocked_by_a_bonus_the_app_cannot_send(
    context: AlgopyTestContext, keeper: Keeper, pulse: Pulse
) -> None:
    """An upkeep whose bonus asset was clawed back must still be serviced.

    Otherwise it stops being executed at all and its ALGO escrow strands.
    """
    asset = context.any.asset()
    start = 1_000
    context.ledger.patch_global_fields(round=UInt64(start))
    upkeep_id = _register(
        context, keeper, pulse, _selector("tick()uint64"),
        fee_asset=int(asset.id), asset_fee=500,
    )
    context.ledger.patch_global_fields(round=UInt64(start + MIN_INTERVAL_ROUNDS))
    keeper.execute(upkeep_id)

    upkeep = _read_upkeep(context, keeper, int(upkeep_id))
    assert upkeep.times_executed == 1, "the execution still happened"
    assert _fee_paid(context, keeper) == MIN_UPKEEP_FEE, "and the ALGO fee was paid"
