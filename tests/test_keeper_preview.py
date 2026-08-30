"""What a keeper earns is two numbers, and quoting one of them is the lie.

`execute` pays the caller an inner payment and the caller pays a group fee to
send it. Reporting the fee alone makes 10,000 uALGO look like the answer when
the answer is 7,000, and reporting the registry's total makes 1.4 ALGO a day
look like an income when it is shared four ways. Both mistakes flatter the
project, which is why they are the ones tested for.

The figures are the real TestNet ones from 2026-08-30: 347 executions in
32,000 rounds, 2.454 ALGO gross, 1.410 ALGO net, four keepers.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from scripts.keeper_bot import EXECUTION_COST_MICROALGO
from scripts.keeper_preview import Earnings, Execution, Opportunity, read_executions

SPR = 2.695
LOOKBACK = 32_000


def execution(**over) -> Execution:
    base = dict(round=66_772_767, keeper="GCQL3M7A", paid=10_000, cost=3_000)
    return Execution(**{**base, **over})


class TestOneExecution:
    def test_the_net_is_the_fee_less_what_it_cost_to_send(self) -> None:
        # The real pair from round 66,772,767: paid 10,000, group fee 3,000.
        assert execution().net == 7_000

    def test_the_floor_upkeep_nets_a_thousand_not_four(self) -> None:
        # 4,000 uALGO is MIN_UPKEEP_FEE and the number the docs quote as thin
        # on purpose. Thin means +0.001 ALGO, not +0.004.
        assert execution(paid=4_000).net == 1_000
        assert EXECUTION_COST_MICROALGO == 3_000

    def test_an_execution_can_lose_money_and_is_counted_that_way(self) -> None:
        # A keeper that raised its own outer fee above what the upkeep pays.
        assert execution(paid=4_000, cost=10_000).net == -6_000


class TestEarnings:
    def make(self, n: int = 347) -> Earnings:
        return Earnings([execution() for _ in range(n)], LOOKBACK, SPR)

    def test_gross_and_net_are_both_reported_because_gross_alone_flatters(self) -> None:
        e = self.make()
        assert e.gross == 3_470_000
        assert e.net == 2_429_000
        assert e.net < e.gross

    def test_a_day_is_derived_from_the_lookback_not_assumed(self) -> None:
        assert self.make().days == pytest.approx(0.998, abs=0.01)

    def test_an_empty_window_is_zero_rather_than_a_crash(self) -> None:
        empty = Earnings([], 0, SPR)
        assert empty.gross == 0 and empty.net == 0
        assert empty.per_day == 0.0
        assert empty.share_if_one_more == 0.0

    def test_by_keeper_attributes_net_to_the_account_that_earned_it(self) -> None:
        mixed = Earnings(
            [execution(keeper="BUSY")] * 3 + [execution(keeper="QUIET", paid=4_000)],
            LOOKBACK, SPR,
        )
        assert mixed.by_keeper == [("BUSY", 3, 21_000), ("QUIET", 1, 1_000)]

    def test_the_busiest_keeper_is_first_so_its_view_is_the_one_borrowed(self) -> None:
        mixed = Earnings(
            [execution(keeper="QUIET")] + [execution(keeper="BUSY")] * 9, LOOKBACK, SPR
        )
        assert mixed.by_keeper[0][0] == "BUSY"


class TestTheShareAnArrivalWouldSee:
    """An extra keeper divides the work rather than creating it."""

    def test_arriving_divides_by_one_more_than_are_here(self) -> None:
        four = Earnings(
            [execution(keeper=k) for k in ("A", "B", "C", "D")], LOOKBACK, SPR
        )
        assert four.share_if_one_more == pytest.approx(four.per_day / 5)

    def test_it_is_never_the_headline_number(self) -> None:
        # The failure mode this guards: quoting the registry total to somebody
        # deciding whether to run a keeper.
        four = Earnings(
            [execution(keeper=k) for k in ("A", "B", "C", "D")], LOOKBACK, SPR
        )
        assert four.share_if_one_more < four.per_day

    def test_an_empty_registry_offers_nothing_rather_than_everything(self) -> None:
        assert Earnings([], LOOKBACK, SPR).share_if_one_more == 0.0


class TestOpportunity:
    def test_money_on_a_working_target_is_real(self) -> None:
        assert Opportunity(19, 769891902, pays=10_000, blocked="").real

    def test_the_highest_paying_upkeep_in_the_registry_is_not_earnable(self) -> None:
        """Upkeep 87, exactly.

        It pays 20,000, its fee having escalated to the ceiling, which makes it
        the most attractive row on the board and worth nothing at all. Counting
        it would be the single most misleading number this tool could print.
        """
        upkeep_87 = Opportunity(87, 770082145, pays=20_000, blocked="TARGET REVERTS")
        assert upkeep_87.net == 17_000
        assert not upkeep_87.real

    def test_an_upkeep_that_cannot_pay_its_fee_is_not_earnable(self) -> None:
        assert not Opportunity(98, 770365966, pays=4_000,
                               blocked="ESCROW CANNOT PAY THE FEE").real

    def test_an_upkeep_that_pays_less_than_it_costs_is_not_earnable(self) -> None:
        assert not Opportunity(73, 769891902, pays=EXECUTION_COST_MICROALGO, blocked="").real


class FakeIndexer:
    def __init__(self, transactions): self._t = transactions
    def search_transactions(self, **_): return {"transactions": self._t}


def app_txn(sender: str, selector: bytes, inners: list, fee: int = 3_000, rnd: int = 1) -> dict:
    return {
        "sender": sender, "fee": fee, "confirmed-round": rnd,
        "application-transaction": {
            "application-args": [base64.b64encode(selector).decode()]
        },
        "inner-txns": inners,
    }


def pay(receiver: str, amount: int) -> dict:
    return {"tx-type": "pay", "payment-transaction": {"receiver": receiver, "amount": amount}}


class TestReadExecutions:
    def test_an_execution_is_a_call_to_execute_not_to_the_application(self) -> None:
        """The same mistake `registry_health` was written to stop repeating."""
        register = hashlib.new("sha512_256", b"register(...)").digest()[:4]
        execute = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
        indexer = FakeIndexer([
            app_txn("K", execute, [pay("K", 10_000)]),
            app_txn("K", register, [pay("K", 10_000)]),
        ])
        found = read_executions(indexer, 769891898, 0)
        assert len(found) == 1 and found[0].paid == 10_000

    def test_only_the_payment_to_the_caller_counts_as_the_fee(self) -> None:
        """A rain-style target moves money too; that is not keeper income."""
        execute = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
        indexer = FakeIndexer([
            app_txn("K", execute, [pay("SOMEBODY-ELSE", 1_000_000), pay("K", 10_000)])
        ])
        assert read_executions(indexer, 769891898, 0)[0].paid == 10_000

    def test_the_group_fee_comes_from_the_transaction_itself(self) -> None:
        execute = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
        indexer = FakeIndexer([app_txn("K", execute, [pay("K", 10_000)], fee=4_000)])
        assert read_executions(indexer, 769891898, 0)[0].net == 6_000

    def test_an_execution_with_no_inner_payment_earned_nothing(self) -> None:
        execute = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
        indexer = FakeIndexer([app_txn("K", execute, [])])
        assert read_executions(indexer, 769891898, 0)[0].net == -3_000
