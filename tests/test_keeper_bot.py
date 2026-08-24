"""Regression tests for the keeper bot's Upkeep box decoder.

The vector is a real box value, recorded from a chain rather than hand-built,
so it pins the encoding the contract actually produces. Its TypeScript twin is
`web/src/app/core/upkeep.test.ts`, which reads the same bytes; if the
contract's Upkeep struct changes, both must change together.

Recorded on LocalNet from the 1.0 contract — #7, #14, #8 and #9 together:
upkeep 0 on app 18775, after its first execution. Every field the batch added
holds a non-zero value, so a decoder that ignores any of them cannot pass:
SKIP_AHEAD, a 12,000 µALGO ceiling, a three-argument call, and an ASA bonus
that was actually paid (the asset escrow is 750,000 of the 1,000,000 funded).
"""

import base64

from dataclasses import replace

from scripts.keeper_bot import (
    CATCH_UP,
    SKIP_AHEAD,
    _as_bytes,
    _decode_upkeep,
    effective_fee,
    select_due,
)

# Box value of upkeep 0 on LocalNet app 18775.
LIVE_BOX_HEX = (
    "5defa167e82d6882b1a57beb7d3bb8583440a2e2e19a27358c94744a4fa7e3cf"
    "0000000000004959"  # target_app = 18777
    "0082"  # tail offset = 130
    "000000000000000a"  # interval_rounds = 10
    "0000000000003698"  # next_execution_round = 13976
    "0000000000000fa0"  # fee_per_execution = 4000
    "0000000000008980"  # balance = 35200
    "0000000000000001"  # times_executed = 1
    "0000000000000001"  # policy = SKIP_AHEAD
    "0000000000002ee0"  # fee_cap = 12000
    "000000000000368f"  # last_serviced_round = 13967
    "000000000000495a"  # fee_asset = 18778
    "000000000003d090"  # asset_fee = 250000
    "00000000000b71b0"  # asset_balance = 750000
    # tail: byte[][] of absorb(uint64,string)'s selector, 7777 and "archon"
    "00030006000c00160004cb782a4800080000000000001e6100080006617263686f6e"
)


def test_decode_live_box() -> None:
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))

    assert upkeep.upkeep_id == 0
    assert upkeep.target_app == 18777
    assert upkeep.interval_rounds == 10
    assert upkeep.next_execution_round == 13976
    assert upkeep.fee_per_execution == 4000
    assert upkeep.balance == 35200
    assert upkeep.times_executed == 1
    assert upkeep.policy == SKIP_AHEAD
    assert upkeep.fee_cap == 12000
    assert upkeep.last_serviced_round == 13967
    assert upkeep.fee_asset == 18778
    assert upkeep.asset_fee == 250_000
    assert upkeep.asset_balance == 750_000


def test_the_recorded_box_is_the_length_the_mbr_formula_assumes() -> None:
    """9-byte name + 130-byte head + the encoded argument list."""
    from algosdk import abi

    from smart_contracts.keeper.contract import BOX_MBR_FIXED

    raw = bytes.fromhex(LIVE_BOX_HEX)
    tail = raw[130:]
    assert len(raw) == 130 + len(tail)
    assert 2_500 + 400 * (9 + len(raw)) == BOX_MBR_FIXED + 400 * len(tail)

    # And the tail really is the three app args the target was called with.
    args = [bytes(a) for a in abi.ABIType.from_string("byte[][]").decode(tail)]
    assert args == [
        abi.Method.from_signature("absorb(uint64,string)uint64").get_selector(),
        (7_777).to_bytes(8, "big"),
        abi.ABIType.from_string("string").encode("archon"),
    ]


def test_effective_fee_walks_the_documented_curve() -> None:
    """The bot's twin of `execute`'s escalation arithmetic.

    Linear from base to cap over one missed interval, then flat. This pins the
    curve's shape only — the twin is checked *against the contract* by
    `tests/test_keeper.py::test_the_fee_rises_linearly_to_the_cap_and_holds`
    and, on a real chain, by `scripts/keeper_e2e.py` stage 16, which asserts
    every fee the contract charged equals what this function predicted.
    """
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    serviced = upkeep.last_serviced_round

    # On time — one interval since the last service — is not late.
    assert effective_fee(upkeep, serviced + 10) == 4_000
    assert effective_fee(upkeep, serviced + 15) == 8_000
    assert effective_fee(upkeep, serviced + 20) == 12_000
    assert effective_fee(upkeep, serviced + 10_000) == 12_000, "the cap holds"
    # And a keeper that has just serviced it is not owed the ceiling again.
    assert effective_fee(upkeep, serviced) == 4_000


def test_a_zero_cap_never_escalates() -> None:
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    upkeep.fee_cap = 0
    upkeep.policy = CATCH_UP
    assert effective_fee(upkeep, upkeep.last_serviced_round + 10_000) == 4_000


def test_as_bytes_accepts_bytes_and_base64() -> None:
    raw = bytes.fromhex(LIVE_BOX_HEX)
    assert _as_bytes(raw) == raw
    assert _as_bytes(bytearray(raw)) == raw
    assert _as_bytes(base64.b64encode(raw).decode()) == raw


def test_select_due_takes_the_richest_work_first() -> None:
    """The bot's actual selection, not a copy of it.

    This is the one behavioural change escalation asks of a keeper: take what
    pays most now, rather than whatever has the lowest id. A regression to
    registry order has to fail something.
    """
    base = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    # Due one interval after the last service, so this is genuine neglect
    # rather than a replay — a replay never escalates.
    due = base.last_serviced_round + 10
    late = replace(base, upkeep_id=1, fee_cap=12_000, next_execution_round=due)
    richer = replace(
        base, upkeep_id=2, fee_per_execution=6_000, fee_cap=0, next_execution_round=due
    )
    not_due = replace(base, upkeep_id=3, fee_cap=0, next_execution_round=due + 90_000)
    broke = replace(base, upkeep_id=4, fee_cap=0, balance=1, next_execution_round=due)

    at_round = base.last_serviced_round + 20  # a whole interval past the service
    order = select_due([richer, late, not_due, broke], at_round)

    assert [u.upkeep_id for u in order] == [1, 2], "escalated first, then the richer one"
    assert effective_fee(order[0], at_round) == 12_000
    assert select_due([richer, late], at_round, is_blocked=lambda i: i == 1)[0].upkeep_id == 2


def test_select_due_falls_back_to_id_order_when_nothing_escalates() -> None:
    base = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    due = base.last_serviced_round + 10
    flat = [replace(base, upkeep_id=i, fee_cap=0, next_execution_round=due) for i in (3, 1, 2)]
    assert [u.upkeep_id for u in select_due(flat, due + 2)] == [1, 2, 3]
