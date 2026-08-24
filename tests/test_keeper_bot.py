"""Regression tests for the keeper bot's Upkeep box decoder.

The vector is a real box value captured from the TestNet keeper app
(769772891), upkeep id 4 after its first execution. It locks the ARC-4
head/tail layout the bot depends on; if the contract's Upkeep struct ever
changes, this test must change with it.
"""

import base64

from scripts.keeper_bot import _as_bytes, _decode_upkeep

# Box value of upkeep 4 on TestNet app 769772891 (round ~66611610).
LIVE_BOX_HEX = (
    "2759a71fb768d8d0053eab8aea563a42a2f11a07e6df5175fb1da10d2ebaaa6b"
    "000000002de1cd6a"  # target_app = 769772906
    "0052"  # tail offset = 82
    "000000000000000a"  # interval_rounds = 10
    "0000000003f864f3"  # next_execution_round = 66610419
    "0000000000000fa0"  # fee_per_execution = 4000
    "0000000000003e80"  # balance = 16000
    "0000000000000001"  # times_executed = 1
    "00044d4d5f0b"  # tail: uint16 length 4 + tick()uint64 selector
)


def test_decode_live_box() -> None:
    upkeep = _decode_upkeep(4, bytes.fromhex(LIVE_BOX_HEX))

    assert upkeep.upkeep_id == 4
    assert upkeep.target_app == 769772906
    assert upkeep.interval_rounds == 10
    assert upkeep.next_execution_round == 66610419
    assert upkeep.fee_per_execution == 4000
    assert upkeep.balance == 16000
    assert upkeep.times_executed == 1


def test_as_bytes_accepts_bytes_and_base64() -> None:
    raw = bytes.fromhex(LIVE_BOX_HEX)
    assert _as_bytes(raw) == raw
    assert _as_bytes(bytearray(raw)) == raw
    assert _as_bytes(base64.b64encode(raw).decode()) == raw
