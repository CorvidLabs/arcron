"""The bot's self-checks: balance guarding and registry health.

Both are asserted against fakes rather than a chain, because the interesting
cases — an empty keeper, a registry nobody is servicing — are awkward to
arrange live and trivial to describe here. The box bytes are the same recorded
vector `test_keeper_bot.py` pins, and every figure below is read out of it
rather than restated, so a re-pinned box cannot leave these tests asserting
against numbers that are no longer in it.
"""

import base64

import pytest

from scripts.keeper_bot import (
    _decode_upkeep,
    ACCOUNT_MBR_MICROALGO,
    EXECUTION_COST_MICROALGO,
    HARD_MINIMUM_MICROALGO,
    LOW_BALANCE_MICROALGO,
    STALL_INTERVALS,
    UnrecoverableError,
    check_registry,
    guard_balance,
)
from tests.test_keeper_bot import LIVE_BOX_HEX

KEEPER = "LXX2CZ7IFVUIFMNFPPVX2O5YLA2EBIXC4GNCONMMSR2EUT5H4PHZ53VNOQ"
_PINNED = _decode_upkeep(4, bytes.fromhex(LIVE_BOX_HEX))
UPKEEP_DUE_ROUND = _PINNED.next_execution_round
UPKEEP_INTERVAL = _PINNED.interval_rounds


class FakeAlgod:
    """Just enough algod for the two checks under test."""

    def __init__(self, *, balance: int = 0, round: int = 0, boxes: dict | None = None):
        self._balance = balance
        self._round = round
        self._boxes = boxes or {}

    def account_info(self, address: str) -> dict:
        return {"amount": self._balance, "min-balance": ACCOUNT_MBR_MICROALGO}

    def status(self) -> dict:
        return {"last-round": self._round}

    def application_boxes(self, app_id: int, **kwargs) -> dict:
        return {"boxes": [{"name": base64.b64encode(name).decode()} for name in self._boxes]}

    def application_box_by_name(self, app_id: int, name: bytes) -> dict:
        return {"value": base64.b64encode(self._boxes[name]).decode()}


def _box(upkeep_id: int, *, balance: int | None = None) -> tuple[bytes, bytes]:
    raw = bytearray(bytes.fromhex(LIVE_BOX_HEX))
    if balance is not None:
        raw[66:74] = balance.to_bytes(8, "big")
    return b"u" + upkeep_id.to_bytes(8, "big"), bytes(raw)


# --- balance guarding -------------------------------------------------

def test_refuses_to_start_when_it_cannot_afford_one_execution() -> None:
    algod = FakeAlgod(balance=HARD_MINIMUM_MICROALGO - 1)
    with pytest.raises(UnrecoverableError) as raised:
        guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO)
    # The message has to say what is wrong and what to do about it.
    assert "below the" in str(raised.value)
    assert "Fund it before starting" in str(raised.value)


def test_starts_with_exactly_enough_for_one_execution() -> None:
    algod = FakeAlgod(balance=HARD_MINIMUM_MICROALGO)
    assert guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO) == HARD_MINIMUM_MICROALGO


def test_warns_while_low_but_keeps_running(caplog) -> None:
    # Ten executions of headroom: workable, but worth saying out loud.
    balance = ACCOUNT_MBR_MICROALGO + 10 * EXECUTION_COST_MICROALGO
    algod = FakeAlgod(balance=balance)
    with caplog.at_level("WARNING"):
        assert guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO) == balance
    assert "10 execution(s) of headroom" in caplog.text


def test_says_nothing_when_comfortably_funded(caplog) -> None:
    algod = FakeAlgod(balance=LOW_BALANCE_MICROALGO * 2)
    with caplog.at_level("WARNING"):
        guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO)
    assert caplog.text == ""


# --- registry health --------------------------------------------------

def test_healthy_registry_exits_zero() -> None:
    name, value = _box(4)
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND - 1, boxes={name: value})
    assert check_registry(algod, 1) == 0


def test_recently_due_is_not_yet_a_stall() -> None:
    # Due, but only just — a keeper gets a couple of intervals of grace.
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND + UPKEEP_INTERVAL, boxes=dict([_box(4)]))
    assert check_registry(algod, 1) == 0


def test_long_overdue_upkeep_is_a_stall(caplog) -> None:
    overdue_by = (STALL_INTERVALS + 3) * UPKEEP_INTERVAL
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND + overdue_by, boxes=dict([_box(4)]))
    with caplog.at_level("WARNING"):
        assert check_registry(algod, 1) == 1
    assert "nobody is servicing it" in caplog.text


def test_a_starved_upkeep_is_not_blamed_on_keepers(caplog) -> None:
    # Escrow below one fee: no keeper can execute it, so it is not a stall.
    name, value = _box(4, balance=1)
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND + 10_000, boxes={name: value})
    with caplog.at_level("INFO"):
        assert check_registry(algod, 1) == 0
    assert "needs a top-up, not a keeper" in caplog.text


def test_an_upkeep_that_cannot_afford_the_ceiling_is_not_reported_starved(
    caplog,
) -> None:
    """The bot's twin of the contract's fall-back, checked through `--check`.

    The pinned upkeep has a 4,000 µALGO fee and a 12,000 µALGO ceiling. An
    escrow of 5,000 cannot pay the escalated price, so the contract charges
    base instead — which means the upkeep is perfectly executable and must not
    be reported as out of funds.
    """
    name, value = _box(4, balance=5_000)
    algod = FakeAlgod(
        round=_PINNED.last_serviced_round + 2 * UPKEEP_INTERVAL, boxes={name: value}
    )
    with caplog.at_level("INFO"):
        check_registry(algod, 1)
    assert "needs a top-up, not a keeper" not in caplog.text


def test_empty_registry_is_healthy() -> None:
    assert check_registry(FakeAlgod(round=1), 1) == 0
