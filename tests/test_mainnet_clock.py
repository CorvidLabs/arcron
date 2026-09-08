"""The hold cannot be reported as running on code that is about to be replaced,
nor on code whose install round nobody can date.

MainNet is gated on sustained TestNet time. The rule is easy to state and easy
to get wrong from memory, because what resets it is not "did anyone edit a
file". Since app 769891898 went live, 98 commits landed and 15 touched
`smart_contracts/`; none of them changed what is on chain, so none reset
anything.

What resets it is new programs, by a new app id or by an in-place update. The
first failure this guards against is a clock that keeps counting through a
pending redeploy: reporting 29 days served when the code those days were
served by is about to be thrown away. The second (#250, F03) is a clock that
counts from the create after an update: alpha-3 replaced alpha-2's programs the
day after the create, and the old clock credited alpha-3 with alpha-2's day.
"""

from __future__ import annotations

import logging

import pytest

from scripts import mainnet_clock
from scripts.mainnet_clock import (
    Clock,
    HistoryUnknown,
    InstallHistory,
    measure,
    read_install_history,
    report,
)

ROUND_SECONDS = 2.695
DAY_ROUNDS = int(86_400 / ROUND_SECONDS)
APP_ID = 769891898
CREATED = 1_000_000
DIGEST = "a" * 64
OTHER = "b" * 64


def rounds_ago(now: int, days: float) -> int:
    return now - int(days * DAY_ROUNDS)


def clock(
    days: float,
    *,
    matches: bool = True,
    app_days: float | None = None,
    updates_days_ago: tuple[float, ...] = (),
    history_error: str = "",
) -> Clock:
    """A clock whose programs were installed `days` ago.

    By default that is also the create. Pass `app_days` and `updates_days_ago`
    to make it an app that has been updated since; the latest update is what
    `days` then has to agree with.
    """
    now = CREATED + int((app_days if app_days is not None else days) * DAY_ROUNDS)
    history: InstallHistory | None
    if history_error:
        history = None
    else:
        history = InstallHistory(
            created_round=CREATED,
            update_rounds=tuple(sorted(rounds_ago(now, d) for d in updates_days_ago)),
        )
    return Clock(
        contract="keeper",
        app_id=APP_ID,
        current_round=now,
        seconds_per_round=ROUND_SECONDS,
        local_digest=DIGEST,
        remote_digest=DIGEST if matches else OTHER,
        history=history,
        history_error=history_error,
    )


# --- the Clock itself -------------------------------------------------------


def test_age_is_measured_from_the_deployment_not_from_a_commit() -> None:
    # A commit date says when somebody typed something. The install round says
    # when this exact code started being the code holding money.
    assert clock(30).days == pytest.approx(30, abs=0.01)


def test_a_hold_completes_only_when_it_has_actually_elapsed() -> None:
    assert not clock(29.9).complete(30)
    assert clock(30.1).complete(30)


def test_a_hold_never_completes_while_a_redeploy_is_pending() -> None:
    """A year of uptime on code that is about to be replaced is not a year of
    evidence about the code that will replace it."""
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
    assert fresh.app_days == pytest.approx(0.0)
    assert fresh.remaining(30) == pytest.approx(30.0)
    assert not fresh.complete(30)


@pytest.mark.parametrize("hold", [0, 1, 30, 60, 90])
def test_the_hold_length_is_the_caller_s_to_choose(hold: int) -> None:
    # The repository says 30 in one place and "30, 60, whatever" in
    # conversation. The script should not be the thing that decides.
    assert clock(45).complete(hold) is (45 >= hold)


def test_never_updated_code_is_as_old_as_its_app() -> None:
    unchanged = clock(40)
    assert unchanged.app_days == pytest.approx(40, abs=0.01)
    assert unchanged.days == pytest.approx(40, abs=0.01)
    assert unchanged.installed_round == CREATED
    assert unchanged.update_count == 0
    assert unchanged.complete(30)


def test_an_update_restarts_the_program_age_and_not_the_app_age() -> None:
    """F03. Alpha-3 was alpha-2's app with new programs one day later.

    The old clock read the create and credited the new programs with the old
    ones' day. The boxes are 40 days old; the code in front of them is one.
    """
    updated = clock(1, app_days=40, updates_days_ago=(1,))
    assert updated.app_days == pytest.approx(40, abs=0.01)
    assert updated.days == pytest.approx(1, abs=0.01)
    assert updated.update_count == 1
    assert not updated.complete(30)
    assert updated.remaining(30) == pytest.approx(29, abs=0.01)


def test_a_round_trip_back_to_the_same_bytes_is_not_continuity() -> None:
    """A -> B -> A. The digest matches the one from a month ago; the month
    between was served by B, and the install of A again is what dates it."""
    round_trip = clock(2, app_days=40, updates_days_ago=(20, 2))
    assert round_trip.matches_source  # A is what is deployed, and what we built
    assert round_trip.days == pytest.approx(2, abs=0.01)
    assert round_trip.update_count == 2
    assert not round_trip.complete(30)


def test_unknown_history_is_not_a_number_and_never_completes() -> None:
    unknown = clock(400, history_error="the indexer could not be searched")
    assert unknown.history_known is False
    assert unknown.days is None
    assert unknown.app_days is None
    assert unknown.installed_round is None
    assert unknown.remaining(30) == 30.0
    assert not unknown.complete(30)
    assert not unknown.complete(0)  # even a zero-day hold needs a date


# --- reading the history from the indexer -----------------------------------


def appl(round_: int, *, on_completion: str = "noop", app_id: int = APP_ID, create: bool = False) -> dict:
    """An indexer application transaction, shaped as the indexer returns it."""
    txn: dict = {
        "confirmed-round": round_,
        "tx-type": "appl",
        "application-transaction": {
            "application-id": 0 if create else app_id,
            "on-completion": on_completion,
        },
    }
    if create:
        txn["created-application-index"] = app_id
    return txn


class FakeIndexer:
    """Pages of `search_transactions`, handed out by `next-token`.

    Records what it was asked, so a test can check the search was the one the
    module promises: application transactions against the app, paged to the
    end.
    """

    def __init__(self, pages: list[list[dict]], *, fail: Exception | None = None) -> None:
        self.pages = pages
        self.fail = fail
        self.calls: list[dict] = []

    def search_transactions(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        token = kwargs.get("next_page")
        index = 0 if token is None else int(token)
        page: dict = {"transactions": list(self.pages[index]), "current-round": 9_999_999}
        if index + 1 < len(self.pages):
            page["next-token"] = str(index + 1)
        return page


class FakeAlgod:
    def __init__(self, last_round: int, approval: bytes = b"A", clear: bytes = b"C") -> None:
        self.last_round = last_round
        self.approval, self.clear = approval, clear

    def application_info(self, app_id: int) -> dict:
        import base64
        return {"params": {
            "approval-program": base64.b64encode(self.approval).decode(),
            "clear-state-program": base64.b64encode(self.clear).decode(),
        }}

    def status(self) -> dict:
        return {"last-round": self.last_round}


@pytest.fixture
def local_build(monkeypatch: pytest.MonkeyPatch):
    """Make the local build be whatever the test says, without compiling.

    Returns a setter: `local_build(b"A", b"C")` makes the local programs those
    bytes; the digest is what it always is, so a match is a real comparison.
    """
    def set_to(approval: bytes = b"A", clear: bytes = b"C") -> None:
        monkeypatch.setattr(mainnet_clock.verify_build, "_spec", lambda name: {"name": name})
        monkeypatch.setattr(mainnet_clock.verify_build, "_programs", lambda spec: (approval, clear))
    set_to()
    return set_to


def test_history_is_the_create_and_every_update_in_round_order() -> None:
    indexer = FakeIndexer([[
        appl(CREATED, create=True),
        appl(CREATED + 50),  # a register; not an install
        appl(CREATED + 300, on_completion="update"),
        appl(CREATED + 100, on_completion="update"),  # out of order, on purpose
        appl(CREATED + 200, on_completion="optin"),
    ]])
    history = read_install_history(indexer, APP_ID)
    assert history.created_round == CREATED
    assert history.update_rounds == (CREATED + 100, CREATED + 300)
    assert history.installed_round == CREATED + 300
    # The promised query: application transactions against this app.
    assert indexer.calls[0]["application_id"] == APP_ID
    assert indexer.calls[0]["txn_type"] == "appl"


def test_history_is_paged_to_exhaustion_and_the_update_on_the_last_page_counts() -> None:
    """The indexer pages at 1,000; an app that has been executing for a month
    has more transactions than that, and the update that matters is the most
    recent one, which is the one on the last page."""
    first = [appl(CREATED, create=True)] + [appl(CREATED + i) for i in range(1, 6)]
    second = [appl(CREATED + 10 + i) for i in range(5)] + [appl(CREATED + 900, on_completion="update")]
    indexer = FakeIndexer([first, second])
    history = read_install_history(indexer, APP_ID)
    assert history.installed_round == CREATED + 900
    assert [c.get("next_page") for c in indexer.calls] == [None, "1"]


def test_an_update_nested_in_a_group_is_still_an_update() -> None:
    root = appl(CREATED + 5, app_id=APP_ID + 1)
    root["inner-txns"] = [appl(CREATED + 5, on_completion="update")]
    history = read_install_history(FakeIndexer([[appl(CREATED, create=True), root]]), APP_ID)
    assert history.installed_round == CREATED + 5


def test_a_history_without_the_create_is_unknown_not_shortened() -> None:
    # Pruned history, or an indexer that only has the last few months: the
    # updates it does show are real, and none of them is provably the latest
    # relative to a create nobody can see.
    indexer = FakeIndexer([[appl(CREATED + 300, on_completion="update"), appl(CREATED + 301)]])
    with pytest.raises(HistoryUnknown, match="creation transaction is not"):
        read_install_history(indexer, APP_ID)


def test_an_indexer_that_cannot_be_reached_is_unknown() -> None:
    indexer = FakeIndexer([], fail=ConnectionError("indexer.testnet: connection refused"))
    with pytest.raises(HistoryUnknown, match="connection refused"):
        read_install_history(indexer, APP_ID)


def test_an_indexer_that_repeats_a_page_token_is_unknown() -> None:
    class Looping(FakeIndexer):
        def search_transactions(self, **kwargs) -> dict:
            return {"transactions": [appl(CREATED, create=True)], "next-token": "again"}

    with pytest.raises(HistoryUnknown, match="paged to exhaustion"):
        read_install_history(Looping([]), APP_ID)


# --- measure, report and the gate --------------------------------------------


def test_measure_dates_the_programs_from_the_latest_update(local_build) -> None:
    now = CREATED + 40 * DAY_ROUNDS
    indexer = FakeIndexer([[
        appl(CREATED, create=True),
        appl(now - 1 * DAY_ROUNDS, on_completion="update"),
    ]])
    measured = measure(FakeAlgod(now), indexer, "keeper", APP_ID, ROUND_SECONDS)
    assert measured.matches_source
    assert measured.history_known
    assert measured.app_days == pytest.approx(40, abs=0.01)
    assert measured.days == pytest.approx(1, abs=0.01)
    assert not measured.complete(30)


def test_measure_records_an_unreachable_indexer_rather_than_raising(local_build) -> None:
    indexer = FakeIndexer([], fail=ConnectionError("connection refused"))
    measured = measure(FakeAlgod(CREATED + 400 * DAY_ROUNDS), indexer, "keeper", APP_ID, ROUND_SECONDS)
    assert measured.matches_source  # the programs are fine; their age is not known
    assert not measured.history_known
    assert "connection refused" in measured.history_error
    assert not measured.complete(30)


def test_the_report_names_why_the_hold_cannot_be_measured(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scripts.mainnet_clock")
    report(clock(400, history_error="the indexer could not be searched: connection refused"), 30)
    text = caplog.text
    assert "install round of the deployed programs is unknown" in text
    assert "connection refused" in text
    assert "COMPLETE" not in text
    assert "programs installed at an unknown round" in text


def test_the_report_separates_app_age_from_program_age(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scripts.mainnet_clock")
    report(clock(2, app_days=40, updates_days_ago=(20, 2)), 30)
    text = caplog.text
    assert f"created at round {CREATED:,}" in text
    assert "app age 40.0 days" in text
    assert "(update 2 of 2)" in text
    assert "program age 2.0 days" in text
    assert "28.0 days to go on a 30 day hold, on the programs installed at round" in text
    assert "COMPLETE" not in text


def test_the_report_says_installed_at_creation_when_never_updated(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scripts.mainnet_clock")
    report(clock(40), 30)
    assert "programs installed at creation" in caplog.text
    assert f"COMPLETE, on the programs installed at round {CREATED:,}" in caplog.text


def test_a_digest_mismatch_still_reports_the_hold_not_running(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scripts.mainnet_clock")
    report(clock(40, matches=False), 30)
    assert "source matches chain: NO" in caplog.text
    assert "this hold is not running" in caplog.text
    assert "COMPLETE" not in caplog.text


def test_a_digest_mismatch_is_not_reported_when_history_is_unknown_too(caplog: pytest.LogCaptureFixture) -> None:
    # Two reasons the hold is not running; the report leads with the one that
    # says even the days above cannot be trusted, and does not print a "days
    # served by code about to be replaced" line that has no days in it.
    caplog.set_level(logging.INFO, logger="scripts.mainnet_clock")
    report(clock(40, matches=False, history_error="pruned"), 30)
    assert "install round of the deployed programs is unknown" in caplog.text
    assert "source matches chain: NO" in caplog.text
    assert "time served by programs" not in caplog.text


class _Algorand:
    def __init__(self, algod, indexer) -> None:
        class _Client:
            pass
        self.client = _Client()
        self.client.algod = algod
        self.client.indexer = indexer


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, local_build):
    """Run `main` against fakes: no network, no `.env.<network>`, no compile."""
    def wire(algod, indexer) -> None:
        monkeypatch.setattr(mainnet_clock.net, "load_network", lambda network: network)
        monkeypatch.setattr(mainnet_clock.net, "connect", lambda network: _Algorand(algod, indexer))
        monkeypatch.setattr(mainnet_clock.net, "seconds_per_round", lambda network: ROUND_SECONDS)
        monkeypatch.setattr(mainnet_clock, "resolve_app_id", lambda parser, app_id, network: app_id)
    return wire


def test_the_gate_stays_shut_when_the_indexer_is_down(wired) -> None:
    """Unknown history never passes the gate, however old the app is."""
    wired(FakeAlgod(CREATED + 400 * DAY_ROUNDS), FakeIndexer([], fail=ConnectionError("down")))
    assert mainnet_clock.main(["--network", "testnet", "--app-id", str(APP_ID), "--gate"]) == 1
    # Without --gate it is a report, and a report exits 0 whatever it finds.
    assert mainnet_clock.main(["--network", "testnet", "--app-id", str(APP_ID)]) == 0


def test_the_gate_opens_on_unchanged_code_past_the_hold(wired) -> None:
    wired(FakeAlgod(CREATED + 40 * DAY_ROUNDS), FakeIndexer([[appl(CREATED, create=True)]]))
    assert mainnet_clock.main(["--network", "testnet", "--app-id", str(APP_ID), "--gate"]) == 0


def test_the_gate_stays_shut_after_a_fresh_update_on_an_old_app(wired) -> None:
    now = CREATED + 40 * DAY_ROUNDS
    wired(FakeAlgod(now), FakeIndexer([[
        appl(CREATED, create=True),
        appl(now - DAY_ROUNDS, on_completion="update"),
    ]]))
    assert mainnet_clock.main(["--network", "testnet", "--app-id", str(APP_ID), "--gate"]) == 1
