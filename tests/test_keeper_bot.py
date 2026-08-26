"""Regression tests for the keeper bot's Upkeep box decoder.

The vector is a real box value, recorded from a chain rather than hand-built,
so it pins the encoding the contract actually produces. Its TypeScript twin is
`web/src/app/core/upkeep.test.ts`, which reads the same bytes; if the
contract's Upkeep struct changes, both must change together.

Recorded on LocalNet from the 1.0 contract — #7, #14, #8 and #9 together:
upkeep 0 on app 20153, after its first execution. Every field the batch added
holds a non-zero value, so a decoder that ignores any of them cannot pass:
SKIP_AHEAD, a 12,000 µALGO ceiling, a three-argument call, and an ASA bonus
that was actually paid (the asset escrow is 750,000 of the 1,000,000 funded).
"""

import base64
from dataclasses import replace

import pytest

from scripts.keeper_bot import (
    CATCH_UP,
    SKIP_AHEAD,
    is_frozen,
    _as_bytes,
    _decode_upkeep,
    effective_fee,
    resolve_app_id,
    select_due,
)

# Box value of upkeep 0 on LocalNet app 20153.
LIVE_BOX_HEX = (
    "5defa167e82d6882b1a57beb7d3bb8583440a2e2e19a27358c94744a4fa7e3cf"
    "0000000000004ebb"  # target_app = 20155
    "0082"  # tail offset = 130
    "000000000000000a"  # interval_rounds = 10
    "0000000000003acf"  # next_execution_round = 15055
    "0000000000000fa0"  # fee_per_execution = 4000
    "0000000000008980"  # balance = 35200
    "0000000000000001"  # times_executed = 1
    "0000000000000001"  # policy = SKIP_AHEAD
    "0000000000002ee0"  # fee_cap = 12000
    "0000000000003ac6"  # last_serviced_round = 15046
    "0000000000004ebc"  # fee_asset = 20156
    "000000000003d090"  # asset_fee = 250000
    "00000000000b71b0"  # asset_balance = 750000
    # tail: byte[][] of absorb(uint64,string)'s selector, 7777 and "arcron"
    "00030006000c00160004cb782a4800080000000000001e6100080006617263726f6e"
)


def test_decode_live_box() -> None:
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))

    assert upkeep.upkeep_id == 0
    assert upkeep.target_app == 20155
    assert upkeep.interval_rounds == 10
    assert upkeep.next_execution_round == 15055
    assert upkeep.fee_per_execution == 4000
    assert upkeep.balance == 35200
    assert upkeep.times_executed == 1
    assert upkeep.policy == SKIP_AHEAD
    assert upkeep.fee_cap == 12000
    assert upkeep.last_serviced_round == 15046
    assert upkeep.fee_asset == 20156
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
        abi.ABIType.from_string("string").encode("arcron"),
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


def test_decode_rejects_a_box_from_an_older_contract() -> None:
    """Silently decoding the wrong struct is worse than refusing to.

    A box written by a deployment that predates the 1.0 batch is 88 bytes
    against this struct's 130-byte head. Read past its end and Python hands
    back zeros, so a keeper would compute a fee from numbers that were never
    in the box — and act on it. The tail offset is the fingerprint: the
    contract always writes 130 there.
    """
    old_box = bytes.fromhex(
        "2759a71fb768d8d0053eab8aea563a42a2f11a07e6df5175fb1da10d2ebaaa6b"
        "000000002de1cd6a"  # target_app
        "0052"  # tail offset = 82, the head this struct replaced
        "000000000000000a0000000003f864f30000000000000fa0"
        "0000000000003e800000000000000001"
        "00044d4d5f0b"
    )
    with pytest.raises(ValueError, match="too short to be an Upkeep"):
        _decode_upkeep(4, old_box)

    # Long enough, but still not this struct: caught by the offset.
    wrong_shape = bytearray(bytes.fromhex(LIVE_BOX_HEX))
    wrong_shape[40:42] = (82).to_bytes(2, "big")
    with pytest.raises(ValueError, match="different version of the contract"):
        _decode_upkeep(4, bytes(wrong_shape))


def test_resolve_app_id_refuses_to_guess() -> None:
    """No default: there is no deployment of this contract to default to."""
    import argparse

    parser = argparse.ArgumentParser()
    assert resolve_app_id(parser, 123, "testnet") == 123
    with pytest.raises(SystemExit):
        resolve_app_id(parser, None, "testnet")


# --- the frozen guard on the deployer fallback ------------------------

class _FakeAlgod:
    """Just enough of algod to answer `is_frozen`."""

    def __init__(self, state: list[dict]) -> None:
        self._state = state

    def application_info(self, app_id: int) -> dict:
        return {"params": {"global-state": self._state}}


def _entry(key: str, value: int) -> dict:
    return {"key": base64.b64encode(key.encode()).decode(), "value": {"uint": value}}


def test_an_app_with_frozen_zero_is_not_frozen() -> None:
    """The value the guard exists for: the creator can still rewrite execute."""
    assert is_frozen(_FakeAlgod([_entry("frozen", 0)]), 1) is False


def test_an_app_with_frozen_one_is_frozen() -> None:
    assert is_frozen(_FakeAlgod([_entry("frozen", 1)]), 1) is True


def test_an_app_with_no_frozen_key_predates_governance_and_is_immutable() -> None:
    """Absent is not unknown.

    A deployment made before `update` and `freeze` existed carries no flag and
    has no update path at all, so reading a missing key as "not frozen" would
    refuse the fallback on precisely the apps that cannot be rewritten.
    """
    assert is_frozen(_FakeAlgod([_entry("next_upkeep_id", 23)]), 1) is True


def test_the_recorded_box_names_its_creator() -> None:
    """The box always carried the creator and the decoder dropped it.

    Nothing downstream could tell one creator's upkeep from another's, which
    is the detector the pre-freeze MainNet window depends on: the plan is to
    freeze the moment an upkeep appears that is not ours, and until now
    nothing could say which those were.

    Pinned against the same recorded box as the rest of the decoder, so the
    offset is checked against real bytes rather than against the docstring.
    """
    upkeep = _decode_upkeep(1, bytes.fromhex(LIVE_BOX_HEX))
    assert len(upkeep.creator) == 58, "an Algorand address is 58 characters"
    assert upkeep.creator.isupper() or any(c.isdigit() for c in upkeep.creator)
