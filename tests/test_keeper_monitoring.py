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

    def __init__(
        self,
        *,
        balance: int = 0,
        round: int = 0,
        boxes: dict | None = None,
        floor: int = ACCOUNT_MBR_MICROALGO,
        assets: list[int] | None = None,
    ):
        self._balance = balance
        self._round = round
        self._boxes = boxes or {}
        self._floor = floor
        self._assets = assets or []

    def account_info(self, address: str) -> dict:
        return {
            "amount": self._balance,
            "min-balance": self._floor,
            "assets": [{"asset-id": asset_id} for asset_id in self._assets],
        }

    def asset_info(self, asset_id: int) -> dict:
        return {"params": {"unit-name": "TEST", "decimals": 6}}

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
#
# Every figure here is *spendable*: what the account holds, less the minimum
# balance the node reports. That floor is not a constant, and treating it as
# one was a real bug. It rises by 100,000 µALGO for every asset the account is
# opted in to and for every app or asset it created, so a keeper opted in to a
# few bonus assets believed it had ALGO to spend that it could not touch, and
# would have discovered it by failing to broadcast rather than by refusing to
# start.


def test_refuses_to_start_when_it_cannot_afford_one_execution() -> None:
    algod = FakeAlgod(balance=ACCOUNT_MBR_MICROALGO + EXECUTION_COST_MICROALGO - 1)
    with pytest.raises(UnrecoverableError) as raised:
        guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO)
    # The message has to say what is wrong and what to do about it.
    assert "below the" in str(raised.value)
    assert "Fund it before starting" in str(raised.value)


def test_starts_with_exactly_enough_for_one_execution() -> None:
    algod = FakeAlgod(balance=ACCOUNT_MBR_MICROALGO + EXECUTION_COST_MICROALGO)
    assert guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO) == EXECUTION_COST_MICROALGO


def test_a_floor_raised_by_opt_ins_is_read_rather_than_assumed() -> None:
    """The bug, in one assertion.

    An account holding 5.5 ALGO with eleven asset opt-ins has a 5,439,000
    µALGO floor and 61,000 µALGO to spend: twenty executions, not eighteen
    hundred. Assuming the 100,000 of a bare account overstated it by 5.34 ALGO
    and would have let the bot start and then fail on a fee it could not pay.
    """
    floor = 5_439_000
    algod = FakeAlgod(balance=floor + 61_000, floor=floor)

    assert guard_balance(algod, KEEPER, 0) == 61_000


def test_a_rich_looking_account_below_its_floor_refuses_to_start() -> None:
    """Several ALGO held, 2,000 µALGO of it spendable, which is not one
    execution. The old check compared 5,441,000 against 103,000 and started."""
    algod = FakeAlgod(balance=5_441_000, floor=5_439_000)

    with pytest.raises(UnrecoverableError, match="minimum balance it cannot spend"):
        guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO)


def test_a_node_that_omits_the_floor_falls_back_to_a_bare_account() -> None:
    """A guess is better than refusing to start; it is only ever too generous."""

    class _Terse(FakeAlgod):
        def account_info(self, address: str) -> dict:
            return {"amount": ACCOUNT_MBR_MICROALGO + EXECUTION_COST_MICROALGO}

    assert guard_balance(_Terse(), KEEPER, 0) == EXECUTION_COST_MICROALGO


def test_warns_while_low_but_keeps_running(caplog) -> None:
    # Ten executions of headroom: workable, but worth saying out loud.
    spendable = 10 * EXECUTION_COST_MICROALGO
    algod = FakeAlgod(balance=ACCOUNT_MBR_MICROALGO + spendable)
    with caplog.at_level("WARNING"):
        assert guard_balance(algod, KEEPER, LOW_BALANCE_MICROALGO) == spendable
    assert "10 execution(s) of headroom" in caplog.text


def test_says_nothing_when_comfortably_funded(caplog) -> None:
    algod = FakeAlgod(balance=ACCOUNT_MBR_MICROALGO + LOW_BALANCE_MICROALGO * 2)
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


# --- forgone bonuses --------------------------------------------------
#
# The leak docs/design/asa-fees.md predicted and asked for this warning
# against: an un-opted-in keeper executes normally and takes the full ALGO
# fee, so nothing fails and nothing is logged. The only symptom is earnings
# quietly lower than the board says they should be.


def test_a_bonus_this_keeper_cannot_receive_is_named(caplog) -> None:
    name, value = _box(4)
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND, boxes={name: value}, assets=[])
    with caplog.at_level("WARNING"):
        check_registry(algod, 1, KEEPER)
    assert f"asset {_PINNED.fee_asset}" in caplog.text
    assert "cannot receive" in caplog.text
    # And the number that decides it, not just the fact. The pinned upkeep
    # pays 250,000 base units against a 1,000 µALGO surcharge, so a base unit
    # has to be worth 0.004 µALGO. A format that rounded to two places would
    # print the whole answer as "0.00", which is why this asserts the digits.
    assert "worth more than 0.004 microAlgos" in caplog.text


def test_nothing_is_said_when_the_keeper_is_opted_in(caplog) -> None:
    name, value = _box(4)
    algod = FakeAlgod(
        round=UPKEEP_DUE_ROUND, boxes={name: value}, assets=[_PINNED.fee_asset]
    )
    with caplog.at_level("WARNING"):
        check_registry(algod, 1, KEEPER)
    assert "cannot receive" not in caplog.text


def test_nothing_is_said_when_no_keeper_was_named(caplog) -> None:
    """`--check` is a probe. Without an address there is no keeper to accuse."""
    name, value = _box(4)
    algod = FakeAlgod(round=UPKEEP_DUE_ROUND, boxes={name: value}, assets=[])
    with caplog.at_level("WARNING"):
        check_registry(algod, 1)
    assert "cannot receive" not in caplog.text


def test_account_state_that_cannot_be_read_does_not_fail_the_check() -> None:
    """A node that will not serve account state is not an unhealthy registry.

    The rest of the report is box state and stands on its own, so the warning
    is dropped rather than taking the health check down with it.
    """

    class _NoAccounts(FakeAlgod):
        def account_info(self, address: str) -> dict:
            raise RuntimeError("403 Forbidden")

    name, value = _box(4)
    algod = _NoAccounts(round=UPKEEP_DUE_ROUND, boxes={name: value})
    assert check_registry(algod, 1, KEEPER) == 0
