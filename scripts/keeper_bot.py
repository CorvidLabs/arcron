"""Permissionless keeper bot for the Keeper network.

Scans the Keeper app's upkeep boxes each round, executes every upkeep that
is due and funded, and collects the per-execution fees. Loops block-by-block
by default, or runs a single scan with --once.

Picks its network with --network (or ARCHON_NETWORK), loading .env.localnet
or .env.testnet. Signs as the account from KEEPER_MNEMONIC if set, else
DEPLOYER_MNEMONIC; execution fees are paid to that account. On LocalNet both
come from KMD, so no mnemonic is needed.

Run:  poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]
"""

import argparse
import base64
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import algokit_utils

from scripts import network as net
from scripts.keeper_backoff import Backoff, default_state_path
from smart_contracts.artifacts.keeper.keeper_client import (
    ExecuteArgs,
    KeeperClient,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# The canonical TestNet Keeper app (see README); override with --app-id or
# KEEPER_APP_ID. LocalNet has no canonical app — pass one.
DEFAULT_APP_ID = 769802474
# Covers the two inner transactions (app call + keeper payment); the outer
# fee is the standard 1,000 µALGO.
EXTRA_FEE_MICROALGO = 2_000
# First delay after an algod/endpoint error; it doubles up to the cap, so a
# node that is down does not get hammered and a blip costs almost nothing.
ERROR_RETRY_SECONDS = 5
MAX_ERROR_RETRY_SECONDS = 60
# What one execution costs the keeper: the outer fee plus the pooled extra.
EXECUTION_COST_MICROALGO = 1_000 + EXTRA_FEE_MICROALGO
# The minimum balance every Algorand account must hold.
ACCOUNT_MBR_MICROALGO = 100_000
# Below this a keeper cannot fund even one execution, so it refuses to start
# rather than looking alive while failing to broadcast.
HARD_MINIMUM_MICROALGO = ACCOUNT_MBR_MICROALGO + EXECUTION_COST_MICROALGO
# Default warning floor: about a hundred executions of headroom.
LOW_BALANCE_MICROALGO = ACCOUNT_MBR_MICROALGO + 100 * EXECUTION_COST_MICROALGO
# Scans between heartbeats while looping.
HEARTBEAT_SCANS = 20
# An upkeep overdue by more than this many of its own intervals is a stall.
STALL_INTERVALS = 2
# Catch-up policies, mirroring smart_contracts/keeper/contract.py.
CATCH_UP = 0
SKIP_AHEAD = 1


class UnrecoverableError(RuntimeError):
    """A condition no amount of retrying fixes — exit non-zero and be noticed."""


class Emitter:
    """Human lines by default; one JSON object per line for log shipping.

    A keeper's logs have to answer "did upkeep N fire, and when?" months
    later, so every event carries the round and the upkeep it concerns.
    """

    def __init__(self, as_json: bool = False) -> None:
        self.as_json = as_json

    def __call__(self, event: str, message: str, level: int = logging.INFO, **fields) -> None:
        if self.as_json:
            logger.log(level, json.dumps({"event": event, **fields}, default=str))
        else:
            logger.log(level, message)


emit = Emitter()


class Shutdown:
    """SIGTERM means finish what you are doing, then stop.

    A redeploy must not abandon a half-signed execution, so the flag is
    checked between upkeeps rather than interrupting one.
    """

    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self._request)

    def _request(self, signum: int, frame: FrameType | None) -> None:
        if self.requested:  # a second signal means "now"
            raise KeyboardInterrupt
        self.requested = True
        emit(
            "shutdown_requested",
            f"Signal {signum} received; finishing the current scan then exiting",
            signal=signum,
        )


@dataclass
class Upkeep:
    upkeep_id: int
    target_app: int
    interval_rounds: int
    next_execution_round: int
    fee_per_execution: int
    balance: int
    times_executed: int
    policy: int
    fee_cap: int
    last_serviced_round: int


def effective_fee(upkeep: Upkeep, current_round: int) -> int:
    """What `execute` would pay for this upkeep right now.

    The twin of the escalation arithmetic in
    `smart_contracts/keeper/contract.py::execute`. The fee rises linearly from
    the base to the cap over one missed interval and then holds, and lateness
    is measured from the last service rather than from the schedule — so a
    keeper draining a backlog is paid the ceiling once, not once per replay.
    A zero cap means the fee never moves.
    """
    base, cap = upkeep.fee_per_execution, upkeep.fee_cap
    if cap <= base:
        return base
    interval = max(upkeep.interval_rounds, 1)
    lateness = max(current_round - upkeep.last_serviced_round, 0)
    excess = min(max(lateness - interval, 0), interval)
    return base + (cap - base) * excess // interval


def _as_bytes(value: object) -> bytes:
    # algosdk returns box names/values as bytes or base64 str depending on
    # version; accept both.
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return base64.b64decode(value)  # type: ignore[arg-type]


def _decode_upkeep(upkeep_id: int, raw: bytes) -> Upkeep:
    """Decode a box value of the contract's Upkeep ARC-4 struct.

    ABI head/tail layout (see smart_contracts/keeper/contract.py): a 32-byte
    creator, then the static fields inline, with the dynamic call_data in the
    tail (the offset at bytes [40:42] points to it; the bot doesn't need it —
    the contract stores and sends it itself). The head is 106 bytes; its
    TypeScript twin is `web/src/app/core/upkeep.ts`.
    """
    return Upkeep(
        upkeep_id=upkeep_id,
        target_app=int.from_bytes(raw[32:40], "big"),
        interval_rounds=int.from_bytes(raw[42:50], "big"),
        next_execution_round=int.from_bytes(raw[50:58], "big"),
        fee_per_execution=int.from_bytes(raw[58:66], "big"),
        balance=int.from_bytes(raw[66:74], "big"),
        times_executed=int.from_bytes(raw[74:82], "big"),
        policy=int.from_bytes(raw[82:90], "big"),
        fee_cap=int.from_bytes(raw[90:98], "big"),
        last_serviced_round=int.from_bytes(raw[98:106], "big"),
    )


def scan_upkeeps(algod, app_id: int) -> list[Upkeep]:
    upkeeps: list[Upkeep] = []
    token: str | None = None
    while True:  # paginate the box list
        kwargs = {"next": token} if token else {}
        page = algod.application_boxes(app_id, **kwargs)
        for box in page["boxes"]:
            name = _as_bytes(box["name"])
            if name[:1] != b"u":
                continue
            raw = _as_bytes(algod.application_box_by_name(app_id, name)["value"])
            upkeeps.append(_decode_upkeep(int.from_bytes(name[1:9], "big"), raw))
        token = page.get("next-token") or None
        if not token:
            return upkeeps


def resolve_app_id(parser: argparse.ArgumentParser, app_id: int | None, network: str) -> int:
    """The app to service: --app-id, else KEEPER_APP_ID, else the TestNet app."""
    if app_id is not None:
        return app_id
    from_env = os.environ.get("KEEPER_APP_ID")
    if from_env:
        return int(from_env)
    if network == net.TESTNET:
        return DEFAULT_APP_ID
    parser.error(
        f"--app-id (or KEEPER_APP_ID) is required on {network}; there is no "
        f"canonical app id off TestNet"
    )


def check_registry(algod, app_id: int) -> int:
    """Report how healthy a registry looks. Returns a process exit code.

    Reads public box state only — no account, no signing — so this works as an
    external probe against a keeper you do not control. An upkeep overdue by
    more than a couple of its own intervals means nobody is servicing it.
    """
    current = algod.status()["last-round"]
    upkeeps = scan_upkeeps(algod, app_id)
    stalled: list[tuple[Upkeep, int]] = []
    starved: list[Upkeep] = []
    for upkeep in upkeeps:
        if upkeep.balance < effective_fee(upkeep, current):
            # Not a liveness problem: no keeper can execute this, and none
            # should be blamed for it. Escalation raises this threshold, so an
            # upkeep can starve at a balance its creator thought was enough.
            starved.append(upkeep)
        elif current - upkeep.next_execution_round > STALL_INTERVALS * upkeep.interval_rounds:
            stalled.append((upkeep, current - upkeep.next_execution_round))

    emit(
        "check",
        f"Round {current}: {len(upkeeps)} upkeeps on app {app_id}, "
        f"{len(stalled)} stalled, {len(starved)} starved",
        round=current,
        app_id=app_id,
        upkeeps=len(upkeeps),
        stalled=len(stalled),
        starved=len(starved),
    )
    for upkeep in starved:
        current_fee = effective_fee(upkeep, current)
        escalated = " escalated" if current_fee != upkeep.fee_per_execution else ""
        emit(
            "starved",
            f"  upkeep {upkeep.upkeep_id}: escrow {upkeep.balance} µALGO is below its "
            f"{current_fee} µALGO{escalated} fee — needs a top-up, not a keeper",
            upkeep_id=upkeep.upkeep_id,
            balance=upkeep.balance,
            fee_per_execution=upkeep.fee_per_execution,
            effective_fee=current_fee,
        )
    for upkeep, overdue in stalled:
        emit(
            "stalled",
            f"  upkeep {upkeep.upkeep_id}: overdue by {overdue} rounds "
            f"({overdue / max(upkeep.interval_rounds, 1):.1f} intervals) — "
            f"nobody is servicing it",
            level=logging.WARNING,
            upkeep_id=upkeep.upkeep_id,
            overdue_rounds=overdue,
            intervals_overdue=round(overdue / max(upkeep.interval_rounds, 1), 1),
        )
    return 1 if stalled else 0


def guard_balance(algod, address: str, warn_below: int) -> int:
    """Refuse to run below what it takes to broadcast; warn while it is low.

    A keeper earns its fees into the same account it spends from, so it is
    normally self-sustaining — right until it is empty, at which point it
    cannot earn its way back out. That is the failure this catches.
    """
    balance = algod.account_info(address)["amount"]
    if balance < HARD_MINIMUM_MICROALGO:
        raise UnrecoverableError(
            f"Keeper {address} holds {balance} µALGO, below the "
            f"{HARD_MINIMUM_MICROALGO} µALGO needed to keep its account and pay for "
            f"one execution ({EXECUTION_COST_MICROALGO} µALGO). Fund it before starting."
        )
    if balance < warn_below:
        runs = (balance - ACCOUNT_MBR_MICROALGO) // EXECUTION_COST_MICROALGO
        emit(
            "low_balance",
            f"Keeper balance {balance} µALGO is low: about {runs} execution(s) of "
            f"headroom. Collected fees top it up, but a quiet registry will not.",
            level=logging.WARNING,
            balance=balance,
            executions_remaining=runs,
            warn_below=warn_below,
        )
    return balance


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="run a single scan, then exit"
    )
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="Keeper app id (default: KEEPER_APP_ID, else the TestNet app)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report registry health and exit; signs nothing, executes nothing",
    )
    parser.add_argument(
        "--min-balance",
        type=int,
        default=int(os.environ.get("KEEPER_MIN_BALANCE", LOW_BALANCE_MICROALGO)),
        help="warn below this signer balance in µALGO (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="where to persist backoff state (default: under XDG_STATE_HOME)",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="keep backoff in memory only; nothing is written to disk",
    )
    parser.add_argument(
        "--clear-backoff",
        action="store_true",
        help="forget every backed-off upkeep before scanning",
    )
    parser.add_argument(
        "--retry-now",
        type=int,
        metavar="UPKEEP_ID",
        help="forget one upkeep's backoff before scanning, after fixing its target",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=os.environ.get("ARCHON_LOG_FORMAT", "text"),
        help="one JSON object per line for log shipping (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    global emit
    as_json = args.log_format == "json"
    if as_json:
        # A log shipper parses the whole line, so it must be only the object.
        for handler in logging.getLogger().handlers:
            handler.setFormatter(logging.Formatter("%(message)s"))
    emit = Emitter(as_json=as_json)
    shutdown = Shutdown()
    shutdown.install()

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id, args.network)
    algod = algorand.client.algod

    if args.check:
        # Nothing below this line needs an account, and a probe should not
        # require one.
        raise SystemExit(check_registry(algod, app_id))

    try:
        keeper = algorand.account.from_environment("KEEPER")
    except Exception:
        keeper = algorand.account.from_environment("DEPLOYER")
    state_file = (
        None
        if args.no_state
        else (args.state_file or default_state_path(args.network, app_id))
    )
    backoff = Backoff(state_file)
    if args.clear_backoff:
        emit("backoff_cleared", f"Cleared backoff for {backoff.clear()} upkeep(s)")
    if args.retry_now is not None:
        emit(
            "backoff_cleared",
            f"Cleared backoff for upkeep {args.retry_now}",
            upkeep_id=args.retry_now,
            cleared=backoff.clear(args.retry_now),
        )

    balance = guard_balance(algod, keeper.address, args.min_balance)
    client = KeeperClient(
        algorand=algorand,
        app_id=app_id,
        default_sender=keeper.address,
        default_signer=keeper.signer,
    )
    emit(
        "started",
        f"Keeper {keeper.address} servicing app {app_id}",
        keeper=keeper.address,
        app_id=app_id,
        network=args.network,
        balance=balance,
        backoff_state=str(state_file) if state_file else "memory",
    )

    error_delay = ERROR_RETRY_SECONDS
    executed_count = 0
    scans = 0
    while True:
        if shutdown.requested:
            emit("stopped", "Shutting down cleanly")
            return
        try:
            current = algod.status()["last-round"]
            upkeeps = scan_upkeeps(algod, app_id)
            # Take the work in the order escalation exists to create: what
            # pays most right now, first. Registry order would mean a
            # neglected upkeep stays neglected however far its fee has risen.
            # Anything in backoff is left out entirely — its target is the
            # thing that is broken, and a rising fee does not fix it.
            due = sorted(
                (
                    u
                    for u in upkeeps
                    if current >= u.next_execution_round
                    and u.balance >= effective_fee(u, current)
                    and not backoff.blocked(u.upkeep_id, current)
                ),
                key=lambda u: (-effective_fee(u, current), u.upkeep_id),
            )
            error_delay = ERROR_RETRY_SECONDS
            scans += 1
            emit(
                "scan",
                f"Round {current}: {len(upkeeps)} upkeeps, {len(due)} due",
                round=current,
                upkeeps=len(upkeeps),
                due=len(due),
                skipped=len(backoff.blocked_ids(current)),
            )
            for upkeep in due:
                if shutdown.requested:
                    break
                fee = effective_fee(upkeep, current)
                try:
                    response = client.send.execute(
                        args=ExecuteArgs(upkeep_id=upkeep.upkeep_id),
                        params=algokit_utils.CommonAppCallParams(
                            extra_fee=algokit_utils.AlgoAmount(
                                micro_algo=EXTRA_FEE_MICROALGO
                            )
                        ),
                    )
                    executed_count += 1
                    backoff.record_success(upkeep.upkeep_id)
                    emit(
                        "executed",
                        f"Executed upkeep {upkeep.upkeep_id} "
                        f"(target app {upkeep.target_app}); "
                        f"+{fee} µALGO, "
                        f"next due round {response.abi_return}",
                        round=current,
                        upkeep_id=upkeep.upkeep_id,
                        target_app=upkeep.target_app,
                        fee_collected=fee,
                        base_fee=upkeep.fee_per_execution,
                        escrow_remaining=upkeep.balance - fee,
                        next_due_round=response.abi_return,
                        tx_id=response.tx_id,
                    )
                except Exception as exc:
                    entry = backoff.record_failure(
                        upkeep.upkeep_id, str(exc), current, upkeep.interval_rounds
                    )
                    if entry is None:
                        # Another keeper got there first, or it was cancelled
                        # mid-flight. Nothing was spent and nothing is wrong —
                        # backing off here would only reduce our coverage.
                        emit(
                            "race_lost",
                            f"Upkeep {upkeep.upkeep_id} was already handled by "
                            f"another keeper",
                            round=current,
                            upkeep_id=upkeep.upkeep_id,
                        )
                    else:
                        emit(
                            "execute_failed",
                            f"Upkeep {upkeep.upkeep_id} failed (no fee charged); "
                            f"retrying at round {entry.next_attempt_round} after "
                            f"{entry.failures} failure(s): {exc}",
                            level=logging.WARNING,
                            round=current,
                            upkeep_id=upkeep.upkeep_id,
                            failures=entry.failures,
                            next_attempt_round=entry.next_attempt_round,
                            reason=str(exc)[:400],
                        )
            if scans % HEARTBEAT_SCANS == 0 or args.once:
                # Proof of life, and the number that kills bots silently.
                balance = guard_balance(algod, keeper.address, args.min_balance)
                emit(
                    "heartbeat",
                    f"Heartbeat: round {current}, {len(upkeeps)} upkeeps, "
                    f"{len(due)} due, {executed_count} executed this session, "
                    f"balance {balance} µALGO",
                    round=current,
                    upkeeps=len(upkeeps),
                    due=len(due),
                    executed_session=executed_count,
                    backed_off=len(backoff.blocked_ids(current)),
                    balance=balance,
                )
            if args.once:
                return
            algod.status_after_block(current + 1)
        except UnrecoverableError:
            raise
        except KeyboardInterrupt:
            emit("stopped", "Interrupted; exiting")
            return
        except Exception as exc:
            if args.once:
                raise
            emit(
                "scan_failed",
                f"{exc}; retrying in {error_delay}s",
                level=logging.WARNING,
                reason=str(exc)[:400],
                retry_in_seconds=error_delay,
            )
            time.sleep(error_delay)
            # Back off while the node is unhappy, but recover quickly once it
            # answers again.
            error_delay = min(error_delay * 2, MAX_ERROR_RETRY_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except UnrecoverableError as exc:
        # Exit non-zero so a supervisor, or a cron job's failure mail,
        # surfaces it instead of swallowing it.
        emit("fatal", str(exc), level=logging.ERROR, reason=str(exc))
        raise SystemExit(2)
