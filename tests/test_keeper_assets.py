"""The ASA bonus economics an operator decides on.

Every upkeep here is built from the same recorded box `test_keeper_bot.py`
pins, with only the fields under test changed, so no figure below is a
hand-written guess at what the contract writes.

The claim these exist to protect is one line: opting in to an asset is worth
`bonus - 1,000` microAlgos per execution, and 1,000 of that is not optional.
Everything else is presentation.
"""

import pytest

from scripts.keeper_assets import (
    OPT_IN_ROUND_TRIP_MICROALGO,
    SURCHARGE_MICROALGO,
    AssetInfo,
    describe,
    describe_asset,
    executions_per_day,
    forgone,
    positions,
)
from scripts.keeper_bot import BONUS_FEE_MICROALGO, Upkeep, _decode_upkeep, effective_fee
from tests.test_keeper_bot import LIVE_BOX_HEX

PINNED = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
ASSET = PINNED.fee_asset
BONUS = PINNED.asset_fee


def upkeep(upkeep_id: int, **changes) -> Upkeep:
    """The recorded upkeep, with named fields moved."""
    from dataclasses import replace

    return replace(PINNED, upkeep_id=upkeep_id, **changes)


# --- the surcharge is the whole decision ------------------------------


def test_the_surcharge_matches_what_the_bot_actually_pays() -> None:
    """Two constants, one fact.

    `keeper_bot.BONUS_FEE_MICROALGO` is what the bot adds to the group fee
    when it can receive a bonus; `SURCHARGE_MICROALGO` is what this module
    tells an operator that costs them. They are the same number, and a report
    quoting a break-even the bot does not charge would be worse than no report.
    """
    assert SURCHARGE_MICROALGO == BONUS_FEE_MICROALGO


def test_break_even_is_the_surcharge_divided_by_the_bonus() -> None:
    """The formula the whole report exists to deliver.

    A 250,000 base-unit bonus against a 1,000 microAlgo surcharge means each
    base unit has to be worth 0.004 microAlgos, so a whole unit of a 6-decimal
    asset has to be worth 4,000 microAlgos, which is 0.004 ALGO.
    """
    (position,) = positions([upkeep(1)])

    assert position.break_even_micro_algo_per_unit == SURCHARGE_MICROALGO / BONUS
    assert position.break_even_micro_algo_per_unit == pytest.approx(0.004)
    whole_unit = position.break_even_micro_algo_per_unit * 10**6
    assert whole_unit == pytest.approx(4_000)


def test_a_bonus_worth_exactly_the_surcharge_earns_nothing() -> None:
    """The boundary the operator is being asked about.

    At the break-even the opt-in collects precisely what it costs, forever: it
    never repays even the two transaction fees it took to open and close.
    """
    (position,) = positions([upkeep(1)])
    at_break_even = position.break_even_micro_algo_per_unit

    assert position.net_micro_algo_per_day(at_break_even) == pytest.approx(0)
    assert position.days_to_repay(at_break_even) is None


def test_a_bonus_below_the_surcharge_is_a_permanent_tax() -> None:
    """Not a missed opportunity: a cost that recurs for as long as it stands.

    This is the case the report exists to stop an operator walking into, and
    the reason opting in cannot be automatic.
    """
    (position,) = positions([upkeep(1)])
    half = position.break_even_micro_algo_per_unit / 2

    assert position.net_micro_algo_per_day(half) < 0
    assert position.days_to_repay(half) is None


def test_a_bonus_above_the_surcharge_repays_its_round_trip() -> None:
    (position,) = positions([upkeep(1)])
    # Twice the break-even: each execution nets exactly the surcharge again.
    double = position.break_even_micro_algo_per_unit * 2
    earned_per_day = position.net_micro_algo_per_day(double)

    assert earned_per_day == pytest.approx(position.surcharge_per_day)
    assert position.days_to_repay(double) == pytest.approx(
        OPT_IN_ROUND_TRIP_MICROALGO / earned_per_day
    )


# --- what is actually accruing ----------------------------------------


def test_only_upkeeps_that_can_pay_a_bonus_are_projected() -> None:
    """Counted, but not projected.

    An upkeep out of bonus escrow pays nothing, and one out of ALGO escrow is
    not executed at all, so neither accrues. Both still name the asset, and an
    operator deciding whether to opt in wants to see that they exist.
    """
    found = positions(
        [
            upkeep(1),
            upkeep(2, asset_balance=BONUS - 1),  # cannot cover its own bonus
            upkeep(3, balance=PINNED.fee_per_execution - 1),  # cannot pay its fee
        ]
    )

    (position,) = found
    assert position.upkeeps == 3
    assert position.live_upkeep_ids == (1,)
    assert position.units_per_day == pytest.approx(
        BONUS * executions_per_day(PINNED.interval_rounds)
    )


def test_an_asset_with_nothing_live_has_no_break_even() -> None:
    """None, not zero. There is no bonus to price, so there is no price."""
    (position,) = positions([upkeep(1, asset_balance=0)])

    assert position.live == 0
    assert position.units_per_day == 0
    assert position.break_even_micro_algo_per_unit is None
    assert position.units_per_execution is None
    assert "accrual:       nothing" in "\n".join(
        describe(position, AssetInfo(ASSET, "TEST", 6))
    )


def test_the_mean_bonus_is_weighted_by_how_often_each_upkeep_runs() -> None:
    """A flat mean would hide the upkeep that decides the economics.

    One upkeep paying a large bonus once a month and one paying a small bonus
    every ten rounds are not two data points of equal weight: almost every
    execution, and so almost every surcharge, comes from the frequent one.
    """
    found = positions(
        [
            upkeep(1, interval_rounds=10, asset_fee=1_000, asset_balance=10**9),
            upkeep(2, interval_rounds=1_000_000, asset_fee=10**6, asset_balance=10**9),
        ]
    )

    (position,) = found
    assert position.units_per_execution == pytest.approx(1_000, rel=0.01)
    # The rare upkeep is present, and it moves the mean by well under a percent.
    assert position.live == 2
    assert position.units_per_execution > 1_000


def test_escrow_runway_counts_bonuses_left_rather_than_units() -> None:
    """How many more times this can pay, which is what runs out."""
    (position,) = positions([upkeep(1), upkeep(2, asset_balance=BONUS)])

    assert position.remaining_bonuses == PINNED.asset_balance // BONUS + 1
    assert position.escrowed_units == PINNED.asset_balance + BONUS


def test_the_same_runway_is_a_fortnight_or_three_minutes_by_cadence() -> None:
    """A count of bonuses says nothing until you know how fast they are spent.

    Two upkeeps with identical escrows, one daily and one every ten rounds.
    An asset accruing handsomely for another few minutes is not worth an
    opt-in, and the count alone cannot tell an operator that.
    """
    escrow = 14 * BONUS
    daily = executions_per_day(30_857)  # about one execution a day
    (slow,) = positions([upkeep(1, interval_rounds=30_857, asset_balance=escrow)])
    (fast,) = positions([upkeep(1, interval_rounds=10, asset_balance=escrow)])

    assert slow.remaining_bonuses == fast.remaining_bonuses == 14
    assert slow.runway_days == pytest.approx(14 / daily, rel=0.01)
    assert slow.runway_days > 13
    assert fast.runway_days < 0.005  # minutes
    assert "minutes" in "\n".join(describe(fast, AssetInfo(ASSET, "CORVID", 6)))


def test_a_break_even_below_one_microalgo_is_not_rounded_away() -> None:
    """The whole answer, for any asset with decimals.

    A six-decimal asset's break-even is 0.004 microAlgos per base unit, and
    printing that as "0 microAlgos" would be worse than printing nothing.
    """
    (position,) = positions([upkeep(1)])
    lines = "\n".join(describe(position, AssetInfo(ASSET, "CORVID", 6)))

    assert "0.004 microAlgos per base unit" in lines
    assert "0 microAlgos per base unit" not in lines


# --- grouping ---------------------------------------------------------


def test_algo_only_upkeeps_are_not_asset_positions() -> None:
    assert positions([upkeep(1, fee_asset=0)]) == []


def test_assets_are_ranked_by_what_they_accrue() -> None:
    """An operator reads this to pick what to opt in to next.

    Registry order answers a different question, and the asset paying most is
    the one the decision is about.
    """
    found = positions(
        [
            upkeep(1, fee_asset=ASSET, asset_fee=1),
            upkeep(2, fee_asset=ASSET + 1, asset_fee=BONUS),
        ]
    )

    assert [position.asset_id for position in found] == [ASSET + 1, ASSET]


def test_opt_in_status_is_unknown_rather_than_false_without_a_keeper() -> None:
    """The distinction the report has to keep.

    "This keeper is not opted in" is a finding. "Nobody said which keeper" is
    not, and reporting it as False would invent a forgone bonus.
    """
    (unknown,) = positions([upkeep(1)])
    (missing,) = positions([upkeep(1)], opted_in=[])
    (held,) = positions([upkeep(1)], opted_in=[ASSET])

    assert unknown.opted_in is None
    assert missing.opted_in is False
    assert held.opted_in is True
    assert forgone([unknown]) == []
    assert forgone([missing]) == [missing]
    assert forgone([held]) == []


def test_an_asset_paying_nothing_is_not_forgone() -> None:
    """Nothing is being left behind when nothing is being paid."""
    (position,) = positions([upkeep(1, asset_balance=0)], opted_in=[])

    assert position.opted_in is False
    assert forgone([position]) == []


# --- the liveness shortcut --------------------------------------------


def test_the_base_fee_decides_liveness_at_every_round() -> None:
    """Why `positions` needs no current round.

    Whether an upkeep can pay its ALGO fee is `balance >= effective_fee`, and
    this asserts that is the same question as `balance >= fee_per_execution`
    whatever the round: `keeper_bot.effective_fee` drops back to the base fee
    whenever the escrow cannot cover the escalated one, so the escalated fee
    is never what makes an upkeep unaffordable.

    Pinned here because it is a property of the escalation rather than of
    anything in `keeper_assets`, and it would stop holding silently: an
    escalation that no longer clamped would leave this module quietly
    projecting accrual from upkeeps that can never run.
    """
    for balance in (0, 3_999, 4_000, 4_001, 20_000, 39_999, 40_000):
        for current in (0, 5, 10, 20, 100, 10_000):
            candidate = upkeep(
                1,
                fee_per_execution=4_000,
                fee_cap=40_000,
                last_serviced_round=0,
                balance=balance,
            )
            assert (candidate.balance >= effective_fee(candidate, current)) == (
                candidate.balance >= candidate.fee_per_execution
            ), f"balance {balance} at round {current}"


# --- reading the asset ------------------------------------------------


class _NoAssets:
    def asset_info(self, asset_id: int) -> dict:
        raise RuntimeError("this node does not serve asset params")


def test_an_asset_that_cannot_be_named_still_gets_a_report() -> None:
    """Best effort, deliberately.

    The economics do not depend on what the asset is called, and a destroyed
    asset or a node that will not answer must not take the report down with it.
    """
    info = describe_asset(_NoAssets(), ASSET)

    assert info.decimals == 0
    assert str(ASSET) in info.unit_name


def test_a_named_asset_reports_amounts_in_its_own_decimals() -> None:
    class _Algod:
        def asset_info(self, asset_id: int) -> dict:
            return {"params": {"unit-name": "CORVID", "decimals": 6}}

    info = describe_asset(_Algod(), ASSET)

    assert info.label == "CORVID"
    assert info.amount(BONUS) == "0.250000 CORVID"


def test_the_report_leads_with_the_break_even() -> None:
    """A report ends on a decision or it is a table of facts.

    "Accrues 3 units a day" leaves arithmetic to do; "each unit must be worth
    more than 0.004 ALGO" is the number an operator can take to a price.
    """
    (position,) = positions([upkeep(1)], opted_in=[])
    lines = "\n".join(describe(position, AssetInfo(ASSET, "CORVID", 6)))

    assert "BREAK EVEN" in lines
    assert "0.004 ALGO" in lines
    assert "opted in:      no" in lines
    assert f"{SURCHARGE_MICROALGO:,} per execution, unavoidable once opted in" in lines
