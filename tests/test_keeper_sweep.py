"""A sweep must never be able to starve the keeper it takes from.

A keeper earns into the account it spends from, so a sweep set too aggressively
does not merely reduce the balance: it stops the executions that were refilling
it, and the keeper cannot earn its way back out. Every test here is about that
one failure, from a different side.

The rest is arithmetic, and the arithmetic is deliberately conservative: the
reserve is floored at the bot's own low balance warning threshold rather than at
the account minimum, because sweeping down to the point where the bot starts
warning every heartbeat is not a sweep, it is a slow outage.
"""

from __future__ import annotations

import pytest

from scripts import keeper_sweep as sweep
from scripts.keeper_bot import EXECUTION_COST_MICROALGO, LOW_BALANCE_MICROALGO


# --- the reserve cannot be talked below the floor ------------------------


def test_a_reserve_of_zero_is_refused() -> None:
    # The flag configures headroom above the floor, never below it.
    assert sweep.reserve_for(0, LOW_BALANCE_MICROALGO) >= LOW_BALANCE_MICROALGO


def test_a_reserve_below_one_execution_is_refused() -> None:
    # Spendable headroom of less than one execution is an account that cannot
    # execute, which is the same outage by a different route.
    assert sweep.reserve_for(1, LOW_BALANCE_MICROALGO) >= EXECUTION_COST_MICROALGO


def test_a_generous_reserve_is_honoured() -> None:
    assert sweep.reserve_for(50_000_000, LOW_BALANCE_MICROALGO) == 50_000_000


def test_the_default_reserve_clears_the_warning_threshold() -> None:
    # A sweep that lands the bot straight into its own low balance warning is
    # working against the thing it is meant to support.
    assert sweep.reserve_for(None, LOW_BALANCE_MICROALGO) >= LOW_BALANCE_MICROALGO


def test_the_floor_follows_a_raised_warning_threshold() -> None:
    # An operator who raises --min-balance has said they want more headroom.
    # The sweep must not quietly undo that.
    raised = 25_000_000
    assert sweep.reserve_for(1_000, raised) >= raised
    assert sweep.reserve_for(None, raised) >= raised


# --- what may actually leave --------------------------------------------


def test_nothing_leaves_when_spendable_equals_the_reserve() -> None:
    assert sweep.sweepable(400_000, 400_000) == 0


def test_nothing_leaves_when_spendable_is_below_the_reserve() -> None:
    assert sweep.sweepable(50_000, 400_000) == 0


def test_the_sweep_pays_for_its_own_fee() -> None:
    # Sweeping the entire surplus would leave the account below the reserve by
    # exactly the transaction fee.
    assert sweep.sweepable(400_000 + sweep.SWEEP_FEE_MICROALGO, 400_000) == 0
    assert sweep.sweepable(500_000, 400_000) == 100_000 - sweep.SWEEP_FEE_MICROALGO


@pytest.mark.parametrize("spendable", [0, 1, 99_999, 100_000, 400_000, 1_000_000, 10**9])
def test_what_is_left_after_any_sweep_still_covers_the_reserve(spendable: int) -> None:
    """The invariant, stated once and checked across the range.

    Whatever the spendable balance, whatever the trigger, what remains after a
    sweep is never below the reserve, and the reserve is never below what the
    bot needs to keep executing. This is the property that makes the feature
    safe to leave running unattended.
    """
    reserve = sweep.reserve_for(None, LOW_BALANCE_MICROALGO)
    amount = sweep.sweepable(spendable, reserve)
    left = spendable - amount - (sweep.SWEEP_FEE_MICROALGO if amount else 0)
    if amount:
        assert left >= reserve >= EXECUTION_COST_MICROALGO
    else:
        assert amount == 0


# --- the two triggers ----------------------------------------------------


def _decide(balance, **kw):
    base = dict(
        reserve=400_000, threshold=None, seconds_since_last=None, every_seconds=None
    )
    return sweep.decide(balance, **{**base, **kw})


def test_no_trigger_configured_sweeps_nothing() -> None:
    # Naming a destination without a trigger is inert, not a sweep-everything.
    decision = _decide(100_000_000)
    assert not decision
    assert "no trigger" in decision.reason


def test_the_threshold_fires_on_its_own() -> None:
    decision = _decide(1_400_000, threshold=500_000)
    assert decision and decision.amount == 1_000_000 - sweep.SWEEP_FEE_MICROALGO


def test_below_the_threshold_nothing_goes() -> None:
    assert not _decide(500_000, threshold=500_000)


def test_the_duration_fires_on_its_own() -> None:
    decision = _decide(500_000, seconds_since_last=3_600, every_seconds=3_600)
    assert decision
    assert "since the last sweep" in decision.reason


def test_the_duration_does_not_fire_early() -> None:
    assert not _decide(500_000, seconds_since_last=60, every_seconds=3_600)


def test_either_trigger_is_enough() -> None:
    # "A threshold or a duration" means or, so a large surplus does not wait
    # for the clock and an elapsed clock does not wait for the surplus.
    assert _decide(9_000_000, threshold=500_000, seconds_since_last=0, every_seconds=86_400)
    assert _decide(500_000, threshold=500_000_000, seconds_since_last=99_999, every_seconds=3_600)


def test_an_elapsed_period_still_sweeps_nothing_when_there_is_no_surplus() -> None:
    # The clock does not manufacture money, and a sweep of nothing would burn
    # a fee every period for ever.
    assert not _decide(400_000, seconds_since_last=99_999, every_seconds=3_600)


# --- assets --------------------------------------------------------------


def test_asset_units_have_no_account_minimum_to_protect() -> None:
    assert sweep.asset_sweepable(1_000, 0) == 1_000
    assert sweep.asset_sweepable(1_000, 250) == 750
    assert sweep.asset_sweepable(100, 250) == 0


class _Algod:
    def __init__(self, assets):
        self._assets = assets

    def account_info(self, address):
        return {"assets": [{"asset-id": a} for a in self._assets]}


def test_a_destination_not_opted_in_is_detected_before_broadcasting() -> None:
    # A rejected asset transfer still costs its fee, so this is asked first.
    assert sweep.destination_holds(_Algod([1, 2]), "DEST", 2) is True
    assert sweep.destination_holds(_Algod([1, 2]), "DEST", 99) is False


def test_an_unreadable_destination_is_treated_as_not_opted_in() -> None:
    class Broken:
        def account_info(self, address):
            raise RuntimeError("node down")

    assert sweep.destination_holds(Broken(), "DEST", 1) is False


# --- the configuration refuses what cannot work --------------------------


class _Args:
    """Just the fields `_validate_sweep` reads."""

    def __init__(self, **kw):
        self.sweep_to = kw.get("sweep_to", "A" * 58)
        self.sweep_above = kw.get("sweep_above", 1_000_000)
        self.sweep_every = kw.get("sweep_every")


def _validate(**kw):
    from scripts.keeper_bot import _validate_sweep

    return _validate_sweep(_Args(**kw), KEEPER)


KEEPER = "E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E"
OTHER = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"


def test_a_valid_configuration_is_accepted() -> None:
    _validate(sweep_to=OTHER, sweep_above=1_000_000)


def test_a_malformed_destination_is_refused_before_the_first_scan() -> None:
    from scripts.keeper_bot import UnrecoverableError

    with pytest.raises(UnrecoverableError, match="not a valid Algorand address"):
        _validate(sweep_to="not-an-address")


def test_sweeping_to_the_keeper_itself_is_refused() -> None:
    """The footgun worth spending an error on.

    It looks like it works: the transaction succeeds, the balance barely
    moves, and nothing warns. It just burns a fee every period for ever.
    """
    from scripts.keeper_bot import UnrecoverableError

    with pytest.raises(UnrecoverableError, match="own address"):
        _validate(sweep_to=KEEPER)


def test_a_destination_with_no_trigger_is_refused() -> None:
    # Naming a destination is a statement of intent. Silently never sweeping
    # would satisfy the letter of the flags and none of the intent.
    from scripts.keeper_bot import UnrecoverableError

    with pytest.raises(UnrecoverableError, match="needs a trigger"):
        _validate(sweep_to=OTHER, sweep_above=None, sweep_every=None)


@pytest.mark.parametrize(
    "trigger",
    [
        {"sweep_above": 0, "sweep_every": None},
        {"sweep_above": None, "sweep_every": 0},
        {"sweep_above": -1, "sweep_every": None},
    ],
)
def test_a_nonpositive_trigger_is_refused(trigger: dict) -> None:
    # Zero is the interesting one: it is not None, so it passes the "needs a
    # trigger" check, and it would then fire on every heartbeat.
    from scripts.keeper_bot import UnrecoverableError

    with pytest.raises(UnrecoverableError, match="must be positive"):
        _validate(sweep_to=OTHER, **trigger)


def test_a_keeper_holding_assets_does_not_have_its_minimum_balance_swept() -> None:
    """The bug a rebase conflict exposed, pinned so it cannot return.

    An account's minimum balance is not a constant. Every asset opt-in adds
    100,000, and so does every app or asset it created. `account_floor`
    measured a live keeper holding eleven bonus assets at 5,439,000 against
    the 100,000 a bare account needs.

    The first version of this module took the *total* balance and subtracted a
    reserve that was assumed to cover the minimum. On that keeper it would
    have offered 5.34 ALGO of untouchable minimum balance as surplus. Working
    from spendable is what makes the reserve mean headroom rather than a
    guess at somebody else's account shape.
    """
    total = 6_000_000
    real_minimum = 5_439_000  # eleven asset opt-ins, measured
    spendable = total - real_minimum  # 561,000

    reserve = sweep.reserve_for(None, LOW_BALANCE_MICROALGO)
    amount = sweep.sweepable(spendable, reserve)

    # Whatever goes, the account keeps its minimum balance and its reserve.
    assert total - amount >= real_minimum + reserve
    # And the naive version really would have been wrong: treating the total
    # as spendable offers most of the minimum balance up.
    naive = sweep.sweepable(total, reserve)
    assert naive > spendable
