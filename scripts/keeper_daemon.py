"""Install the keeper as a launchd agent, so it stops depending on GitHub.

`.github/workflows/keeper-bot.yml` asks for a run every thirty minutes and
gets about five a day: GitHub drops scheduled workflows under load, and a
registry whose upkeeps want hourly service is then overdue by arithmetic
rather than by fault. A keeper that watches every block cannot be dropped.

The bot already loops forever and already sweeps; nothing here reimplements
either. What this adds is the launchd wrapper and, more importantly, the two
refusals that an unattended money-signing process needs and a hand-written
plist would not give you:

**A destination is mandatory.** A keeper earns into the same account it signs
from. Left alone for a month that is a hot key with a growing balance and no
reason to hold it, so `--install` will not write a plist until you have either
named a wallet or said `--no-sweep` out loud.

**Whatever the bot would refuse at startup is refused here instead.** A bad
sweep address, the keeper's own address, a destination with no trigger, a
missing app id: the bot rejects all of these before its first scan, and under
launchd a rejection is not an error message, it is a job exiting 2 and
relaunching once a minute in a log nobody is tailing. The first attempt at
this module shipped without the app-id check and did exactly that. The sweep
rules are enforced by calling `keeper_bot._validate_sweep` rather than keeping
a second copy of them.

The mnemonic is never written to the plist. launchd agents live unencrypted in
~/Library/LaunchAgents, so the job runs from the repository root and the bot
reads `.env.<network>` the same way every other script does.

Run:  poetry run python -m scripts.keeper_daemon --status
      poetry run python -m scripts.keeper_daemon --install --sweep-to ADDRESS
      poetry run python -m scripts.keeper_daemon --print --no-sweep
      poetry run python -m scripts.keeper_daemon --uninstall
"""

from __future__ import annotations

import argparse
import logging
import os
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from scripts import network as net

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Reverse-DNS label, per launchd convention. The network is part of it so a
#: TestNet agent and a MainNet agent can coexist without one booting the other
#: out: `launchctl bootout` takes a label, and two jobs sharing one would make
#: stopping the safe one stop the dangerous one instead.
LABEL_PREFIX = "xyz.corvidlabs.arcron.keeper"

#: The label the hand-written plist this replaces used. It is booted out on
#: install, because otherwise a machine that ran the old one ends up with two
#: bots signing from the same key: they race each other, both pay group fees,
#: and only one can win. Nothing else would notice.
LEGACY_LABEL = "com.corvidlabs.arcron-keeper"

#: Seconds launchd waits before restarting a job that exited non-zero. The
#: default is 10, which turns a permanent misconfiguration -- a bad mnemonic,
#: a wrong genesis id -- into six restarts a minute. A keeper that cannot
#: start should fail slowly enough to be read in the log.
THROTTLE_SECONDS = 60

@dataclass(frozen=True)
class DaemonPlan:
    """Everything the plist needs, resolved and checked."""

    label: str
    python: Path
    repo: Path
    log_path: Path
    network: str
    app_id: int | None
    sweep_to: str | None
    sweep_above: int | None
    sweep_every: int | None
    notes: list[str] = field(default_factory=list)

    @property
    def plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def arguments(self) -> list[str]:
        """The bot's argv. No secret appears here; the plist is world-readable."""
        argv = [str(self.python), "-m", "scripts.keeper_bot", "--network", self.network]
        if self.app_id is not None:
            argv += ["--app-id", str(self.app_id)]
        if self.sweep_to:
            argv += ["--sweep-to", self.sweep_to]
            if self.sweep_above is not None:
                argv += ["--sweep-above", str(self.sweep_above)]
            if self.sweep_every is not None:
                argv += ["--sweep-every", str(self.sweep_every)]
        argv += ["--log-format", "json"]
        return argv


def label_for(network: str) -> str:
    return f"{LABEL_PREFIX}.{network}"


def resolve_sweep(
    sweep_to: str | None,
    *,
    no_sweep: bool,
    sweep_above: int | None,
    sweep_every: int | None,
    keeper_address: str | None = None,
) -> tuple[str | None, int | None, int | None, list[str]]:
    """Decide the sweep configuration, or refuse.

    Returns (destination, threshold, period, notes).

    Only the first two refusals are new. Everything about whether a *given*
    destination is usable -- a bad address, the keeper's own address, a
    destination with no trigger -- is `keeper_bot._validate_sweep`, called
    here rather than reimplemented, so the agent cannot be installable with a
    configuration the bot will refuse to start on. That failure mode is the
    reason to check at install time at all: under launchd it appears as a
    restarting job in a log nobody is tailing.
    """
    if no_sweep and sweep_to:
        raise ValueError("--no-sweep and --sweep-to contradict each other; pick one")
    if no_sweep:
        return None, None, None, ["Sweeping is off. Earnings stay in the signing key."]
    if not sweep_to:
        # The bot is happy to run with no destination at all, and for a
        # foreground session that is right. An agent that runs for weeks
        # unattended is where "the earnings are in the signing key" stops
        # being a default and becomes a decision, so make it one.
        raise ValueError(
            "Name a wallet with --sweep-to ADDRESS, or say --no-sweep. A keeper "
            "signs from the account it earns into, and this agent is meant to "
            "run unattended for weeks."
        )

    from scripts import keeper_bot

    probe = argparse.Namespace(
        sweep_to=sweep_to, sweep_above=sweep_above, sweep_every=sweep_every
    )
    try:
        keeper_bot._validate_sweep(probe, keeper_address or "")
    except keeper_bot.UnrecoverableError as error:
        raise ValueError(str(error)) from error
    return sweep_to, sweep_above, sweep_every, []


def find_python(repo: Path) -> Path:
    """The interpreter launchd should run.

    launchd starts with almost no PATH and no shell profile, so `poetry` and a
    bare `python3` are both unreliable there. The virtualenv's own interpreter
    is the only thing guaranteed to have this project's dependencies.
    """
    candidate = Path(sys.executable)
    if repo in candidate.parents:
        return candidate
    venv = repo / ".venv" / "bin" / "python"
    if venv.exists():
        return venv
    raise FileNotFoundError(
        f"No interpreter found inside {repo}. Run `poetry install` first, or "
        f"invoke this from the project's virtualenv."
    )


def resolve_app_id(app_id: int | None, network: str) -> int:
    """The app the agent will service.

    `keeper_bot.resolve_app_id` deliberately has no default -- an older app's
    boxes are a different shape, so a keeper pointed at a stale deployment
    decodes nothing it can trust -- and it enforces that with `parser.error`,
    which under launchd means the agent exits 2 and relaunches once a minute
    forever. Resolved here so the same omission is a sentence in a terminal.
    """
    if app_id is not None:
        return app_id
    from_env = os.environ.get("KEEPER_APP_ID")
    if from_env:
        return int(from_env)
    raise ValueError(
        f"--app-id (or KEEPER_APP_ID) is required on {network}: there is no "
        f"canonical Arcron deployment to default to. The live one is 769891898."
    )


def build_plan(args: argparse.Namespace, *, keeper_address: str | None) -> DaemonPlan:
    repo = Path(__file__).resolve().parent.parent
    app_id = resolve_app_id(args.app_id, args.network)
    sweep_to, sweep_above, sweep_every, notes = resolve_sweep(
        args.sweep_to,
        no_sweep=args.no_sweep,
        sweep_above=args.sweep_above,
        sweep_every=args.sweep_every,
        keeper_address=keeper_address,
    )
    return DaemonPlan(
        label=label_for(args.network),
        python=find_python(repo),
        repo=repo,
        log_path=Path.home() / "Library" / "Logs" / "arcron" / f"keeper-{args.network}.log",
        network=args.network,
        app_id=app_id,
        sweep_to=sweep_to,
        sweep_above=sweep_above,
        sweep_every=sweep_every,
        notes=notes,
    )


def plist_bytes(plan: DaemonPlan) -> bytes:
    """The launchd job, as plist XML.

    `KeepAlive.SuccessfulExit = false` restarts a crash and leaves a clean exit
    alone. The bot only returns zero when it was signalled, so a clean exit
    means somebody stopped it on purpose and it should stay stopped; anything
    else is a fault worth retrying. `ProcessType` is deliberately not
    Background, which `deploy/com.corvidlabs.arcron-keeper.plist` used to set:
    App Nap throttles Background jobs, and a throttled keeper loses races it
    would otherwise win.
    """
    job = {
        "Label": plan.label,
        "ProgramArguments": plan.arguments(),
        "WorkingDirectory": str(plan.repo),
        "EnvironmentVariables": {
            "ARCRON_NETWORK": plan.network,
            # Without this the JSON lines sit in a pipe buffer and the log
            # looks dead for minutes at a time.
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_SECONDS,
        # launchd sends SIGTERM and waits this long before SIGKILL, which
        # lets the scan in flight finish rather than dying mid-group.
        "ExitTimeOut": 30,
        "ProcessType": "Standard",
        "LowPriorityIO": False,
        "StandardOutPath": str(plan.log_path),
        "StandardErrorPath": str(plan.log_path),
    }
    return plistlib.dumps(job)


def describe(plan: DaemonPlan) -> None:
    logger.info("")
    logger.info(f"  label       {plan.label}")
    logger.info(f"  network     {plan.network}")
    logger.info(f"  app id      {plan.app_id}")
    logger.info(f"  interpreter {plan.python}")
    logger.info(f"  working dir {plan.repo}")
    logger.info(f"  log         {plan.log_path}")
    logger.info(f"  plist       {plan.plist_path}")
    if plan.sweep_to:
        trigger = []
        if plan.sweep_above is not None:
            trigger.append(f"above {plan.sweep_above} µALGO")
        if plan.sweep_every is not None:
            trigger.append(f"every {plan.sweep_every}s")
        logger.info(f"  sweeps to   {plan.sweep_to}")
        logger.info(f"  trigger     {' or '.join(trigger)}")
    else:
        logger.info("  sweeps to   (nothing; earnings stay in the keeper account)")
    for note in plan.notes:
        logger.info(f"  note        {note}")
    logger.info("")


def _launchctl(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *argv], capture_output=True, text=True, check=False
    )


def _wait_until_gone(label: str, *, timeout: float = 45.0) -> bool:
    """Block until launchd has finished tearing a label down.

    `bootout` returns before the job is gone: the plist sets `ExitTimeOut` to
    30, so launchd SIGTERMs, waits for the scan in flight, and only then
    releases the label. Bootstrapping into that window fails with the useless
    `Bootstrap failed: 5: Input/output error`, which is how a reinstall
    silently leaves you with no keeper at all.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _launchctl("print", f"gui/{os.getuid()}/{label}").returncode != 0:
            return True
        time.sleep(0.5)
    return False


def _confirm_alive(plan: DaemonPlan, *, settle: float = 4.0) -> None:
    """Say whether the job is actually running, not merely accepted.

    `bootstrap` returning zero means launchd took the plist, not that the bot
    survived argument parsing. The first version of this printed "Installed
    and started" over a job that was exiting 2 and relaunching every sixty
    seconds, which is invisible unless you go and read the log.
    """
    time.sleep(settle)
    result = _launchctl("print", f"gui/{os.getuid()}/{plan.label}")
    if result.returncode != 0:
        logger.error(f"{plan.label} is not loaded moments after bootstrap. Check {plan.log_path}.")
        return
    fields = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        for key in ("state = ", "pid = ", "last exit code = "):
            if stripped.startswith(key):
                fields.setdefault(key.strip(" =").strip(), stripped.split("=", 1)[1].strip())
    exited = fields.get("last exit code")
    if fields.get("pid"):
        logger.info(f"Installed and running: pid {fields['pid']}.")
        return
    if exited and exited not in ("(never exited)", "0"):
        logger.error(
            f"{plan.label} started and exited {exited} within {settle:.0f}s. "
            f"It will relaunch every {THROTTLE_SECONDS}s until fixed. "
            f"The reason is the last lines of {plan.log_path}."
        )
        return
    logger.info(f"Installed; launchd reports state {fields.get('state', 'unknown')}.")


def install(plan: DaemonPlan) -> None:
    plan.plist_path.parent.mkdir(parents=True, exist_ok=True)
    plan.log_path.parent.mkdir(parents=True, exist_ok=True)
    # Boot out any previous copy first: launchd refuses to load a label it
    # already has, and a stale job pointing at an old path would otherwise
    # keep running while this one silently failed to start.
    if _launchctl("print", f"gui/{os.getuid()}/{LEGACY_LABEL}").returncode == 0:
        logger.warning(f"Booting out the superseded {LEGACY_LABEL}; it signs from the same key.")
        _launchctl("bootout", f"gui/{os.getuid()}/{LEGACY_LABEL}")
        _wait_until_gone(LEGACY_LABEL)
    _launchctl("bootout", f"gui/{os.getuid()}/{plan.label}")
    if not _wait_until_gone(plan.label):
        raise RuntimeError(
            f"{plan.label} is still loaded after waiting for it to stop. "
            f"Run `launchctl bootout gui/{os.getuid()}/{plan.label}` and retry."
        )
    plan.plist_path.write_bytes(plist_bytes(plan))
    result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plan.plist_path))
    if result.returncode != 0:
        raise RuntimeError(
            f"launchctl bootstrap failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    _confirm_alive(plan)
    logger.info(f"Follow it with:  tail -f {plan.log_path}")


def uninstall(plan: DaemonPlan) -> None:
    result = _launchctl("bootout", f"gui/{os.getuid()}/{plan.label}")
    if result.returncode == 0:
        logger.info(f"Stopped {plan.label}.")
    else:
        logger.info(f"{plan.label} was not running.")
    if plan.plist_path.exists():
        plan.plist_path.unlink()
        logger.info(f"Removed {plan.plist_path}.")
    logger.info("The log is left in place.")


def status(plan: DaemonPlan) -> None:
    logger.info(f"plist    {'present' if plan.plist_path.exists() else 'absent'}")
    result = _launchctl("print", f"gui/{os.getuid()}/{plan.label}")
    if result.returncode != 0:
        logger.info(f"launchd  not loaded ({plan.label})")
        return
    wanted = ("state =", "pid =", "last exit code =", "runs =")
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(wanted):
            logger.info(f"launchd  {stripped}")
    if plan.log_path.exists():
        size = plan.log_path.stat().st_size
        logger.info(f"log      {plan.log_path} ({size / 1e6:.1f} MB)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None, help="keeper app id")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="write the plist and start it")
    action.add_argument("--uninstall", action="store_true", help="stop it and remove the plist")
    action.add_argument("--status", action="store_true", help="is it loaded, and running")
    action.add_argument("--print", dest="print_only", action="store_true",
                        help="print the plist and the plan; write nothing")
    parser.add_argument("--sweep-to", default=os.environ.get("KEEPER_SWEEP_TO"),
                        help="forward surplus earnings to this wallet")
    parser.add_argument("--no-sweep", action="store_true",
                        help="run without sweeping; earnings stay in the signing key")
    parser.add_argument("--sweep-above", type=int, default=None,
                        help="sweep once the surplus reaches this many µALGO")
    parser.add_argument("--sweep-every", type=int, default=None,
                        help="sweep this many seconds after the last one")
    args = parser.parse_args(argv)

    if args.network == net.MAINNET:
        # A laptop is where the TestNet keeper has lived, and it is being
        # throttled and going to sleep there. MainNet is a VPS (deploy/vps),
        # with the keeper's hot key in /etc/arcron/keeper.env and nowhere near
        # `.env.mainnet`, which is what this job would read. Refused here,
        # before the darwin check, so the answer is the same on every OS.
        parser.error(
            "MainNet is not run from a laptop under launchd. Use deploy/vps/install.sh "
            "(systemd) or deploy/compose.yaml; see docs/hosting.md."
        )

    if sys.platform != "darwin":
        raise SystemExit("launchd is macOS only. On Linux use a systemd unit.")

    if args.status or args.uninstall:
        # Neither reads a key, and neither should fail because .env is missing.
        plan = DaemonPlan(
            label=label_for(args.network),
            python=Path(sys.executable),
            repo=Path(__file__).resolve().parent.parent,
            log_path=Path.home() / "Library" / "Logs" / "arcron" / f"keeper-{args.network}.log",
            network=args.network,
            app_id=args.app_id,
            sweep_to=None,
            sweep_above=None,
            sweep_every=None,
        )
        (status if args.status else uninstall)(plan)
        return

    keeper_address = _keeper_address(args.network)
    if keeper_address is None and not args.no_sweep:
        # Without it, `_validate_sweep` compares the destination against "" and
        # the "you cannot sweep to yourself" refusal silently does not run.
        logger.warning(
            "Could not derive the keeper's address (no KEEPER_MNEMONIC in "
            f".env.{args.network}), so --sweep-to is not being checked against "
            "it. The bot will still refuse at startup, which under launchd is "
            "a relaunch every minute rather than a message."
        )
    if args.sweep_to is None:
        # argparse read the environment before `.env.<network>` was loaded, so
        # a KEEPER_SWEEP_TO set there would otherwise be silently ignored and
        # the operator would be told to name a wallet they had already named.
        args.sweep_to = os.environ.get("KEEPER_SWEEP_TO")
    try:
        plan = build_plan(args, keeper_address=keeper_address)
    except (ValueError, FileNotFoundError) as error:
        # These are operator mistakes with one-line fixes. A traceback buries
        # the sentence that says what to do.
        raise SystemExit(f"{error}")
    describe(plan)
    if args.print_only:
        sys.stdout.write(plist_bytes(plan).decode())
        return
    install(plan)


def _keeper_address(network: str) -> str | None:
    """The address the bot will sign as, if it can be derived without a network.

    Best effort on purpose: this exists only so `--sweep-to` can refuse the
    keeper's own address, and a missing `.env` should not block `--print`.
    """
    try:
        net.load_network(network)
    except Exception:
        # No `.env.<network>`. `--print` should still work, and `--install`
        # will fail later on its own terms when the bot cannot find a key.
        return None
    mnemonic_words = os.environ.get("KEEPER_MNEMONIC")
    if not mnemonic_words:
        return None
    try:
        from algosdk import account, mnemonic

        return account.address_from_private_key(mnemonic.to_private_key(mnemonic_words))
    except Exception:
        return None


if __name__ == "__main__":
    main()
