"""The allocation check in the ONE draw proof, which got it wrong live.

A ONE ticket's credit persists until it is claimed. The first version of this
check compared absolute allocations against zero, so an account holding an
unclaimed prize from a draw weeks earlier looked like it had just won one, and
the run aborted after the draw and before the close-out. These pin the delta
semantics so that is a test failure rather than another stranded wallet.
"""

import pytest

from scripts.rain_one_draw import check_allocations

A = "E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E"
B = "3NQY7ZHZO6TDNGQODM4MTLGEJSQ3DBO7ZGJUXFXRUDN7H4J6FH2ODTUVT4"
C = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"


def test_the_winner_gains_exactly_the_locked_prize() -> None:
    check_allocations({A: 0, B: 0, C: 0}, {A: 0, B: 50_000, C: 0}, winner=B, locked=50_000)


def test_a_stale_unclaimed_credit_is_not_a_win() -> None:
    # The live failure. A holds 50,000 from an earlier draw it never claimed;
    # B wins this one. Comparing absolutes accused A of winning.
    check_allocations(
        {A: 50_000, B: 0, C: 0}, {A: 50_000, B: 50_000, C: 0}, winner=B, locked=50_000
    )


def test_the_winner_accumulating_a_second_prize_is_still_exact() -> None:
    check_allocations(
        {A: 0, B: 50_000, C: 0}, {A: 0, B: 100_000, C: 0}, winner=B, locked=50_000
    )


def test_a_loser_gaining_anything_is_refused() -> None:
    with pytest.raises(SystemExit, match="gained 1 without winning"):
        check_allocations(
            {A: 0, B: 0, C: 0}, {A: 1, B: 50_000, C: 0}, winner=B, locked=50_000
        )


def test_a_winner_credited_the_wrong_amount_is_refused() -> None:
    with pytest.raises(SystemExit, match="expected exactly 50000"):
        check_allocations({A: 0, B: 0}, {A: 0, B: 49_999}, winner=B, locked=50_000)


def test_a_winner_credited_nothing_is_refused() -> None:
    # The seed said B, the contract credited nobody. Silent without this.
    with pytest.raises(SystemExit, match="gained 0"):
        check_allocations({A: 0, B: 0}, {A: 0, B: 0}, winner=B, locked=50_000)


def test_a_winner_outside_the_watched_set_is_refused() -> None:
    # The index box pointed at an account we never funded or entered. That is
    # a real failure, not a bookkeeping gap, so it must not read as a pass.
    with pytest.raises(SystemExit, match="gained 0"):
        check_allocations({A: 0, B: 0}, {A: 0, B: 0}, winner=C, locked=50_000)
