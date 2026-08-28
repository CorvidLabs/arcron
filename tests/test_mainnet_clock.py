"""The hold cannot be reported as running on code that is about to be replaced.

MainNet is gated on sustained TestNet time. The rule is easy to state and easy
to get wrong from memory, because what resets it is not "did anyone edit a
file". Since app 769891898 went live, 98 commits landed and 15 touched
`smart_contracts/`; none of them changed what is on chain, so none reset
anything.

What resets it is a redeploy, and the signal that one is coming is the local
build no longer matching the deployment. The failure this guards against is a
clock that keeps counting through that: reporting 29 days served when the code
those days were served by is about to be thrown away.
"""

from __future__ import annotations

import pytest

from scripts.mainnet_clock import Clock

ROUND_SECONDS = 2.695
DAY_ROUNDS = int(86_400 / ROUND_SECONDS)


def clock(days: float, *, matches: bool = True) -> Clock:
    digest = "a" * 64
    return Clock(
        contract="keeper",
        app_id=769891898,
        created_round=1_000_000,
        current_round=1_000_000 + int(days * DAY_ROUNDS),
        seconds_per_round=ROUND_SECONDS,
        local_digest=digest,
        remote_digest=digest if matches else "b" * 64,
    )


def test_age_is_measured_from_the_deployment_not_from_a_commit() -> None:
    # A commit date says when somebody typed something. The creation round says
    # when this exact contract started being the one holding money.
    assert clock(30).days == pytest.approx(30, abs=0.01)


def test_a_hold_completes_only_when_it_has_actually_elapsed() -> None:
    assert not clock(29.9).complete(30)
    assert clock(30.1).complete(30)


def test_a_hold_never_completes_while_a_redeploy_is_pending() -> None:
    """The whole point of the module.

    A year of uptime on code that is about to be replaced is not a year of
    evidence about the code that will replace it.
    """
    aged = clock(365, matches=False)
    assert aged.days > 30
    assert not aged.complete(30)


def test_remaining_never_goes_negative() -> None:
    # "-4.2 days to go" reads as a bug and invites the reader to distrust the
    # rest of the output.
    assert clock(100).remaining(30) == 0.0


def test_matches_source_is_a_digest_comparison_not_a_guess() -> None:
    assert clock(1).matches_source is True
    assert clock(1, matches=False).matches_source is False


def test_a_brand_new_deployment_reports_zero_rather_than_failing() -> None:
    fresh = clock(0)
    assert fresh.days == pytest.approx(0.0)
    assert fresh.remaining(30) == pytest.approx(30.0)
    assert not fresh.complete(30)


@pytest.mark.parametrize("hold", [0, 1, 30, 60, 90])
def test_the_hold_length_is_the_caller_s_to_choose(hold: int) -> None:
    # The repository says 30 in one place and "30, 60, whatever" in
    # conversation. The script should not be the thing that decides.
    assert clock(45).complete(hold) is (45 >= hold)
