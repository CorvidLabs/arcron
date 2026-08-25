"""The snapshot's derived numbers must mean what the page says they mean.

The site renders these fields directly, so an error here is published rather
than logged. Everything derived is checked against the same arithmetic the
contract uses.
"""

import pytest

from scripts.keeper_bot import Upkeep
from scripts.testnet_snapshot import _upkeep_json


def upkeep(**overrides: int) -> Upkeep:
    fields = {
        "upkeep_id": 34,
        "target_app": 769823097,
        "interval_rounds": 100,
        "next_execution_round": 1_000,
        "fee_per_execution": 4_000,
        "balance": 120_000,
        "times_executed": 3,
        "policy": 0,
        "fee_cap": 0,
        "last_serviced_round": 900,
        "fee_asset": 0,
        "asset_fee": 0,
        "asset_balance": 0,
    }
    fields.update(overrides)
    return Upkeep(**fields)  # type: ignore[arg-type]


def test_executions_funded_uses_the_fee_that_would_actually_be_charged() -> None:
    """An escalating upkeep buys fewer runs than its base fee suggests."""
    escalating = upkeep(fee_per_execution=4_000, fee_cap=12_000, balance=120_000)
    # One full interval late: the fee has escalated all the way to the cap.
    recorded = _upkeep_json(escalating, current_round=1_100)
    assert recorded["effectiveFeeMicroAlgos"] == 12_000
    assert recorded["executionsFunded"] == 10  # not 30, which the base fee would imply


def test_a_fixed_fee_upkeep_is_priced_at_its_base() -> None:
    recorded = _upkeep_json(upkeep(fee_cap=0), current_round=5_000)
    assert recorded["effectiveFeeMicroAlgos"] == 4_000
    assert recorded["executionsFunded"] == 30


def test_due_in_rounds_never_goes_negative() -> None:
    """An overdue upkeep is due now, not due in minus four hundred rounds."""
    assert _upkeep_json(upkeep(), current_round=1_400)["dueInRounds"] == 0
    assert _upkeep_json(upkeep(), current_round=900)["dueInRounds"] == 100


def test_policy_is_named_rather_than_numbered() -> None:
    assert _upkeep_json(upkeep(policy=0), current_round=1_000)["policy"] == "catch-up"
    assert _upkeep_json(upkeep(policy=1), current_round=1_000)["policy"] == "skip-ahead"


def test_unused_asset_fields_are_null_rather_than_zero() -> None:
    """An ALGO-only upkeep has no fee asset, which is not the same as asset 0."""
    recorded = _upkeep_json(upkeep(), current_round=1_000)
    assert recorded["feeAsset"] is None
    assert recorded["assetFee"] is None
    assert recorded["assetBalance"] is None


def test_an_asa_upkeep_reports_its_asset() -> None:
    recorded = _upkeep_json(
        upkeep(fee_asset=12_345, asset_fee=50, asset_balance=1_000), current_round=1_000
    )
    assert (recorded["feeAsset"], recorded["assetFee"], recorded["assetBalance"]) == (12_345, 50, 1_000)


@pytest.mark.parametrize("balance", [0, 3_999])
def test_an_underfunded_upkeep_funds_no_executions(balance: int) -> None:
    assert _upkeep_json(upkeep(balance=balance), current_round=1_000)["executionsFunded"] == 0
