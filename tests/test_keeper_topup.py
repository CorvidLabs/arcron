"""An escrow means nothing until it is divided by a cadence.

The bug this module exists to prevent is not arithmetic, it is a category
error: reading 0.15 ALGO as "nearly empty" or 4 ALGO as "fine" without asking
how often the upkeep is called. Upkeep 91 held 0.148 ALGO and had 1.5 days of
life in it. Upkeeps 98 to 109 would have needed 192 ALGO *each* to reach the
same 30 days, because they run every 54 seconds. Both numbers look ordinary
and mean opposite things.

So the figures pinned here are the real ones from TestNet on 2026-08-29,
rather than round numbers chosen to make the assertions tidy.
"""

from __future__ import annotations

import pytest

from scripts.keeper_bot import Upkeep
from scripts.keeper_topup import (
    DEFAULT_MAX_PER_UPKEEP_MICROALGO,
    Skipped,
    TopUp,
    affordable,
    plan,
    required_balance,
    runway_days,
)

#: TestNet, from `scripts.network.seconds_per_round`.
SPR = 2.695


def upkeep(**over) -> Upkeep:
    """Upkeep 91, the rain hub draw, as it stood before it was topped up."""
    base = dict(
        upkeep_id=91,
        creator="E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E",
        target_app=770130162,
        interval_rounds=1_286,
        next_execution_round=66_802_000,
        fee_per_execution=4_000,
        balance=148_000,
        times_executed=8,
        policy=0,
        fee_cap=0,
        last_serviced_round=66_801_000,
        fee_asset=0,
        asset_fee=0,
        asset_balance=0,
    )
    return Upkeep(**{**base, **over})


class TestRunway:
    def test_upkeep_91_had_a_day_and_a_half(self) -> None:
        # The number that started this: 0.148 ALGO on an hourly draw.
        assert runway_days(148_000, 4_000, 1_286, SPR) == pytest.approx(1.48, abs=0.01)

    def test_the_same_escrow_on_a_54_second_cadence_is_minutes(self) -> None:
        # Upkeep 98's cadence. Identical balance, three orders of magnitude
        # less life, which is the whole point of quoting days.
        assert runway_days(148_000, 4_000, 20, SPR) < 0.03

    def test_only_whole_executions_count(self) -> None:
        # An escrow that cannot pay for one more call has no runway, however
        # many microalgo are stranded in it.
        assert runway_days(3_999, 4_000, 1_286, SPR) == 0.0

    @pytest.mark.parametrize("fee,interval", [(0, 1_286), (4_000, 0), (0, 0)])
    def test_a_missing_price_or_cadence_is_zero_not_a_crash(self, fee: int, interval: int) -> None:
        assert runway_days(148_000, fee, interval, SPR) == 0.0


class TestRequiredBalance:
    def test_it_reproduces_the_top_up_that_was_actually_sent(self) -> None:
        # 2.840 ALGO, txid HLQFJEYMIT4N55BHGMPD62BW52AISM5L7RHRZVWOVSYCUGBLNMLQ.
        assert required_balance(30.0, 4_000, 1_286, SPR) - 148_000 == 2_840_000

    def test_it_reproduces_what_a_54_second_upkeep_would_have_cost(self) -> None:
        # 192 ALGO each, 2,308 for the twelve. The reason they are cancelled
        # rather than funded.
        assert required_balance(30.0, 4_000, 20, SPR) == 192_356_000

    def test_it_inverts_runway_days(self) -> None:
        needed = required_balance(30.0, 4_000, 1_286, SPR)
        assert runway_days(needed, 4_000, 1_286, SPR) == pytest.approx(30.0, abs=0.05)


class TestPlan:
    def test_an_upkeep_already_past_the_target_is_left_alone(self) -> None:
        funding, skipped = plan([upkeep(balance=3_000_000)], SPR, target_days=30.0)
        assert funding == [] and skipped == []

    def test_upkeep_91_is_planned_at_the_amount_that_was_sent(self) -> None:
        funding, skipped = plan([upkeep()], SPR, target_days=30.0)
        assert skipped == []
        assert [(t.upkeep_id, t.microalgo) for t in funding] == [(91, 2_840_000)]
        assert funding[0].days_after == pytest.approx(30.0, abs=0.05)

    def test_an_upkeep_priced_to_burn_is_reported_not_funded(self) -> None:
        """The twelve-upkeep lesson, as an assertion.

        Funding it at all is the mistake: any affordable amount buys hours,
        and the account is emptied for nothing.
        """
        funding, skipped = plan(
            [upkeep(upkeep_id=98, interval_rounds=20, balance=0)], SPR, target_days=30.0
        )
        assert funding == []
        assert len(skipped) == 1 and skipped[0].upkeep_id == 98
        assert skipped[0].microalgo == 192_356_000
        assert "cancel it" in skipped[0].reason

    def test_the_threshold_is_what_separates_the_two(self) -> None:
        hourly, minutely = upkeep(), upkeep(upkeep_id=98, interval_rounds=20, balance=0)
        funding, skipped = plan([hourly, minutely], SPR, target_days=30.0)
        assert [t.upkeep_id for t in funding] == [91]
        assert [s.upkeep_id for s in skipped] == [98]
        assert DEFAULT_MAX_PER_UPKEEP_MICROALGO < 192_356_000

    def test_only_narrows_to_the_named_upkeeps(self) -> None:
        pair = [upkeep(), upkeep(upkeep_id=93, balance=100_000)]
        funding, _ = plan(pair, SPR, target_days=30.0, only={93})
        assert [t.upkeep_id for t in funding] == [93]

    def test_an_upkeep_with_no_cadence_is_skipped_rather_than_divided_by_zero(self) -> None:
        _, skipped = plan([upkeep(interval_rounds=0)], SPR)
        assert len(skipped) == 1 and "nothing to compute" in skipped[0].reason


class TestEscalation:
    def test_no_cap_means_the_two_numbers_agree(self) -> None:
        # Upkeep 91's fee_cap is 0, so its 30.0 days is exact rather than
        # optimistic. Worth pinning: it is why that one was safe to fund once.
        funding, _ = plan([upkeep()], SPR, target_days=30.0)
        assert funding[0].days_after == funding[0].floor_days_after
        assert not funding[0].escalates

    def test_a_cap_shortens_the_floor_without_changing_the_plan(self) -> None:
        """Planning at the ceiling would pay for a failure mode, not a schedule.

        The ceiling is only reached when keepers are already late, so the
        amount stays based on the base fee and the exposure is printed.
        """
        capped = upkeep(fee_cap=8_000)
        funding, _ = plan([capped], SPR, target_days=30.0)
        assert funding[0].microalgo == 2_840_000
        assert funding[0].escalates
        assert funding[0].floor_days_after < funding[0].days_after

    def test_a_cap_below_the_base_fee_never_escalates(self) -> None:
        # `keeper_bot.effective_fee`: a cap at or under the base holds the fee.
        funding, _ = plan([upkeep(fee_cap=1_000)], SPR, target_days=30.0)
        assert not funding[0].escalates


def top_up(upkeep_id: int, microalgo: int, days_now: float) -> TopUp:
    return TopUp(
        upkeep_id=upkeep_id, target_app=770130162, microalgo=microalgo,
        days_now=days_now, days_after=30.0, floor_days_after=30.0,
    )


class TestAffordable:
    def test_a_full_budget_takes_everything_in_id_order(self) -> None:
        chosen, _ = affordable(
            [top_up(91, 2_840_000, 1.5), top_up(89, 2_632_000, 3.6)],
            spendable=10_000_000, reserve=500_000,
        )
        assert [t.upkeep_id for t in chosen] == [89, 91]

    def test_a_short_budget_buys_days_where_they_are_scarcest(self) -> None:
        # 91 has 1.5 days and 89 has 3.6, and only one fits. The neediest
        # wins regardless of id order or amount.
        chosen, _ = affordable(
            [top_up(89, 2_632_000, 3.6), top_up(91, 2_840_000, 1.5)],
            spendable=3_265_000, reserve=100_000,
        )
        assert [t.upkeep_id for t in chosen] == [91]

    def test_an_upkeep_is_never_partially_funded(self) -> None:
        """Half a top-up turns one clear number into two unclear ones."""
        chosen, left = affordable([top_up(91, 2_840_000, 1.5)],
                                  spendable=2_000_000, reserve=0)
        assert chosen == []
        assert left == 2_000_000

    def test_the_reserve_is_never_spent(self) -> None:
        chosen, left = affordable([top_up(91, 2_840_000, 1.5)],
                                  spendable=3_000_000, reserve=500_000)
        assert chosen == [] and left == 2_500_000

    def test_group_fees_are_left_payable(self) -> None:
        # Exactly the top-up and nothing more is not affordable: the call
        # itself costs about 0.002 ALGO.
        assert affordable([top_up(91, 2_840_000, 1.5)], 2_840_000, 0)[0] == []
        assert affordable([top_up(91, 2_840_000, 1.5)], 2_842_000, 0)[0] != []

    def test_an_empty_account_buys_nothing_rather_than_failing(self) -> None:
        chosen, left = affordable([top_up(91, 2_840_000, 1.5)], spendable=0, reserve=500_000)
        assert chosen == [] and left == 0
