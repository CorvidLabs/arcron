"""The daemon is the first thing here that signs unattended for weeks.

Two of these tests are about money going to the wrong place, and two are about
money never moving at all. The rest pin the launchd contract, because a plist
that is subtly wrong fails by not running, which looks exactly like a quiet
week on the registry.
"""

import argparse
import plistlib
from pathlib import Path

import pytest

from scripts import keeper_daemon

# A syntactically valid TestNet address; the checksum has to be real, because
# resolve_sweep validates it the way the SDK will.
WALLET = "3NQY7ZHZO6TDNGQODM4MTLGEJSQ3DBO7ZGJUXFXRUDN7H4J6FH2ODTUVT4"
KEEPER = "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU"


def _plan(**overrides) -> keeper_daemon.DaemonPlan:
    base = dict(
        label="xyz.corvidlabs.arcron.keeper.testnet",
        python=Path("/repo/.venv/bin/python"),
        repo=Path("/repo"),
        log_path=Path("/logs/keeper-testnet.log"),
        network="testnet",
        app_id=769891898,
        sweep_to=WALLET,
        sweep_above=None,
        sweep_every=86_400,
    )
    base.update(overrides)
    return keeper_daemon.DaemonPlan(**base)


# --- the sweep refusals ---------------------------------------------------


def test_a_destination_is_mandatory() -> None:
    # The whole point: an unattended keeper accumulating into its own hot key
    # is the default outcome unless something refuses to let it happen.
    with pytest.raises(ValueError, match="--sweep-to"):
        keeper_daemon.resolve_sweep(
            None, no_sweep=False, sweep_above=None, sweep_every=None
        )


def test_no_sweep_is_an_accepted_answer() -> None:
    dest, above, every, notes = keeper_daemon.resolve_sweep(
        None, no_sweep=True, sweep_above=None, sweep_every=None
    )
    assert dest is None and above is None and every is None
    assert notes


def test_no_sweep_and_a_destination_contradict() -> None:
    with pytest.raises(ValueError, match="contradict"):
        keeper_daemon.resolve_sweep(
            WALLET, no_sweep=True, sweep_above=None, sweep_every=None
        )


def test_a_destination_with_no_trigger_is_refused_at_install_time() -> None:
    # The bot refuses this before its first scan. Under launchd that refusal
    # is a job restarting every minute in a log nobody is tailing, so the same
    # rule has to fire in the terminal that runs --install.
    with pytest.raises(ValueError, match="needs a trigger"):
        keeper_daemon.resolve_sweep(
            WALLET, no_sweep=False, sweep_above=None, sweep_every=None
        )


def test_an_explicit_trigger_is_accepted() -> None:
    dest, above, every, notes = keeper_daemon.resolve_sweep(
        WALLET, no_sweep=False, sweep_above=5_000_000, sweep_every=None
    )
    assert (dest, above, every) == (WALLET, 5_000_000, None)
    assert notes == []


def test_a_bad_address_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid Algorand address"):
        keeper_daemon.resolve_sweep(
            "not-an-address", no_sweep=False, sweep_above=None, sweep_every=86_400
        )


def test_sweeping_to_the_keeper_itself_is_refused() -> None:
    # Valid, plausible, and a recurring fee to move the balance nowhere.
    with pytest.raises(ValueError, match="own address"):
        keeper_daemon.resolve_sweep(
            KEEPER,
            no_sweep=False,
            sweep_above=None,
            sweep_every=86_400,
            keeper_address=KEEPER,
        )


def test_the_refusals_are_the_bots_own_not_a_second_copy() -> None:
    # If these ever diverge, the agent becomes installable with a
    # configuration the bot will not start on -- which is invisible failure.
    from scripts import keeper_bot

    calls: list[tuple[object, str]] = []

    def spy(args, keeper_address):
        calls.append((args, keeper_address))

    original = keeper_bot._validate_sweep
    keeper_bot._validate_sweep = spy
    try:
        keeper_daemon.resolve_sweep(
            WALLET, no_sweep=False, sweep_above=None, sweep_every=86_400,
            keeper_address=KEEPER,
        )
    finally:
        keeper_bot._validate_sweep = original

    assert len(calls) == 1
    args, address = calls[0]
    assert args.sweep_to == WALLET
    assert args.sweep_every == 86_400
    assert address == KEEPER


# --- the plist contract ---------------------------------------------------


def test_no_secret_reaches_the_plist() -> None:
    # ~/Library/LaunchAgents is not a secret store. The bot reads .env itself.
    raw = keeper_daemon.plist_bytes(_plan()).decode()
    assert "MNEMONIC" not in raw
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert set(job["EnvironmentVariables"]) == {"ARCRON_NETWORK", "PYTHONUNBUFFERED"}


def test_sweep_flags_reach_the_bot() -> None:
    argv = _plan().arguments()
    assert "--sweep-to" in argv
    assert argv[argv.index("--sweep-to") + 1] == WALLET
    assert argv[argv.index("--sweep-every") + 1] == "86400"


def test_no_sweep_puts_no_sweep_flags_on_the_command() -> None:
    argv = _plan(sweep_to=None, sweep_every=None).arguments()
    assert not any(a.startswith("--sweep") for a in argv)


def test_the_job_never_passes_once() -> None:
    # --once would make launchd restart a one-shot every minute forever.
    assert "--once" not in _plan().arguments()


def test_a_clean_exit_is_not_restarted() -> None:
    # The bot returns zero only when it was signalled, so a clean exit means a
    # deliberate stop. Bare `KeepAlive: true` restarts that too, which turns
    # `launchctl stop` into a no-op the operator has to discover.
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert job["KeepAlive"] == {"SuccessfulExit": False}
    assert job["RunAtLoad"] is True


def test_a_scan_in_flight_gets_time_to_finish() -> None:
    # Without ExitTimeOut launchd SIGKILLs after its default grace period,
    # which can land between signing a group and submitting it.
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert job["ExitTimeOut"] >= 30


def test_the_restart_throttle_is_slow_enough_to_read() -> None:
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert job["ThrottleInterval"] >= 60


def test_the_job_is_not_background_priority() -> None:
    # App Nap throttles Background jobs; a throttled keeper loses races.
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert job["ProcessType"] != "Background"
    assert job["LowPriorityIO"] is False


def test_the_label_carries_the_network() -> None:
    # bootout takes a label. One shared label would mean stopping the TestNet
    # keeper also stopped a MainNet one, or worse, replaced it.
    assert keeper_daemon.label_for("testnet") != keeper_daemon.label_for("mainnet")
    assert "testnet" in keeper_daemon.label_for("testnet")


def test_the_interpreter_is_the_project_virtualenv() -> None:
    # launchd has no shell profile, so `poetry` and a bare python3 are both
    # unreliable; only the venv interpreter has this project's dependencies.
    job = plistlib.loads(keeper_daemon.plist_bytes(_plan()))
    assert job["ProgramArguments"][0].endswith("/.venv/bin/python")
    assert job["WorkingDirectory"] == "/repo"


def test_find_python_rejects_a_tree_with_no_virtualenv(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        keeper_daemon.find_python(tmp_path)


def test_build_plan_threads_the_refusal_through(tmp_path: Path) -> None:
    args = argparse.Namespace(
        network="testnet", app_id=769891898, sweep_to=None, no_sweep=False,
        sweep_above=None, sweep_every=None,
    )
    with pytest.raises(ValueError, match="--sweep-to"):
        keeper_daemon.build_plan(args, keeper_address=None)


# --- the app id -----------------------------------------------------------


def test_a_missing_app_id_is_refused_at_install_time(monkeypatch) -> None:
    # This shipped once without the check. keeper_bot enforces it with
    # parser.error, so the agent installed cleanly, exited 2, and relaunched
    # every 60 seconds; `launchctl print` said "last exit code = 2" and the
    # log said "--app-id is required". Nothing else said anything.
    monkeypatch.delenv("KEEPER_APP_ID", raising=False)
    with pytest.raises(ValueError, match="--app-id"):
        keeper_daemon.resolve_app_id(None, "testnet")


def test_the_app_id_falls_back_to_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("KEEPER_APP_ID", "769891898")
    assert keeper_daemon.resolve_app_id(None, "testnet") == 769891898


def test_an_explicit_app_id_wins(monkeypatch) -> None:
    monkeypatch.setenv("KEEPER_APP_ID", "1")
    assert keeper_daemon.resolve_app_id(769891898, "testnet") == 769891898


def test_the_app_id_reaches_the_command() -> None:
    argv = _plan().arguments()
    assert argv[argv.index("--app-id") + 1] == "769891898"


def test_mainnet_is_not_a_laptop_job() -> None:
    """The keeper's hot key on MainNet lives in /etc/arcron on a VPS, not beside .env.mainnet."""
    with pytest.raises(SystemExit):
        keeper_daemon.main(["--network", "mainnet", "--status"])
