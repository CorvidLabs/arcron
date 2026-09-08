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
        creator="E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E",
        target_app=1043,
        interval_rounds=10,
        next_execution_round=1_000,
        fee_per_execution=4_000,
        balance=12_000,
        times_executed=0,
        policy=0,
        fee_cap=0,
        last_serviced_round=990,
        fee_asset=0,
        asset_fee=0,
        asset_balance=0,
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
    # The escrow fell from 12,000 to 0, so 12,000 µALGO is what was paid. The
    # old estimator modelled it as 4 × the base fee and reported 0.016 ALGO —
    # more than the upkeep ever held.
    assert "0.012 ALGO paid" in events[0].text


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


def test_a_snapshot_from_before_escalation_does_not_report_the_ceiling() -> None:
    """An upgrade must not make every upkeep in an old state file look late.

    Snapshots persist to disk, so the first run after this feature ships reads
    a `previous` written without `fee_cap` or `last_serviced_round`. Defaulting
    the service round to zero would make every upkeep maximally late and price
    every execution at the ceiling.
    """
    from scripts.notifier import _burst_cost, _fee_now

    legacy = {
        "times_executed": 0,
        "balance": 100_000,
        "fee_per_execution": 4_000,
        "interval_rounds": 10,
        "next_execution_round": 1_000,
        "target_app": 1043,
    }
    assert _fee_now(legacy, 9_999_999) == 4_000

    current = {**legacy, "times_executed": 2, "fee_cap": 12_000, "last_serviced_round": 1_010}
    assert _burst_cost(legacy, current, 2) == 8_000


# --- the detector the unfrozen window depends on -----------------------
#
# An unfrozen MainNet deployment whose id is unpublished is supposed to hold
# nobody's escrow but ours, and a stranger's box starts a 24-hour clock for an
# operator decision (docs/design/mainnet-rollout.md, "If a stranger appears").
# A reviewer pointed out that the plan was worth nothing because nothing could
# tell one creator's upkeep from another's: the box always carried the creator
# and the decoder dropped it, and the snapshot had no field for it.

OURS = "E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E"
STRANGER = "WOX2O7LDLN74QDQYDJRUHGBLAH3JBEUYAFJO6FQL4P2EXV33VYAR536BBY"


def test_a_stranger_registering_is_announced() -> None:
    events = diff(
        snapshot([]),
        snapshot([upkeep(creator=STRANGER)]),
        known_creators=frozenset({OURS}),
    )
    assert [e.kind for e in events] == ["stranger"]
    assert "who is not one of us" in events[0].text


def test_a_stranger_is_announced_even_on_a_first_run() -> None:
    """An ordinary registration is suppressed on a first run so the initial
    registry is not a flood. A stranger must not be, because an app that is
    supposed to be empty and is not is exactly the thing being watched for,
    and there is no flood to avoid."""
    events = diff(
        Snapshot(),  # no previous run at all
        snapshot([upkeep(creator=STRANGER)]),
        known_creators=frozenset({OURS}),
    )
    assert [e.kind for e in events] == ["stranger"]


def test_our_own_upkeep_is_not_a_stranger() -> None:
    """A detector that flags everybody is an outage, not a detector."""
    events = diff(
        snapshot([upkeep()]),
        snapshot([upkeep(upkeep_id=2, creator=OURS), upkeep()]),
        known_creators=frozenset({OURS}),
    )
    assert [e.kind for e in events] == ["registered"]


def test_no_allowlist_means_nobody_is_a_stranger() -> None:
    """Right on a shared TestNet app, wrong on one whose id is unpublished,
    which is why the runner says so at startup rather than defaulting quietly."""
    events = diff(
        snapshot([upkeep()]),
        snapshot([upkeep(upkeep_id=2, creator=STRANGER), upkeep()]),
    )
    assert [e.kind for e in events] == ["registered"]


# --- attribution names the executor, not the first caller ------------------

def _block(*txns: dict) -> dict:
    return {"block": {"txns": [{"txn": t} for t in txns]}}


class _BlockAlgod:
    def __init__(self, blocks: dict[int, dict]) -> None:
        self.blocks = blocks

    def block_info(self, round_number: int) -> dict:
        return self.blocks[round_number]


def test_attribution_requires_the_execute_selector() -> None:
    """A `cancel` in the same block used to be credited as the execution.

    Every call to the app is an application call. The first one in a block
    was read as the keeper, so a creator cancelling in the round an execution
    landed was named as having executed it.
    """
    from scripts.notifier import EXECUTE_SELECTOR, attribute

    creator = "FIYLSRRXA22FZ4FXV7NJUGFESVIEHIT4M23A4NRZTSR4NCTRSCDMXO4LGA"
    keeper = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"
    cancel_selector = b"\x01\x02\x03\x04"
    algod = _BlockAlgod({
        10: _block(
            {"type": "appl", "apid": 7, "snd": creator, "apaa": [cancel_selector, b"\x00" * 8]},
            {"type": "appl", "apid": 7, "snd": keeper, "apaa": [EXECUTE_SELECTOR, b"\x00" * 8]},
        ),
    })
    assert attribute(algod, 7, since_round=9, until_round=10, upkeep_id=0) == keeper


def test_attribution_finds_nobody_when_only_other_calls_landed() -> None:
    from scripts.notifier import attribute

    creator = "FIYLSRRXA22FZ4FXV7NJUGFESVIEHIT4M23A4NRZTSR4NCTRSCDMXO4LGA"
    algod = _BlockAlgod({
        10: _block({"type": "appl", "apid": 7, "snd": creator, "apaa": [b"\x01\x02\x03\x04"]}),
    })
    assert attribute(algod, 7, since_round=9, until_round=10, upkeep_id=0) is None


def test_attribution_reads_a_base64_selector_too() -> None:
    import base64

    from scripts.notifier import EXECUTE_SELECTOR, attribute

    keeper = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"
    # The REST shape: every argument base64, the id included.
    algod = _BlockAlgod({
        10: _block({"type": "appl", "apid": 7, "snd": keeper,
                    "apaa": [base64.b64encode(EXECUTE_SELECTOR).decode(),
                             base64.b64encode((0).to_bytes(8, "big")).decode()]}),
    })
    assert attribute(algod, 7, since_round=9, until_round=10, upkeep_id=0) == keeper


def test_the_execute_selector_is_the_contracts() -> None:
    """Pinned against the ARC-56 spec, so a renamed method cannot silently un-attribute everything."""
    import hashlib

    from scripts.notifier import EXECUTE_SELECTOR

    spec = json.loads(next((Path(__file__).resolve().parent.parent / "smart_contracts" / "artifacts" / "keeper").glob("*.arc56.json")).read_text())
    execute = next(m for m in spec["methods"] if m["name"] == "execute")
    signature = f"execute({','.join(a['type'] for a in execute['args'])}){execute['returns']['type']}"
    assert EXECUTE_SELECTOR == hashlib.new("sha512_256", signature.encode()).digest()[:4]


# --- Discord cannot park the watcher --------------------------------------

def test_retry_after_is_honoured_only_up_to_a_ceiling(monkeypatch) -> None:
    import urllib.error

    from scripts import notifier

    slept: list[float] = []

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                     {"Retry-After": "86400"}, None)

    monkeypatch.setattr(notifier.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(notifier.time, "sleep", slept.append)
    assert notifier.post("https://discord.invalid/webhook", "hello") is False
    # One sleep between each pair of attempts, none after the last; every one
    # of them the ceiling rather than the day Discord asked for.
    assert slept == [notifier.MAX_RETRY_AFTER_SECONDS] * (notifier.POST_ATTEMPTS - 1)


def test_a_garbage_retry_after_falls_back_rather_than_crashing(monkeypatch) -> None:
    import urllib.error

    from scripts import notifier

    slept: list[float] = []

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                     {"Retry-After": "soon"}, None)

    monkeypatch.setattr(notifier.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(notifier.time, "sleep", slept.append)
    assert notifier.post("https://discord.invalid/webhook", "hello") is False
    assert slept == [2.0] * (notifier.POST_ATTEMPTS - 1)


# --- MainNet refuses to watch blindly -------------------------------------

class _Stop(Exception):
    pass


class _StoppingAlgod:
    def application_info(self, app_id: int) -> dict:
        import base64

        # A keeper, so the startup check passes and the first scan is reached.
        return {"params": {"global-state": [
            {"key": base64.b64encode(b"next_upkeep_id").decode(), "value": {"uint": 0, "type": 2}},
        ]}}

    def status(self) -> dict:
        raise _Stop()


def _connected(monkeypatch) -> None:
    from types import SimpleNamespace

    from scripts import notifier

    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=_StoppingAlgod())))
    monkeypatch.delenv("ARCRON_OURS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)


def test_mainnet_refuses_to_start_with_nobody_counted_as_ours(monkeypatch) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    with pytest.raises(SystemExit):
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once", "--no-state"])


def test_mainnet_refuses_to_start_without_a_webhook(monkeypatch) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    ours = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
    with pytest.raises(SystemExit):
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once", "--no-state", "--ours", ours])


def test_mainnet_starts_with_ours_and_an_explicit_stdout(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    ours = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
    # Past the guards means it reached the first scan, which the fake stops.
    # A state file, because --no-state is one of the guards on MainNet.
    with pytest.raises(_Stop):
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once",
                       "--state-file", str(tmp_path / "n.json"), "--ours", ours, "--stdout"])


def test_ours_is_read_from_the_environment_when_no_flag_is_given(monkeypatch, tmp_path) -> None:
    """compose cannot pass a value from env_file into a command; the process reads it itself."""
    from scripts import notifier

    _connected(monkeypatch)
    monkeypatch.setenv("ARCRON_OURS", "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    with pytest.raises(_Stop):
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once",
                       "--state-file", str(tmp_path / "n.json")])


def test_testnet_still_starts_with_nobody_counted_as_ours(monkeypatch) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    with pytest.raises(_Stop):
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state"])


# --- a wrong app id is refused rather than watched -----------------------

def test_a_nonexistent_app_is_refused_at_startup() -> None:
    """algod answers a box listing for a nonexistent app with 200 and [], so a
    typo in KEEPER_APP_ID used to produce a watcher that watched nothing."""
    from scripts.keeper_bot import UnrecoverableError, require_keeper_app

    class Missing:
        def application_info(self, app_id: int) -> dict:
            raise Exception("application does not exist")

    with pytest.raises(UnrecoverableError, match="does not exist"):
        require_keeper_app(Missing(), 999_999_999_999, "mainnet")


def test_an_app_that_is_not_a_keeper_is_refused_at_startup() -> None:
    import base64

    from scripts.keeper_bot import UnrecoverableError, require_keeper_app

    class Pulse:
        def application_info(self, app_id: int) -> dict:
            return {"params": {"global-state": [{"key": base64.b64encode(b"beats").decode(), "value": {"uint": 3, "type": 2}}]}}

    with pytest.raises(UnrecoverableError, match="not a keeper"):
        require_keeper_app(Pulse(), 1004, "localnet")


def test_a_keeper_passes_the_startup_check() -> None:
    import base64

    from scripts.keeper_bot import require_keeper_app

    class Keeper:
        def application_info(self, app_id: int) -> dict:
            return {"params": {"global-state": [
                {"key": base64.b64encode(b"next_upkeep_id").decode(), "value": {"uint": 0, "type": 2}},
                {"key": base64.b64encode(b"frozen").decode(), "value": {"uint": 0, "type": 2}},
            ]}}

    require_keeper_app(Keeper(), 769891898, "testnet")


def test_main_refuses_an_app_that_is_not_a_keeper(monkeypatch) -> None:
    from types import SimpleNamespace

    from scripts import notifier

    class NotAKeeper(_StoppingAlgod):
        def application_info(self, app_id: int) -> dict:
            return {"params": {"global-state": []}}

    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=NotAKeeper())))
    with pytest.raises(SystemExit):
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state"])


# --- posting is honest about whether it worked (F01) ------------------------
#
# `post` used to return None whatever happened: a 429 slept and then did not
# retry, a 5xx was logged and forgotten. Nothing upstream could tell a
# delivered stranger alert from a dropped one, which for that one event is
# the whole difference.

def _ok():
    from types import SimpleNamespace

    return SimpleNamespace(status=204, close=lambda: None)


def _http_error(request, code: int, headers: dict | None = None):
    import urllib.error

    return urllib.error.HTTPError(request.full_url, code, "error", headers or {}, None)


def _scripted_urlopen(script: list):
    """Each entry is either an int status to refuse with, an exception to
    raise, or "ok". Records how many times it was called."""
    calls: list[str] = []

    def urlopen(request, timeout):
        step = script.pop(0) if script else "ok"
        calls.append(json.loads(request.data)["content"] if step == "ok" else str(step))
        if step == "ok":
            return _ok()
        if isinstance(step, int):
            raise _http_error(request, step)
        raise step

    urlopen.calls = calls  # type: ignore[attr-defined]
    return urlopen


def test_post_returns_true_only_on_a_2xx(monkeypatch) -> None:
    from scripts import notifier

    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", _scripted_urlopen(["ok"]))
    assert notifier.post("https://discord.invalid/webhook", "hello") is True

    monkeypatch.setattr(notifier.urllib.request, "urlopen", _scripted_urlopen([404]))
    assert notifier.post("https://discord.invalid/webhook", "hello") is False


def test_post_retries_a_server_error_and_delivers_on_recovery(monkeypatch) -> None:
    from scripts import notifier

    slept: list[float] = []
    urlopen = _scripted_urlopen([500, 503, "ok"])
    monkeypatch.setattr(notifier.time, "sleep", slept.append)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)

    assert notifier.post("https://discord.invalid/webhook", "hello") is True
    assert len(urlopen.calls) == 3
    # Bounded backoff, doubling, never past the ceiling.
    assert slept == [notifier.POST_BACKOFF_SECONDS, notifier.POST_BACKOFF_SECONDS * 2]
    assert all(s <= notifier.MAX_RETRY_AFTER_SECONDS for s in slept)


def test_post_gives_up_after_bounded_attempts_and_never_raises(monkeypatch) -> None:
    import socket
    import urllib.error

    from scripts import notifier

    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    for failure in ([500] * 10, [urllib.error.URLError("dns")] * 10, [socket.timeout()] * 10,
                    [RuntimeError("reset by peer")] * 10):
        urlopen = _scripted_urlopen(list(failure))
        monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
        assert notifier.post("https://discord.invalid/webhook", "hello") is False
        assert len(urlopen.calls) == notifier.POST_ATTEMPTS


def test_a_rate_limited_post_waits_what_discord_asked_and_then_delivers(monkeypatch) -> None:
    """The old branch slept the Retry-After and then dropped the message
    anyway, which is the one message Discord had just promised to accept."""
    from scripts import notifier

    slept: list[float] = []
    script: list = ["429"]

    def urlopen(request, timeout):
        if script:
            script.pop()
            raise _http_error(request, 429, {"Retry-After": "1.5"})
        return _ok()

    monkeypatch.setattr(notifier.time, "sleep", slept.append)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
    assert notifier.post("https://discord.invalid/webhook", "hello") is True
    assert slept == [1.5]


def test_a_client_error_is_not_retried(monkeypatch) -> None:
    # A deleted webhook will not come back by asking three times.
    from scripts import notifier

    urlopen = _scripted_urlopen([400, 400, 400])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
    assert notifier.post("https://discord.invalid/webhook", "hello") is False
    assert len(urlopen.calls) == 1


# --- a stranger alert is durable (F01) -------------------------------------

def _stranger_event(upkeep_id: int = 7):
    from scripts.notifier import diff

    [event] = diff(
        Snapshot(),
        snapshot([upkeep(upkeep_id=upkeep_id, creator=STRANGER)]),
        known_creators=frozenset({OURS}),
    )
    return event


def test_a_stranger_survives_an_outage_and_is_delivered_on_recovery(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    pending = notifier.PendingStrangers(tmp_path / "pending.json")
    pending.add("testnet", 1, _stranger_event(), current_round=500)
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)

    # Discord is down for the whole first attempt.
    down = _scripted_urlopen([500] * notifier.POST_ATTEMPTS)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", down)
    pending.deliver("https://discord.invalid/webhook", now=0.0)
    assert len(pending) == 1, "an undelivered stranger stays pending"
    assert (tmp_path / "pending.json").exists()

    # Back, one retry window later: delivered and forgotten.
    up = _scripted_urlopen(["ok"])
    monkeypatch.setattr(notifier.urllib.request, "urlopen", up)
    pending.deliver("https://discord.invalid/webhook", now=notifier.STRANGER_RETRY_SECONDS)
    assert len(pending) == 0
    assert "who is not one of us" in up.calls[0]
    assert json.loads((tmp_path / "pending.json").read_text()) == {}


def test_a_pending_stranger_round_trips_through_the_file(tmp_path, monkeypatch) -> None:
    """A restart must find the alert where the previous process left it,
    payload included, and not need the chain to rebuild it."""
    from scripts import notifier

    path = tmp_path / "pending.json"
    notifier.PendingStrangers(path).add("mainnet", 123, _stranger_event(7), current_round=51_234_567)

    restored = notifier.PendingStrangers.load(path)
    assert set(restored.records) == {"mainnet/123/7"}
    record = restored.records["mainnet/123/7"]
    assert record["upkeep_id"] == 7
    assert record["first_seen_round"] == 51_234_567
    assert "who is not one of us" in record["text"]
    assert "round 51234567" in record["text"]

    up = _scripted_urlopen(["ok"])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", up)
    restored.deliver("https://discord.invalid/webhook", now=0.0)
    assert up.calls == [record["text"]]
    assert notifier.PendingStrangers.load(path).records == {}


def test_a_repeat_sighting_keeps_the_first_seen_time(tmp_path) -> None:
    from scripts import notifier

    pending = notifier.PendingStrangers(tmp_path / "pending.json")
    assert pending.add("testnet", 1, _stranger_event(), current_round=500) is True
    assert pending.add("testnet", 1, _stranger_event(), current_round=900) is False
    assert pending.records["testnet/1/7"]["first_seen_round"] == 500


def test_a_crash_between_the_2xx_and_the_acknowledgement_yields_a_duplicate_not_a_loss(
    monkeypatch, tmp_path
) -> None:
    from scripts import notifier

    path = tmp_path / "pending.json"
    pending = notifier.PendingStrangers(path)
    pending.add("testnet", 1, _stranger_event(), current_round=500)

    class _Crash(BaseException):
        pass

    real_save = notifier.PendingStrangers.save
    def crash_on_save(self) -> None:
        # The pacing save before the post goes through; the acknowledgement
        # save after the 2xx (the record already deleted in memory) crashes.
        if not self.records:
            raise _Crash()
        real_save(self)

    delivered = _scripted_urlopen(["ok", "ok"])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", delivered)
    monkeypatch.setattr(notifier.PendingStrangers, "save", crash_on_save)
    with pytest.raises(_Crash):
        pending.deliver("https://discord.invalid/webhook", now=0.0)
    assert len(delivered.calls) == 1, "Discord answered 2xx before the crash"

    # The process restarts and reads the file the crash left behind.
    monkeypatch.undo()
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", delivered)
    restarted = notifier.PendingStrangers.load(path)
    assert len(restarted) == 1
    # The attempt before the crash was written into the record, so the
    # restart waits out the window rather than posting on the spot...
    restarted.deliver("https://discord.invalid/webhook", now=1.0)
    assert len(delivered.calls) == 1
    # ...and then the duplicate lands.
    restarted.deliver("https://discord.invalid/webhook", now=notifier.STRANGER_RETRY_SECONDS)
    assert len(delivered.calls) == 2, "posted twice; never zero times"
    assert len(restarted) == 0


def test_pending_strangers_are_retried_no_more_than_once_per_window(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    pending = notifier.PendingStrangers(tmp_path / "pending.json")
    pending.add("testnet", 1, _stranger_event(), current_round=500)
    down = _scripted_urlopen([500] * 100)
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", down)

    pending.deliver("https://discord.invalid/webhook", now=0.0)
    pending.deliver("https://discord.invalid/webhook", now=30.0)
    pending.deliver("https://discord.invalid/webhook", now=notifier.STRANGER_RETRY_SECONDS - 1)
    assert len(down.calls) == notifier.POST_ATTEMPTS, "one attempt inside the window"
    pending.deliver("https://discord.invalid/webhook", now=notifier.STRANGER_RETRY_SECONDS)
    assert len(down.calls) == 2 * notifier.POST_ATTEMPTS


def test_the_pending_file_sits_beside_the_snapshot() -> None:
    from scripts import notifier

    assert notifier.pending_path(Path("/var/lib/arcron/notifier-mainnet-123.json")) == Path(
        "/var/lib/arcron/notifier-mainnet-123-pending.json"
    )
    assert notifier.pending_path(None) is None


# --- the main loop, end to end against a scripted registry -----------------

class _Halt(KeyboardInterrupt):
    """Stops `main` cleanly after the scripted scans; `Exception` would be
    caught by the loop's retry clause and never end the test."""


class _Clock:
    """`time.sleep` advances `time.time`, so a test can walk the notifier
    through retry windows without waiting through them."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def time(self) -> float:
        return self.now


class _ScriptedAlgod(_StoppingAlgod):
    def __init__(self, scans: int) -> None:
        self.scans = scans
        self.round = 1_000

    def status(self) -> dict:
        if self.scans == 0:
            raise _Halt()
        self.scans -= 1
        self.round += 100
        return {"last-round": self.round}


def _run_main(monkeypatch, tmp_path, registries: list[list], urlopen, clock: _Clock,
              executors=lambda *a: {}, extra: tuple[str, ...] = ()) -> None:
    from types import SimpleNamespace

    from scripts import notifier

    algod = _ScriptedAlgod(scans=len(registries))
    script = list(registries)
    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=algod)))
    monkeypatch.setattr(notifier, "scan_upkeeps", lambda algod, app_id: script.pop(0))
    monkeypatch.setattr(notifier, "executors", executors)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(notifier.time, "sleep", clock.sleep)
    monkeypatch.setattr(notifier.time, "time", clock.time)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.delenv("ARCRON_OURS", raising=False)
    notifier.main(["--network", "testnet", "--app-id", "1", "--ours", OURS,
                   "--state-file", str(tmp_path / "notifier.json"),
                   "--poll-seconds", str(notifier.STRANGER_RETRY_SECONDS), *extra])


def test_a_stranger_cancelled_before_delivery_is_still_delivered(monkeypatch, tmp_path) -> None:
    """The record carries its payload, so a box that vanished before Discord
    came back is announced anyway. A delivered-id set re-read from live boxes
    would have dropped exactly this one."""
    from scripts import notifier

    # Every attempt during the first scan fails; everything after succeeds.
    urlopen = _scripted_urlopen([500] * notifier.POST_ATTEMPTS)
    _run_main(
        monkeypatch, tmp_path,
        registries=[[upkeep(upkeep_id=7, creator=STRANGER)], []],
        urlopen=urlopen, clock=_Clock(),
    )
    posted = [c for c in urlopen.calls if c != "500"]
    assert any("Upkeep 7 was registered by" in text for text in posted)
    assert any("Upkeep 7 cancelled" in text for text in posted)
    # And delivered means acknowledged: nothing is left waiting.
    assert json.loads((tmp_path / "notifier-pending.json").read_text()) == {}


def test_a_stranger_is_posted_before_a_flood_of_executions(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    # A wide interval, so none of the forty is also "stalled" at the scripted
    # rounds and the count below is executions only.
    before = [upkeep(upkeep_id=i, balance=1_000_000, interval_rounds=1_000) for i in range(1, 41)]
    after = [upkeep(upkeep_id=i, balance=996_000, times_executed=1, interval_rounds=1_000,
                    next_execution_round=2_200) for i in range(1, 41)]
    after.append(upkeep(upkeep_id=99, creator=STRANGER, interval_rounds=1_000))
    urlopen = _scripted_urlopen([])
    _run_main(monkeypatch, tmp_path, registries=[before, after], urlopen=urlopen, clock=_Clock())

    assert len(urlopen.calls) == 41
    assert "Upkeep 99 was registered by" in urlopen.calls[0]
    assert all("executed" in text for text in urlopen.calls[1:])
    assert not notifier.PendingStrangers.load(tmp_path / "notifier-pending.json").records


def test_the_pending_file_is_written_before_the_snapshot_advances(monkeypatch, tmp_path) -> None:
    """A crash between recording the sighting and saving the snapshot must
    leave the sighting on disk; the other order would leave a snapshot that
    thinks the stranger is old news and nothing to say so."""
    from scripts import notifier

    seen: list[str] = []
    real_save = notifier.save

    def save_and_record(path, snap) -> None:
        seen.append("snapshot")
        real_save(path, snap)

    real_pending_save = notifier.PendingStrangers.save

    def pending_save(self) -> None:
        seen.append("pending")
        real_pending_save(self)

    monkeypatch.setattr(notifier, "save", save_and_record)
    monkeypatch.setattr(notifier.PendingStrangers, "save", pending_save)
    _run_main(monkeypatch, tmp_path, registries=[[upkeep(upkeep_id=7, creator=STRANGER)]],
              urlopen=_scripted_urlopen([500] * 10), clock=_Clock())
    assert seen.index("pending") < seen.index("snapshot")
    assert "testnet/1/7" in json.loads((tmp_path / "notifier-pending.json").read_text())


def test_an_undeliverable_ordinary_event_is_logged_and_dropped(monkeypatch, tmp_path, caplog) -> None:
    import logging

    from scripts import notifier

    before = [upkeep()]
    after = [upkeep(times_executed=1, balance=8_000, next_execution_round=1_010)]
    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[before, after],
                  urlopen=_scripted_urlopen([500] * 10), clock=_Clock())
    assert any("Dropped the 'executed' announcement" in r.message for r in caplog.records)
    # Nothing ordinary is queued for later; only strangers are, and there
    # were none, so the pending file was never even created.
    assert not (tmp_path / "notifier-pending.json").exists()


# --- the stranger text matches the accepted policy (F02) --------------------

def test_the_stranger_text_asks_for_an_operator_decision_not_a_freeze() -> None:
    """docs/design/mainnet-rollout.md, "If a stranger appears": a durable alert
    and an operator decision within 24 hours, never an automatic freeze."""
    text = _stranger_event().text.lower()
    assert "freeze now" not in text
    assert "the agreed answer is to freeze" not in text
    assert "operator decision" in text
    assert "24 hours" in text
    # The three permitted outcomes, and the two forbidden ones named as such.
    assert "accepted for permanence" in text
    assert "already-approved update" in text
    assert "accept the temporary unfrozen exposure" in text
    assert "not an automatic freeze" in text
    assert "creator-only" in text  # why cancel is not an option for us


# --- honest counts in the summary (F13) -------------------------------------

def test_a_burst_carries_its_run_count_and_estimated_cost() -> None:
    [event] = diff(
        snapshot([upkeep()]),
        snapshot([upkeep(times_executed=4, balance=8_000, next_execution_round=1_040)]),
    )
    assert event.kind == "executed"
    assert event.runs == 4
    assert event.paid == 4_000  # the drawdown, not 4 × the base fee


def test_two_upkeeps_executed_in_one_scan_are_counted_separately() -> None:
    before = snapshot([upkeep(upkeep_id=1), upkeep(upkeep_id=2, fee_per_execution=6_000)])
    after = snapshot([
        upkeep(upkeep_id=1, times_executed=2, balance=4_000, next_execution_round=1_020),
        upkeep(upkeep_id=2, fee_per_execution=6_000, times_executed=1, balance=6_000,
               next_execution_round=1_010),
    ])
    events = [e for e in diff(before, after) if e.kind == "executed"]
    assert [(e.upkeep_id, e.runs, e.paid) for e in events] == [(1, 2, 8_000), (2, 1, 6_000)]
    # What main() adds up: three runs, 14,000 µALGO, not "two events at the base fee".
    assert sum(e.runs for e in events) == 3
    assert sum(e.paid for e in events) == 14_000


def test_events_that_are_not_executions_count_no_runs() -> None:
    for event in diff(snapshot([upkeep()]), snapshot([upkeep(balance=1)])):
        assert (event.runs, event.paid) == (0, 0)


def test_summary_labels_the_payment_total_as_an_estimate() -> None:
    text = summarise(snapshot([upkeep()]), executions=3, paid=12_000)
    assert "≈ 0.012 ALGO paid to keepers" in text
    assert "estimated from escrow drawdown" in text


def test_an_unattributed_execution_says_so() -> None:
    from scripts.notifier import _attribution_line

    assert "attribution unknown" in _attribution_line(None)
    keeper = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"
    assert "NUGVPQGZ" in _attribution_line(keeper)
    assert "unknown" not in _attribution_line(keeper)


# --- --ours takes addresses, not names --------------------------------------

def test_an_nfd_name_in_ours_is_refused_at_startup(monkeypatch, capsys) -> None:
    """`corvid.algo` in --ours would make our own creator look like a stranger
    on the first registration: loud and wrong, but at least visible. The
    check names the entry so the fix is obvious."""
    from scripts import notifier

    _connected(monkeypatch)
    with pytest.raises(SystemExit):
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state",
                       "--ours", f"{OURS},corvid.algo"])
    err = capsys.readouterr().err
    assert "corvid.algo" in err
    assert "not resolved" in err


def test_a_mistyped_address_in_ours_is_refused_at_startup(monkeypatch, capsys) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    with pytest.raises(SystemExit):
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state",
                       "--ours", OURS[:-1] + "A"])
    assert "not an Algorand address" in capsys.readouterr().err


def test_valid_addresses_in_ours_are_accepted(monkeypatch) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    with pytest.raises(_Stop):  # past the guards, into the first scan
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state",
                       "--ours", f"{OURS}, {STRANGER}"])


def test_the_summary_help_names_it_as_the_liveness_signal() -> None:
    source = NOTIFIER_SOURCE.read_text()
    assert "liveness signal" in source
    assert "24-hour" in source


# --- second review of the F01 work: what the first round got wrong ----------

def test_a_webhook_without_a_scheme_is_refused_at_startup(monkeypatch, capsys) -> None:
    """`urllib.request.Request` raises ValueError on a URL with no scheme, and
    it did so inside `post` but outside its `try`, on every scan, from the
    stranger re-post that runs before anything else."""
    from scripts import notifier

    _connected(monkeypatch)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "discord.com/api/webhooks/1/abc")
    with pytest.raises(SystemExit):
        notifier.main(["--network", "testnet", "--app-id", "1", "--once", "--no-state"])
    assert "DISCORD_WEBHOOK_URL" in capsys.readouterr().err

    assert notifier.valid_webhook("https://discord.com/api/webhooks/1/abc")
    assert notifier.valid_webhook("http://127.0.0.1:9/hook")
    for bad in ("discord.com/api/webhooks/1/abc", "https://", "ftp://discord.com/x", "", "   "):
        assert not notifier.valid_webhook(bad), bad


def test_post_does_not_raise_on_a_url_urllib_cannot_form(monkeypatch) -> None:
    from scripts import notifier

    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    # Not monkeypatching urlopen: the failure is in building the request.
    assert notifier.post("discord.com/api/webhooks/1/abc", "hello") is False


def test_an_unwritable_state_directory_does_not_stop_delivery(monkeypatch, tmp_path, caplog) -> None:
    """Reviewer's reproduction: six scans, three stranger posts, zero
    executions announced, because the save after the 2xx aborted the scan and
    the snapshot never advanced. The disk is now allowed to fail."""
    import logging

    from scripts import notifier

    def refuse_to_write(path, payload) -> None:
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr(notifier, "_write_json", refuse_to_write)
    before = [upkeep()]
    after = [upkeep(times_executed=1, balance=8_000, next_execution_round=1_010),
             upkeep(upkeep_id=7, creator=STRANGER, interval_rounds=1_000)]
    urlopen = _scripted_urlopen([])
    with caplog.at_level(logging.ERROR, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[before, after, after],
                  urlopen=urlopen, clock=_Clock())
    strangers = [c for c in urlopen.calls if "Upkeep 7 was registered" in c]
    executed = [c for c in urlopen.calls if "Upkeep 1 executed" in c]
    assert len(strangers) == 1, "delivered once, from memory, not once per window"
    assert len(executed) == 1, "the scan finished and ordinary events went out"
    assert any("Permission denied" in r.message for r in caplog.records)


def test_a_pending_stranger_is_posted_even_when_the_node_is_down(monkeypatch, tmp_path) -> None:
    """Delivery used to run only after a successful scan, so a node outage
    held back an alert that Discord was ready to take."""
    from types import SimpleNamespace

    from scripts import notifier

    state = tmp_path / "notifier.json"
    notifier.PendingStrangers(notifier.pending_path(state)).add(
        "testnet", 1, _stranger_event(7), current_round=500)

    class DownAlgod(_StoppingAlgod):
        calls = 0

        def status(self) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("403 Forbidden")
            raise _Halt()

    urlopen = _scripted_urlopen([])
    clock = _Clock()
    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=DownAlgod())))
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(notifier.time, "sleep", clock.sleep)
    monkeypatch.setattr(notifier.time, "time", clock.time)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    notifier.main(["--network", "testnet", "--app-id", "1", "--ours", OURS,
                   "--state-file", str(state)])
    assert len(urlopen.calls) == 1
    assert "Upkeep 7 was registered by" in urlopen.calls[0]
    assert notifier.PendingStrangers.load(notifier.pending_path(state)).records == {}


def test_the_per_event_payment_is_labelled_an_estimate() -> None:
    [event] = diff(
        snapshot([upkeep()]),
        snapshot([upkeep(times_executed=1, balance=8_000, next_execution_round=1_010)]),
    )
    assert "≈ 0.004 ALGO paid (escrow drawdown)" in event.text


def test_attribution_matches_the_upkeep_id_not_just_the_selector() -> None:
    """Two upkeeps run by two keepers in one window were both credited to
    whichever keeper's `execute` was read first."""
    from scripts.notifier import EXECUTE_SELECTOR, attribute, executors

    a = "FIYLSRRXA22FZ4FXV7NJUGFESVIEHIT4M23A4NRZTSR4NCTRSCDMXO4LGA"
    b = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"
    itob = lambda n: n.to_bytes(8, "big")  # noqa: E731
    algod = _BlockAlgod({
        10: _block(
            {"type": "appl", "apid": 7, "snd": a, "apaa": [EXECUTE_SELECTOR, itob(1)]},
            {"type": "appl", "apid": 7, "snd": b, "apaa": [EXECUTE_SELECTOR, itob(2)]},
        ),
    })
    assert attribute(algod, 7, 9, 10, upkeep_id=1) == a
    assert attribute(algod, 7, 9, 10, upkeep_id=2) == b
    assert attribute(algod, 7, 9, 10, upkeep_id=3) is None
    assert executors(algod, 7, 9, 10) == {1: a, 2: b}


def test_an_execute_call_without_an_id_argument_attributes_nothing() -> None:
    from scripts.notifier import EXECUTE_SELECTOR, executors

    algod = _BlockAlgod({
        10: _block({"type": "appl", "apid": 7, "snd": OURS, "apaa": [EXECUTE_SELECTOR]},
                   {"type": "appl", "apid": 7, "snd": OURS, "apaa": [EXECUTE_SELECTOR, b"\x01"]}),
    })
    assert executors(algod, 7, 9, 10) == {}


def test_the_windows_blocks_are_read_once_per_scan_not_once_per_event(monkeypatch, tmp_path) -> None:
    """N executions used to cost N passes over the same blocks, against the
    public quota the keeper is already refused over."""
    reads: list[tuple] = []
    keeper = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"

    def counted(algod, app_id, since_round, until_round):
        reads.append((since_round, until_round))
        return {i: keeper for i in range(1, 6)}  # the first five were this keeper

    before = [upkeep(upkeep_id=i, interval_rounds=1_000) for i in range(1, 11)]
    after = [upkeep(upkeep_id=i, interval_rounds=1_000, times_executed=1, balance=8_000,
                    next_execution_round=2_200) for i in range(1, 11)]
    urlopen = _scripted_urlopen([])
    _run_main(monkeypatch, tmp_path, registries=[before, after], urlopen=urlopen, clock=_Clock(),
              executors=counted)
    assert len(reads) == 1, "one pass over the window for ten executions"
    posted = [c for c in urlopen.calls if "executed" in c]
    assert len(posted) == 10
    assert sum("NUGVPQGZ" in c for c in posted) == 5
    assert sum("attribution unknown" in c for c in posted) == 5


def test_a_corrupt_pending_file_is_moved_aside_not_overwritten(tmp_path, caplog) -> None:
    import logging

    from scripts import notifier

    path = tmp_path / "pending.json"
    path.write_text("{ not json, but maybe a stranger")
    with caplog.at_level(logging.ERROR, logger=notifier.logger.name):
        pending = notifier.PendingStrangers.load(path)
    assert pending.records == {}
    aside = [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(aside) == 1 and "maybe a stranger" in aside[0].read_text()
    assert any("moved to" in r.message for r in caplog.records)

    # The next sighting writes a fresh file and the evidence is still there.
    pending.add("testnet", 1, _stranger_event(9), current_round=1)
    assert "testnet/1/9" in json.loads(path.read_text())
    assert aside[0].exists()


def test_an_incomplete_pending_record_is_kept_and_delivered(tmp_path, monkeypatch, caplog) -> None:
    """An entry without `text` used to be dropped on load and erased by the
    next save. It is a sighting nobody has acted on; it is delivered as what
    it is, naming the upkeep from the key."""
    import logging

    from scripts import notifier

    path = tmp_path / "pending.json"
    path.write_text(json.dumps({
        "mainnet/123/7": {"upkeep_id": 7, "first_seen_round": 51_234_567},
        "mainnet/123/8": "not even an object",
    }))
    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        pending = notifier.PendingStrangers.load(path)
    assert set(pending.records) == {"mainnet/123/7", "mainnet/123/8"}
    assert sum("missing" in r.message for r in caplog.records) == 2

    up = _scripted_urlopen([])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", up)
    pending.deliver("https://discord.invalid/webhook", now=0.0)
    assert len(up.calls) == 2
    assert any("Upkeep 7 was registered" in c and "round 51234567" in c for c in up.calls)
    assert any("Upkeep 8 was registered" in c for c in up.calls)
    assert all("operator decision within 24 hours" in c for c in up.calls)
    assert json.loads(path.read_text()) == {}


def test_retry_pacing_survives_a_restart(tmp_path, monkeypatch) -> None:
    """A crash-looping unit restarts more often than the retry window and
    used to re-post every record, three attempts each, on every start."""
    from scripts import notifier

    path = tmp_path / "pending.json"
    pending = notifier.PendingStrangers(path)
    pending.add("testnet", 1, _stranger_event(7), current_round=500)
    down = _scripted_urlopen([500] * 100)
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", down)
    pending.deliver("https://discord.invalid/webhook", now=1_000.0)
    assert len(down.calls) == notifier.POST_ATTEMPTS
    assert json.loads(path.read_text())["testnet/1/7"]["last_attempt"] == 1_000.0

    # Restart inside the window: nothing. Restart past it: one more attempt.
    notifier.PendingStrangers.load(path).deliver("https://discord.invalid/webhook", now=1_100.0)
    assert len(down.calls) == notifier.POST_ATTEMPTS
    notifier.PendingStrangers.load(path).deliver(
        "https://discord.invalid/webhook", now=1_000.0 + notifier.STRANGER_RETRY_SECONDS)
    assert len(down.calls) == 2 * notifier.POST_ATTEMPTS

    # A record never attempted is still posted on the spot after a restart.
    fresh = notifier.PendingStrangers(tmp_path / "fresh.json")
    fresh.add("testnet", 1, _stranger_event(8), current_round=500)
    assert json.loads((tmp_path / "fresh.json").read_text())["testnet/1/8"]["last_attempt"] is None
    notifier.PendingStrangers.load(tmp_path / "fresh.json").deliver(
        "https://discord.invalid/webhook", now=5.0)
    assert len(down.calls) == 3 * notifier.POST_ATTEMPTS


def test_the_comments_state_the_numbers_they_describe() -> None:
    source = NOTIFIER_SOURCE.read_text()
    assert "three attempts, so two retries" in source
    assert "at least five minutes, not exactly five" in source
    assert "two-hourly" in source and "absence for a day" in source


# --- a node the bot would refuse is refused here too -------------------------

def test_an_unrecoverable_node_error_stops_the_notifier_after_one_delivery(monkeypatch, tmp_path) -> None:
    """The retry clause swallowed `UnrecoverableError` with a warning and spun,
    which is a watcher that looks alive and watches nothing. The bot exits 2 on
    it; so does this now, after posting whatever stranger alert is owed."""
    from types import SimpleNamespace

    from scripts import notifier
    from scripts.keeper_bot import UnrecoverableError

    state = tmp_path / "notifier.json"
    notifier.PendingStrangers(notifier.pending_path(state)).add(
        "testnet", 1, _stranger_event(7), current_round=500)

    def legacy_listing(algod, app_id):
        raise UnrecoverableError("The node answered the box listing without a round")

    # Discord is down at the top of the loop and back by the time the node fails.
    urlopen = _scripted_urlopen([500] * notifier.POST_ATTEMPTS)
    clock = _Clock()
    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=_ScriptedAlgod(scans=5))))
    monkeypatch.setattr(notifier, "scan_upkeeps", legacy_listing)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(notifier.time, "sleep", clock.sleep)
    monkeypatch.setattr(notifier.time, "time", clock.time)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    with pytest.raises(SystemExit) as stop:
        notifier.main(["--network", "testnet", "--app-id", "1", "--ours", OURS,
                       "--state-file", str(state), "--poll-seconds", "1"])
    assert stop.value.code == 2
    # Pacing still applies on the way out: the attempt at the top of the loop
    # was seconds ago, so the exit-path attempt is skipped and the record is
    # left, with its last attempt, for the restart systemd will give it.
    assert len(urlopen.calls) == notifier.POST_ATTEMPTS
    left = notifier.PendingStrangers.load(notifier.pending_path(state)).records
    assert set(left) == {"testnet/1/7"} and left["testnet/1/7"]["last_attempt"] == 1_000_000.0


def test_an_unrecoverable_error_is_matched_by_name_after_a_module_reload() -> None:
    from scripts import notifier

    class UnrecoverableError(RuntimeError):  # a fresh class object, as a reload would make
        pass

    assert notifier._is_unrecoverable(UnrecoverableError("x"))
    assert not notifier._is_unrecoverable(RuntimeError("x"))


def test_an_ordinary_node_error_is_still_retried(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from scripts import notifier

    class Flaky(_StoppingAlgod):
        calls = 0

        def status(self) -> dict:
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("timed out")
            raise _Halt()

    algod = Flaky()
    clock = _Clock()
    monkeypatch.setattr(notifier.net, "connect",
                        lambda network: SimpleNamespace(client=SimpleNamespace(algod=algod)))
    monkeypatch.setattr(notifier.time, "sleep", clock.sleep)
    monkeypatch.setattr(notifier.time, "time", clock.time)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    notifier.main(["--network", "testnet", "--app-id", "1", "--no-state", "--poll-seconds", "1"])
    assert algod.calls == 3


# --- round three: delivery must never block observation ----------------------

def test_a_malformed_pending_record_is_coerced_not_fatal(tmp_path, caplog) -> None:
    """`"last_attempt": "yesterday"` raised TypeError as the first statement of
    the scan loop, and the retry clause slept through it forever."""
    import logging

    from scripts import notifier

    path = tmp_path / "pending.json"
    path.write_text(json.dumps({
        "testnet/1/7": {"upkeep_id": "7", "first_seen_round": "5", "first_seen_at": 12,
                        "last_attempt": "yesterday", "text": "kept"},
        "testnet/1/8": {"upkeep_id": 8.0, "first_seen_round": 5, "first_seen_at": "x",
                        "last_attempt": 1_000, "text": ["not", "a", "string"]},
    }))
    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        records = notifier.PendingStrangers.load(path).records
    seven, eight = records["testnet/1/7"], records["testnet/1/8"]
    assert (seven["upkeep_id"], seven["first_seen_round"], seven["first_seen_at"]) == (7, 5, "unknown")
    assert seven["last_attempt"] is None and seven["text"] == "kept"
    assert (eight["upkeep_id"], eight["last_attempt"]) == (8, 1_000.0)
    assert "Upkeep 8 was registered" in eight["text"], "an unreadable text is replaced, not kept"
    repairs = [r.message for r in caplog.records if "is not" in r.message]
    assert any("last_attempt='yesterday'" in m for m in repairs)
    assert any("text=" in m for m in repairs)
    # Sorting and the clock arithmetic now work on what was loaded.
    sorted(records, key=lambda k: (records[k]["first_seen_round"], k))


def test_a_malformed_pending_file_does_not_stop_scanning(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    state = tmp_path / "notifier.json"
    notifier.pending_path(state).write_text(json.dumps({
        "testnet/1/7": {"upkeep_id": 7, "first_seen_round": "5", "first_seen_at": "then",
                        "last_attempt": "yesterday", "text": "🚨 Upkeep 7 was registered by a stranger"},
    }))
    before = [upkeep()]
    after = [upkeep(times_executed=1, balance=8_000, next_execution_round=1_010)]
    urlopen = _scripted_urlopen([])
    _run_main(monkeypatch, tmp_path, registries=[before, after], urlopen=urlopen, clock=_Clock())
    assert "Upkeep 7 was registered" in urlopen.calls[0], "delivered, on the first loop"
    assert any("Upkeep 1 executed" in c for c in urlopen.calls), "and the scans went ahead"
    assert notifier.PendingStrangers.load(notifier.pending_path(state)).records == {}


def test_a_fault_in_delivery_is_logged_and_the_scan_still_runs(monkeypatch, tmp_path, caplog) -> None:
    """The belt to the coercion's braces: whatever `deliver` might raise, the
    node is still asked what happened."""
    import logging

    from scripts import notifier

    real_deliver = notifier.PendingStrangers.deliver
    calls = {"n": 0}

    def flaky_deliver(self, webhook, now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("unsupported operand")
        return real_deliver(self, webhook, now)

    monkeypatch.setattr(notifier.PendingStrangers, "deliver", flaky_deliver)
    before = [upkeep()]
    after = [upkeep(times_executed=1, balance=8_000, next_execution_round=1_010)]
    urlopen = _scripted_urlopen([])
    with caplog.at_level(logging.ERROR, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[before, after], urlopen=urlopen, clock=_Clock())
    assert any("Delivering pending stranger alerts failed" in r.message for r in caplog.records)
    assert any("Upkeep 1 executed" in c for c in urlopen.calls)


def test_an_unwritable_state_directory_does_not_kill_the_summary(monkeypatch, tmp_path, caplog) -> None:
    """The snapshot save sat before the scan counter and the summary block, so
    with EACCES every scan ended in the retry clause: executions announced,
    zero summaries, and the summary is the runbook's liveness signal."""
    import logging

    from scripts import notifier

    def refuse_to_write(path, payload) -> None:
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr(notifier, "_write_json", refuse_to_write)
    registry = [upkeep()]
    urlopen = _scripted_urlopen([])
    with caplog.at_level(logging.ERROR, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[registry, registry, registry],
                  urlopen=urlopen, clock=_Clock(), extra=("--summary-every", "1"))
    assert sum("**Registry**" in c for c in urlopen.calls) == 3
    assert sum("saving the snapshot" in r.message for r in caplog.records) == 3, "said every time"


def test_mainnet_refuses_no_state(monkeypatch, capsys) -> None:
    """In memory, an undelivered stranger alert dies with the process, and
    systemd restarting the process is the ordinary case: F01 made vacuous."""
    from scripts import notifier

    _connected(monkeypatch)
    ours = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
    with pytest.raises(SystemExit):
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once", "--no-state",
                       "--ours", ours, "--stdout"])
    assert "--no-state is refused on MainNet" in capsys.readouterr().err


def test_mainnet_starts_with_a_state_file(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    _connected(monkeypatch)
    ours = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
    with pytest.raises(_Stop):  # past every guard, into the first scan
        notifier.main(["--network", "mainnet", "--app-id", "1", "--once", "--ours", ours,
                       "--stdout", "--state-file", str(tmp_path / "notifier.json")])


def test_the_attribution_comment_names_the_quota_cost() -> None:
    source = NOTIFIER_SOURCE.read_text()
    assert "meters by bytes" in source and "our own node" in source


# --- round four: the snapshot file gets the pending file's treatment --------

def test_a_snapshot_with_string_numbers_is_repaired_on_load(tmp_path, caplog) -> None:
    """`"balance": "10000"` made `diff` raise at `_burst_cost` on every loop."""
    import logging

    from scripts import notifier

    path = tmp_path / "notifier.json"
    good = snapshot([upkeep(upkeep_id=1), upkeep(upkeep_id=2)]).to_json()
    good["upkeeps"]["1"]["balance"] = "10000"
    good["upkeeps"]["1"]["fee_cap"] = 12000.0
    good["upkeeps"]["2"]["balance"] = "lots"           # beyond repair
    good["upkeeps"]["3"] = "not an object"              # beyond repair
    good["dormant"] = ["2", "x", 4]
    good["last_round"] = "1000"
    path.write_text(json.dumps(good))

    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        restored = load(path)
    assert set(restored.upkeeps) == {1}
    assert restored.upkeeps[1]["balance"] == 10_000 and restored.upkeeps[1]["fee_cap"] == 12_000
    assert restored.dormant == {2, 4}
    assert restored.last_round == 1_000
    messages = [r.message for r in caplog.records]
    assert any("balance='10000' read as 10000" in m for m in messages)
    assert any("upkeep 2 has balance='lots'" in m for m in messages)
    assert any("'3' is not an upkeep" in m for m in messages)
    assert any("'x' is not an upkeep id" in m for m in messages)
    # And the point: arithmetic on it works.
    diff(restored, snapshot([upkeep(upkeep_id=1, times_executed=1, balance=6_000)]))


def test_as_int_refuses_what_is_not_a_whole_number() -> None:
    from scripts.notifier import _as_int

    assert [_as_int(v) for v in (5, "5", " 5 ", 5.0)] == [5, 5, 5, 5]
    assert [_as_int(v) for v in (True, 5.5, "5.0", "five", None, [5], float("inf"))] == [None] * 7


def test_a_corrupt_snapshot_does_not_stop_scanning_or_hide_a_stranger(monkeypatch, tmp_path) -> None:
    from scripts import notifier

    state = tmp_path / "notifier.json"
    broken = snapshot([upkeep()], current_round=1_000).to_json()
    broken["upkeeps"]["1"]["balance"] = "12000"
    state.write_text(json.dumps(broken))

    after = [upkeep(times_executed=1, balance=8_000, next_execution_round=1_010),
             upkeep(upkeep_id=7, creator=STRANGER, interval_rounds=1_000)]
    urlopen = _scripted_urlopen([])
    _run_main(monkeypatch, tmp_path, registries=[after], urlopen=urlopen, clock=_Clock())
    assert "Upkeep 7 was registered by" in urlopen.calls[0]
    assert any("Upkeep 1 executed" in c for c in urlopen.calls), "the repaired snapshot diffed"
    assert json.loads(state.read_text())["last_round"] == 1_100, "and the scan counted"


def test_a_stranger_is_recorded_even_when_diff_raises(monkeypatch, tmp_path, caplog) -> None:
    """Observation of a stranger must not depend on the previous snapshot
    being sane: a stranger is a fact about the current registry."""
    import logging

    from scripts import notifier

    def broken_diff(previous, current, known_creators=frozenset()):
        raise TypeError("unsupported operand type(s) for -: 'str' and 'int'")

    monkeypatch.setattr(notifier, "diff", broken_diff)
    state = tmp_path / "notifier.json"
    registry = [upkeep(), upkeep(upkeep_id=7, creator=STRANGER, interval_rounds=1_000)]
    urlopen = _scripted_urlopen([])
    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[registry, registry], urlopen=urlopen, clock=_Clock())
    assert any("unsupported operand" in r.message for r in caplog.records), "the error is still reported"
    # Recorded during the first scan, delivered at the top of the second.
    assert [c for c in urlopen.calls if "Upkeep 7 was registered by" in c]
    assert notifier.PendingStrangers.load(notifier.pending_path(state)).records == {}


def test_strangers_in_needs_no_previous_snapshot() -> None:
    from scripts.notifier import strangers_in

    current = snapshot([upkeep(), upkeep(upkeep_id=7, creator=STRANGER)])
    events = strangers_in(current, frozenset({OURS}))
    assert [(e.kind, e.upkeep_id) for e in events] == [("stranger", 7)]
    assert events[0].text == _stranger_event(7).text, "the same alert diff would have raised"
    assert strangers_in(current, frozenset()) == [], "no allowlist, nobody is a stranger"


def test_a_last_attempt_in_the_future_does_not_mute_the_alert(tmp_path, monkeypatch, caplog) -> None:
    """`Infinity` is legal JSON to Python, and a clock stepped back leaves a
    real timestamp ahead of now; either way `now - last` never reached the
    window and the alert was muted for good."""
    import logging

    from scripts import notifier

    monkeypatch.setattr(notifier.time, "time", lambda: 1_000_000.0)
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({
        "testnet/1/7": {"upkeep_id": 7, "first_seen_round": 1, "first_seen_at": "x",
                        "last_attempt": float("inf"), "text": "seven"},
        "testnet/1/8": {"upkeep_id": 8, "first_seen_round": 1, "first_seen_at": "x",
                        "last_attempt": 1_000_000.0 + 7 * 3600, "text": "eight"},
    }))
    with caplog.at_level(logging.WARNING, logger=notifier.logger.name):
        pending = notifier.PendingStrangers.load(path)
    assert pending.records["testnet/1/7"]["last_attempt"] is None
    assert pending.records["testnet/1/8"]["last_attempt"] is None
    assert sum("not a past time" in r.message for r in caplog.records) == 2

    up = _scripted_urlopen([])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", up)
    pending.deliver("https://discord.invalid/webhook")
    assert sorted(up.calls) == ["eight", "seven"]

    # And at run time, should the clock move after load.
    again = notifier.PendingStrangers(path, {"testnet/1/9": {
        "upkeep_id": 9, "first_seen_round": 1, "first_seen_at": "x",
        "last_attempt": 1_000_000.0 + 7 * 3600, "text": "nine"}})
    again.deliver("https://discord.invalid/webhook", now=1_000_000.0)
    assert up.calls[-1] == "nine"


def test_the_snapshot_does_not_advance_on_disk_past_an_unrecorded_sighting(
    monkeypatch, tmp_path, caplog
) -> None:
    """If the pending write fails and the snapshot write succeeds, the disk
    says "already seen" about a stranger no file records, which is the one
    loss this arrangement exists to rule out."""
    import logging

    from scripts import notifier

    def refuse(self) -> None:
        raise OSError(13, "Permission denied", str(self.path))

    monkeypatch.setattr(notifier.PendingStrangers, "save", refuse)
    state = tmp_path / "notifier.json"
    quiet = [upkeep()]
    with_stranger = [upkeep(), upkeep(upkeep_id=7, creator=STRANGER, interval_rounds=1_000)]
    urlopen = _scripted_urlopen([])
    with caplog.at_level(logging.ERROR, logger=notifier.logger.name):
        _run_main(monkeypatch, tmp_path, registries=[quiet, with_stranger], urlopen=urlopen,
                  clock=_Clock())
    on_disk = json.loads(state.read_text())
    assert on_disk["last_round"] == 1_100, "the first scan's snapshot, not the second's"
    assert "7" not in on_disk["upkeeps"]
    assert any("Not advancing the snapshot on disk" in r.message for r in caplog.records)
    # Delivery itself was not held back: memory moved on, only the disk waited.
    assert any("Upkeep 7 was registered by" in c for c in urlopen.calls)


def test_a_successful_pending_write_lets_the_snapshot_advance_again(tmp_path, monkeypatch) -> None:
    """The hold-back is per write, not for the rest of the process."""
    from scripts import notifier

    pending = notifier.PendingStrangers(tmp_path / "pending.json")
    real_write = notifier._write_json
    monkeypatch.setattr(notifier, "_write_json",
                        lambda path, payload: (_ for _ in ()).throw(OSError(13, "denied", str(path))))
    pending.add("testnet", 1, _stranger_event(7), current_round=1)
    assert pending.unsaved is True
    monkeypatch.setattr(notifier, "_write_json", real_write)  # the directory was fixed
    pending.add("testnet", 1, _stranger_event(8), current_round=2)
    assert pending.unsaved is False
    assert set(json.loads((tmp_path / "pending.json").read_text())) == {"testnet/1/7", "testnet/1/8"}
