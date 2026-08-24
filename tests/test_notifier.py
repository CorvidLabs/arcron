"""What the notifier announces, and what it must never be able to do.

The diffing is pure, so every interesting case — a run of executions, an
upkeep going quiet, a restart — is testable without a chain or a webhook.
"""

import ast
import json
from pathlib import Path

import pytest

from scripts.keeper_bot import Upkeep
from scripts.notifier import (
    STALL_INTERVALS,
    Snapshot,
    _as_address,
    diff,
    load,
    save,
    summarise,
)

NOTIFIER_SOURCE = Path("scripts/notifier.py")


def upkeep(**overrides) -> Upkeep:
    base = dict(
        upkeep_id=1,
        target_app=1043,
        interval_rounds=10,
        next_execution_round=1_000,
        fee_per_execution=4_000,
        balance=12_000,
        times_executed=0,
        policy=0,
        fee_cap=0,
        last_serviced_round=990,
    )
    base.update(overrides)
    return Upkeep(**base)


def snapshot(upkeeps: list[Upkeep], current_round: int = 1_000) -> Snapshot:
    return Snapshot.of(upkeeps, current_round)


# --- the boundary that matters ---------------------------------------

def test_the_notifier_cannot_sign_anything() -> None:
    """Read-only is a structural property here, not a promise in a docstring.

    A notifier that could sign would be a liability with no upside, so this
    fails if anything key-shaped ever appears in it.
    """
    source = NOTIFIER_SOURCE.read_text()
    forbidden = ("mnemonic", "private_key", "signer", "sign_transaction", "from_environment")
    found = [word for word in forbidden if word in source.lower()]
    assert found == [], f"the notifier must hold no keys, but mentions: {found}"

    # And it imports nothing that could produce an account.
    tree = ast.parse(source)
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("account" in module for module in imported)


# --- executions -------------------------------------------------------

def test_announces_an_execution_with_what_it_paid() -> None:
    before = snapshot([upkeep()])
    after = snapshot([upkeep(times_executed=1, balance=8_000, next_execution_round=1_010)])

    events = diff(before, after)

    assert [event.kind for event in events] == ["executed"]
    assert "0.004 ALGO paid" in events[0].text
    assert "next due at round 1010" in events[0].text


def test_collapses_a_burst_into_one_announcement() -> None:
    # Catch-up after an outage can run an upkeep several times between scans;
    # that is one thing that happened, not four.
    before = snapshot([upkeep()])
    after = snapshot([upkeep(times_executed=4, balance=0, next_execution_round=1_040)])

    events = diff(before, after)

    assert len(events) == 2  # the burst, and going dry as a result
    assert "×4" in events[0].text
    assert "0.016 ALGO paid" in events[0].text


# --- the failures, which are the point --------------------------------

def test_announces_an_upkeep_running_dry_once() -> None:
    funded = snapshot([upkeep()])
    dry = snapshot([upkeep(balance=100)])

    events = diff(funded, dry)
    assert [event.kind for event in events] == ["dormant"]
    assert "no keeper can run it" in events[0].text
    assert "Anyone can top it up" in events[0].text

    # Still dry on the next scan: already said, say nothing.
    assert diff(dry, snapshot([upkeep(balance=100)])) == []


def test_announces_recovery() -> None:
    dry = snapshot([upkeep(balance=100)])
    funded = snapshot([upkeep(balance=50_000)])
    assert [event.kind for event in diff(dry, funded)] == ["revived"]


def test_announces_an_upkeep_nobody_is_servicing() -> None:
    # Funded and due, but far past its window: a keeper failure, not a funding one.
    late_round = 1_000 + STALL_INTERVALS * 10 + 50
    healthy = snapshot([upkeep()], current_round=1_000)
    late = snapshot([upkeep()], current_round=late_round)

    events = diff(healthy, late)

    assert [event.kind for event in events] == ["stalled"]
    assert "Nobody is keeping it" in events[0].text
    assert diff(late, snapshot([upkeep()], current_round=late_round + 100)) == []


def test_a_dry_upkeep_is_not_also_reported_as_unserviced() -> None:
    # It cannot be executed by anyone, so blaming keepers would be wrong.
    events = diff(
        snapshot([upkeep()], current_round=1_000),
        snapshot([upkeep(balance=1)], current_round=99_999),
    )
    assert [event.kind for event in events] == ["dormant"]


# --- registry churn ---------------------------------------------------

def test_announces_registration_and_cancellation() -> None:
    one = snapshot([upkeep(upkeep_id=1)])
    two = snapshot([upkeep(upkeep_id=1), upkeep(upkeep_id=2)])

    assert [event.kind for event in diff(one, two)] == ["registered"]
    assert [event.kind for event in diff(two, one)] == ["cancelled"]


def test_a_first_run_does_not_announce_the_whole_registry() -> None:
    """Starting fresh against a busy app must not flood the channel."""
    events = diff(Snapshot(), snapshot([upkeep(upkeep_id=i) for i in range(20)]))
    assert events == []


def test_a_first_run_does_report_what_is_currently_broken() -> None:
    """Healthy upkeeps are noise on startup; broken ones are the news.

    A notifier started against an app with a dry upkeep should say so rather
    than wait for it to change state, which it never will on its own.
    """
    events = diff(Snapshot(), snapshot([upkeep(), upkeep(upkeep_id=2, balance=1)]))
    assert [event.kind for event in events] == ["dormant"]


# --- surviving a restart ---------------------------------------------

def test_state_round_trips_so_a_restart_replays_nothing(tmp_path: Path) -> None:
    path = tmp_path / "notifier.json"
    live = snapshot([upkeep(times_executed=3), upkeep(upkeep_id=2, balance=1)])
    save(path, live)

    restored = load(path)

    assert restored.upkeeps.keys() == live.upkeeps.keys()
    assert restored.dormant == live.dormant
    # The whole point: nothing to announce immediately after a restart.
    assert diff(restored, live) == []


def test_unreadable_state_does_not_stop_the_notifier(tmp_path: Path) -> None:
    path = tmp_path / "notifier.json"
    path.write_text("{ not json")
    assert load(path).upkeeps == {}


def test_running_without_state_is_allowed() -> None:
    assert load(None).upkeeps == {}
    save(None, snapshot([upkeep()]))  # must not raise


# --- reading a block's sender ----------------------------------------

def test_a_senders_address_is_read_however_algod_spells_it() -> None:
    """algosdk returns a decoded address; other paths return raw bytes.

    Pinned because getting this wrong is silent — the notifier simply stops
    attributing executions rather than failing loudly.
    """
    address = "FIYLSRRXA22FZ4FXV7NJUGFESVIEHIT4M23A4NRZTSR4NCTRSCDMXO4LGA"
    assert _as_address(address) == address

    from algosdk import encoding

    assert _as_address(encoding.decode_address(address)) == address


def test_an_unrecognisable_sender_is_skipped_rather_than_fatal() -> None:
    # Attribution is a nicety; a surprising block must not stop announcements.
    for value in (None, "", b"", "not-an-address", 42, b"\x00" * 31):
        assert _as_address(value) is None


# --- the periodic summary --------------------------------------------

def test_summary_counts_what_it_can_and_flags_what_is_stuck() -> None:
    text = summarise(snapshot([upkeep(), upkeep(upkeep_id=2, balance=1)]), executions=7, paid=28_000)
    assert "2 upkeeps" in text
    assert "7 executions" in text
    assert "0.028 ALGO paid" in text
    assert "1 out of funds" in text
