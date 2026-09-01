"""An execution is a call to `execute`, not a call to the application.

This module exists because the query was hand-rolled once and got that wrong.
It grouped every application call by sender, and reported an account that had
only ever called `register` as a keeper executing upkeeps. The count was real.
The label was false, and it would have gone into a status report as evidence
that an outsider was running a keeper.

The rest is classification: which upkeeps are worth mentioning and which are
fine. Getting that wrong in the other direction matters too, because a health
report that flags everything is one nobody reads.
"""

from __future__ import annotations

import hashlib

import pytest

from scripts.keeper_bot import BONUS_FEE_MICROALGO, EXECUTION_COST_MICROALGO
from algosdk.logic import get_application_address

from scripts.registry_health import (
    LOW_RUNWAY_DAYS,
    RegistrySolvency,
    UpkeepHealth,
    classify_failure,
    execute_selector,
    read_solvency,
)


def health(**over) -> UpkeepHealth:
    base = dict(
        upkeep_id=1,
        target_app=769891902,
        times_executed=5,
        net_to_keeper=7_000,
        runway_days=30.0,
        rounds_late=0,
        interval_rounds=1_286,
    )
    return UpkeepHealth(**{**base, **over})


def test_the_execute_selector_is_derived_from_the_abi() -> None:
    # Pasted bytes match nothing silently if the signature ever changes. This
    # asserts the derivation, and pins the value so a change is visible.
    expected = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]
    assert execute_selector() == expected
    assert execute_selector().hex() == "5b49cc5c"


def test_the_execute_selector_is_not_the_register_one() -> None:
    """The whole bug, in one assertion.

    `register` is what the account misreported as a keeper had actually called.
    """
    register = hashlib.new(
        "sha512_256",
        b"register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64",
    ).digest()[:4]
    assert execute_selector() != register


class TestPaysNothing:
    def test_an_upkeep_at_the_floor_with_a_bonus_pays_nothing(self) -> None:
        # 4,000 fee, 3,000 to execute, 1,000 more because the bonus transfer is
        # a third inner transaction. Exactly zero, which is what upkeep 73 does.
        net = 4_000 - (EXECUTION_COST_MICROALGO + BONUS_FEE_MICROALGO)
        assert net == 0
        assert health(net_to_keeper=net).pays_nothing

    def test_a_negative_margin_counts_too(self) -> None:
        assert health(net_to_keeper=-500).pays_nothing

    def test_a_single_microalgo_of_margin_does_not(self) -> None:
        # Thin is not the same as nothing, and calling it nothing would be a
        # different claim than the one this makes.
        assert not health(net_to_keeper=1).pays_nothing


class TestRunway:
    def test_below_the_threshold_is_flagged(self) -> None:
        assert health(runway_days=LOW_RUNWAY_DAYS - 0.1).low_runway

    def test_at_the_threshold_is_not(self) -> None:
        assert not health(runway_days=LOW_RUNWAY_DAYS).low_runway


class TestOverdue:
    def test_late_within_one_cycle_is_not_overdue(self) -> None:
        # Losing a race is ordinary. Flagging it would make the report useless
        # on any registry with more than one keeper.
        assert not health(rounds_late=1_285, interval_rounds=1_286).overdue

    def test_late_by_more_than_a_whole_cycle_is(self) -> None:
        assert health(rounds_late=1_287, interval_rounds=1_286).overdue

    def test_a_healthy_upkeep_raises_nothing(self) -> None:
        assert health().flags() == []


def test_flags_accumulate_rather_than_shadow_one_another() -> None:
    # An upkeep can be underpaid *and* nearly out of escrow *and* unserviced,
    # and a report that showed only the first would hide the other two.
    bad = health(net_to_keeper=0, runway_days=1.0, rounds_late=99_999, interval_rounds=1_286)
    assert len(bad.flags()) == 3


@pytest.mark.parametrize("runs", [0, 1, 500])
def test_execution_count_never_affects_the_flags(runs: int) -> None:
    # A busy upkeep and an idle one are judged on the same terms. Runs are
    # reported because they are interesting, not because they are healthy.
    assert health(times_executed=runs).flags() == []


# The real simulate output from TestNet on 2026-08-30. Kept verbatim because
# the point of these tests is what is *absent* from it.
UPKEEP_98_CANNOT_PAY = (
    "transaction M4FZTCFZZQ2FIQOI3HDXAR7IKGPKI5NUX27ZHBJWNXZUBZT2U5GA: logic eval error: "
    "assert failed pc=1181. Details: app=769891898, pc=1181, opcodes=dig 15; >=; assert"
)
UPKEEP_87_TARGET_REVERTS = (
    "transaction TPHZC6Y6Q7AKZMUWL3M26WK22FYSWXKDQ3PQAIPHT23M2QV7JYPA: logic eval error: "
    "inner tx 0 failed: logic eval error: assert failed pc=249. Details: app=770082145, "
    "pc=249, opcodes===; !; assert. Details: app=769891898, pc=1483, opcodes=intc_1 // 0; "
    "itxn_field Fee; itxn_submit; label36:"
)


class TestClassifyFailure:
    """Two upkeeps, both OVERDUE, and only one of them is about money."""

    def test_the_assert_message_is_not_in_the_response(self) -> None:
        """The reason this classifier cannot be a string match.

        `assert upkeep.balance >= fee, "Insufficient funding"` puts that text
        in the source map, not on chain. Matching it would have silently
        classified nothing at all, which is worse than not trying.
        """
        assert "Insufficient funding" not in UPKEEP_98_CANNOT_PAY
        assert "Not due" not in UPKEEP_98_CANNOT_PAY

    def test_an_inner_transaction_failure_is_the_target_reverting(self) -> None:
        assert classify_failure(UPKEEP_87_TARGET_REVERTS) == "TARGET REVERTS"

    def test_a_funded_upkeep_that_reverts_is_not_a_funding_problem(self) -> None:
        # Upkeep 87 exactly: 5.75 ALGO in escrow, fee escalated to its ceiling,
        # and still nothing a top-up can do about it.
        assert classify_failure(UPKEEP_87_TARGET_REVERTS, can_pay_fee=True) == "TARGET REVERTS"

    def test_a_broken_target_that_is_also_broke_is_still_a_broken_target(self) -> None:
        # Order matters: funding one of these buys nothing.
        assert classify_failure(UPKEEP_87_TARGET_REVERTS, can_pay_fee=False) == "TARGET REVERTS"

    def test_the_funding_case_is_decided_by_the_numbers_not_the_text(self) -> None:
        assert classify_failure(UPKEEP_98_CANNOT_PAY, can_pay_fee=False) == (
            "ESCROW CANNOT PAY THE FEE"
        )

    def test_the_same_error_from_a_solvent_upkeep_is_quoted_not_guessed(self) -> None:
        """An unrecognised failure gets repeated back, not filed under a label.

        Same message, solvent upkeep: something else is wrong and the report
        has no business naming it.
        """
        verdict = classify_failure(UPKEEP_98_CANNOT_PAY, can_pay_fee=True)
        assert verdict.startswith("WOULD FAIL: ")
        assert "pc=1181" in verdict

    def test_a_passing_simulation_says_nothing(self) -> None:
        assert classify_failure("") == ""
        assert classify_failure("", can_pay_fee=False) == ""

    def test_the_quoted_form_stops_at_the_details(self) -> None:
        # The Details section is pc noise; the first clause is the sentence.
        assert "Details" not in classify_failure(UPKEEP_98_CANNOT_PAY, can_pay_fee=True)


class TestBlockedFlag:
    def test_a_reason_reaches_the_flags(self) -> None:
        flags = health(rounds_late=10_000, blocked="TARGET REVERTS").flags()
        assert "OVERDUE BY 10,000 ROUNDS" in flags
        assert "TARGET REVERTS" in flags

    def test_no_simulation_adds_no_flag(self) -> None:
        # Empty means "not asked", which must not read as "would succeed".
        assert health(rounds_late=10_000).flags() == ["OVERDUE BY 10,000 ROUNDS"]

    def test_an_on_schedule_upkeep_is_never_flagged_by_this(self) -> None:
        assert health(rounds_late=0, blocked="").flags() == []


class TestRegistrySolvency:
    """The app account must be able to pay out every µALGO its boxes promise.

    Found by the 2026-09-01 audit, on LocalNet, against a keeper created
    without its base minimum balance: `register` charges the box MBR exactly
    and `opt_in_asset` charges the holding deposit exactly, but nothing ever
    charges the 0.1 ALGO the account itself needs. A creator who overpaid the
    box MBR to get past that got a box saying 120,000 with 57,900 spendable
    behind it; the 15th execution and then `cancel` were refused by the ledger
    ("balance 37900 below min 100000"), until a stranger donated 0.1 ALGO.
    `deploy_config` funds it; `govern create`, the MainNet path, only says to.
    This report is where an operator would look, and it did not look.
    """

    def test_a_registry_whose_base_mbr_was_never_funded_is_short(self) -> None:
        # Exactly the LocalNet numbers: MBR overpaid to 100,000 against a
        # required 62,100, 120,000 of funding, one box.
        solvency = RegistrySolvency(amount=220_000, min_balance=162_100, escrowed=120_000)
        assert solvency.spendable == 57_900
        assert solvency.shortfall == 62_100
        assert solvency.flags() == ["THE APP CANNOT PAY OUT 62,100 uALGO IT HOLDS IN ESCROW"]

    def test_a_funded_registry_has_no_shortfall(self) -> None:
        # The same registry after the 0.1 ALGO arrives from anyone at all.
        solvency = RegistrySolvency(amount=320_000, min_balance=162_100, escrowed=120_000)
        assert solvency.shortfall == 0
        assert solvency.flags() == []

    def test_surplus_is_not_a_problem(self) -> None:
        # Overpaid MBR and opt-in deposits only ever accumulate. That is fine.
        solvency = RegistrySolvency(amount=1_000_000, min_balance=162_100, escrowed=120_000)
        assert solvency.shortfall == 0

    def test_solvency_is_read_from_the_app_account(self) -> None:
        class Algod:
            def account_info(self, address: str) -> dict:
                assert address == get_application_address(769891898)
                return {"amount": 220_000, "min-balance": 162_100}

        solvency = read_solvency(Algod(), 769891898, 120_000)
        assert solvency == RegistrySolvency(amount=220_000, min_balance=162_100, escrowed=120_000)

    def test_a_node_that_does_not_report_min_balance_is_assumed_to_be_at_the_floor(self) -> None:
        """Same fallback the keeper report has used since it was written.

        Guessing low is the safe direction: it can only make the report say
        there is more spendable than there is, which is caught by the next
        thing that actually tries to spend it.
        """
        class Algod:
            def account_info(self, address: str) -> dict:
                return {"amount": 500_000}

        assert read_solvency(Algod(), 1, 0).min_balance == 100_000
