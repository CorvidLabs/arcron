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

import base64
import hashlib

import pytest

from scripts.keeper_bot import BONUS_FEE_MICROALGO, EXECUTION_COST_MICROALGO
from algosdk.logic import get_application_address
from algosdk.v2client.algod import AlgodClient

from scripts.registry_health import (
    ARC4_RETURN_PREFIX,
    LOW_RUNWAY_DAYS,
    RegistrySolvency,
    UpkeepHealth,
    classify_failure,
    execute_selector,
    read_escrowed,
    read_solvency,
    read_upkeeps,
    reports_no_work,
)
from tests.test_keeper_bot import LIVE_BOX_HEX


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

    def test_a_node_that_does_not_report_min_balance_is_refused_not_guessed(self) -> None:
        """The floor is a lower bound, so assuming it answers the wrong way.

        `min-balance` is at least 100,000 and rises by the exact MBR of every
        box the app holds; on the registry above it is 162,100. Substituting
        the floor would have called that account 400,000 spendable instead of
        237,900, and a registry 62,100 short would have printed as solvent —
        the one failure this check was added to catch. An answer that can only
        be wrong in that direction is worth less than no answer.
        """
        class Algod:
            def account_info(self, address: str) -> dict:
                return {"amount": 220_000}

        with pytest.raises(ValueError) as raised:
            read_solvency(Algod(), 769891898, 120_000)
        assert "min-balance" in str(raised.value)

    def test_the_refusal_says_what_it_will_not_assume(self) -> None:
        # Whoever reads this is holding a node that answered short, and needs
        # to know the report stopped rather than rounded.
        class Algod:
            def account_info(self, address: str) -> dict:
                return {"amount": 220_000}

        with pytest.raises(ValueError) as raised:
            read_solvency(Algod(), 769891898, 120_000)
        assert "100,000" in str(raised.value)
        assert "769891898" in str(raised.value)


class TestEveryBoxIsCounted:
    """The escrow sum is only as good as the box list it is summed from.

    `read_upkeeps` took a single `limit=1_000` page of boxes and called it the
    registry. At the 33 upkeeps live on TestNet that is the registry, so the
    solvency check shipped correct and would have stayed correct right up to
    the point where it mattered. Past a thousand boxes the dropped tail comes
    off the *liability* side of the comparison, so the app looks like it owes
    less than it does and an insolvent registry prints as healthy: the same
    direction of error as guessing the ledger's floor low.

    `keeper_bot.scan_upkeeps` has paged with the `next-token` since it was
    written. These assert that this module now uses that one rather than a
    second copy that can drift away from it again.
    """

    @staticmethod
    def _box(upkeep_id: int, balance: int) -> tuple[bytes, bytes]:
        # The recorded LocalNet box, with only the escrow moved, so each box
        # carries a number that says which page it came from.
        raw = bytearray(bytes.fromhex(LIVE_BOX_HEX))
        raw[66:74] = balance.to_bytes(8, "big")
        return b"u" + upkeep_id.to_bytes(8, "big"), bytes(raw)

    class PagedAlgod(AlgodClient):
        """An algod that hands back the box list a page at a time, as a node does.

        **Subclasses the real `AlgodClient` and stubs only `algod_request`,
        the one method that reaches the network.** An earlier version of this
        fake defined `application_boxes(self, app_id, **kwargs)` and accepted
        a `next` keyword, which the real client does not: it builds that
        call's query string from `limit` alone and forwards everything else to
        `algod_request`, which has no such parameter. So the reader under test
        passed here and raised `TypeError` against a node, and the bug the
        pagination was added to fix would have become a crash instead of an
        undercount. Grok 4.6 found it by checking the fake against the client
        the production path uses, which is the only way this class of mistake
        is ever found.

        The page token is opaque to the caller, so it is just the index of the
        next page here; what matters is that the caller has to follow it, and
        has to do so through a real client's real signature.
        """

        def __init__(self, pages: list[list[tuple[bytes, bytes]]]) -> None:
            self.pages = pages
            self.asked_for: list[str | None] = []
            self.values = {name: raw for page in pages for name, raw in page}

        def _page(self, token: "str | None") -> dict:
            self.asked_for.append(token)
            index = 0 if token is None else int(token)
            page: dict = {
                "boxes": [
                    {"name": base64.b64encode(name).decode()}
                    for name, _ in self.pages[index]
                ]
            }
            if index + 1 < len(self.pages):
                page["next-token"] = str(index + 1)
            return page

        def application_boxes(self, app_id: int, limit: int = 0, **kwargs) -> dict:
            # The real signature, which takes no `next`: a reader that tries to
            # continue through this method is wrong, and must fail here.
            assert not kwargs, f"application_boxes takes no {sorted(kwargs)}"
            return self._page(None)

        def algod_request(self, method, requrl, params=None, **kwargs):
            assert "/boxes" in requrl, f"unexpected request {method} {requrl}"
            return self._page((params or {}).get("next"))

        def application_box_by_name(self, app_id: int, name: bytes) -> dict:
            return {"value": base64.b64encode(self.values[name]).decode()}

    def test_a_registry_that_spans_two_pages_is_read_whole(self) -> None:
        algod = self.PagedAlgod([
            [self._box(1, 10_000), self._box(2, 20_000)],
            [self._box(3, 30_000)],
        ])

        upkeeps = read_upkeeps(algod, 769891898, 2.8, 15_055)

        assert [u.upkeep_id for u in upkeeps] == [1, 2, 3]
        assert algod.asked_for == [None, "1"], "the second page was never asked for"

    def test_the_escrow_sum_includes_the_boxes_on_later_pages(self) -> None:
        # The assertion the old single-page read would have failed: 60,000 owed
        # against 40,000 seen is a 20,000 hole in the wrong direction.
        algod = self.PagedAlgod([
            [self._box(1, 10_000), self._box(2, 30_000)],
            [self._box(3, 20_000)],
        ])
        assert read_escrowed(algod, 769891898) == 60_000

    def test_a_page_of_boxes_that_are_not_upkeeps_is_still_filtered(self) -> None:
        # Paging must not smuggle past the name check: anyone can pay for a box
        # under a name of their choosing, and only `u`-prefixed ones are ours.
        algod = self.PagedAlgod([
            [(b"config", bytes.fromhex(LIVE_BOX_HEX)), self._box(1, 10_000)],
            [self._box(2, 20_000)],
        ])
        assert [u.upkeep_id for u in read_upkeeps(algod, 769891898, 2.8, 15_055)] == [1, 2]
        assert read_escrowed(algod, 769891898) == 30_000

    def test_a_registry_that_fits_on_one_page_costs_one_request(self) -> None:
        # A missing token ends the walk. A pager that treats it as "start over"
        # would page forever against the live registry, which serves one page.
        algod = self.PagedAlgod([[self._box(1, 10_000)]])
        assert len(read_upkeeps(algod, 769891898, 2.8, 15_055)) == 1
        assert algod.asked_for == [None]


class TestATargetThatDoesNothing:
    """The blind spot four upkeeps fell into before anything reported it.

    73, 79, 91 and 113 all paid a keeper on schedule to call a target with no
    work to do. Every other reading was green: the target simulated clean, so
    not TARGET REVERTS; the escrow paid, so not the funding case; the keeper
    was paid, so not PAYS THE KEEPER NOTHING. `rain` hub 770746178 declined
    sixty-three consecutive scheduled draws this way.
    """

    @staticmethod
    def _group(return_value: bytes | None, failure: str = ""):
        """A simulate group whose inner app call logged an ARC-4 return."""
        logs = []
        if return_value is not None:
            logs = [base64.b64encode(ARC4_RETURN_PREFIX + return_value).decode()]
        return {
            "failure-message": failure,
            "txn-results": [{
                "txn-result": {
                    "inner-txns": [
                        {"application-index": 770746178, "logs": logs},
                        # The keeper's fee payment, which logs nothing.
                        {"payment-transaction": {"amount": 1_000}},
                    ]
                }
            }],
        }

    def test_a_target_returning_zero_is_reported(self) -> None:
        assert reports_no_work(self._group((0).to_bytes(8, "big"))) is True

    def test_a_target_that_did_something_is_not(self) -> None:
        assert reports_no_work(self._group((1).to_bytes(8, "big"))) is False
        assert reports_no_work(self._group((63).to_bytes(8, "big"))) is False

    def test_a_target_returning_nothing_is_left_alone(self) -> None:
        """Void is not zero. A convention this cannot read is not a finding."""
        assert reports_no_work(self._group(None)) is False

    def test_a_return_that_is_not_eight_bytes_is_left_alone(self) -> None:
        """A string or a struct says nothing about how much work was done."""
        assert reports_no_work(self._group(b"nothing")) is False
        assert reports_no_work(self._group(b"")) is False

    def test_no_inner_calls_at_all_is_not_idle(self) -> None:
        assert reports_no_work({"txn-results": [{"txn-result": {}}]}) is False
        assert reports_no_work({}) is False

    def test_the_flag_says_what_it_saw(self) -> None:
        upkeep = health(rounds_late=5, interval_rounds=1_286, idle_target=True)
        assert "TARGET REPORTS NO WORK" in upkeep.flags()

    def test_an_upkeep_doing_work_carries_no_such_flag(self) -> None:
        upkeep = health(rounds_late=5, interval_rounds=1_286)
        assert "TARGET REPORTS NO WORK" not in upkeep.flags()

    def test_it_is_separate_from_every_other_reading(self) -> None:
        """The point of the flag: an idle upkeep is otherwise perfect.

        No revert, escrow pays, keeper paid, on schedule. Without this the
        line reads clean, which is what it did for four upkeeps.
        """
        upkeep = health(rounds_late=5, interval_rounds=1_286, idle_target=True)
        assert upkeep.flags() == ["TARGET REPORTS NO WORK"]
