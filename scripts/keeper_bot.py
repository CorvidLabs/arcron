"""Permissionless keeper bot for the Keeper network.

Scans the Keeper app's upkeep boxes each round, executes every upkeep that
is due and funded, and collects the per-execution fees. Loops block-by-block
by default, or runs a single scan with --once.

Picks its network with --network (or ARCRON_NETWORK), loading .env.localnet
or .env.testnet. Signs as the account from KEEPER_MNEMONIC if set, else
DEPLOYER_MNEMONIC; execution fees are paid to that account. On LocalNet both
come from KMD, so no mnemonic is needed.

Run:  poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]

`--align S` holds the first scan until the next whole multiple of S seconds,
which is how two keepers started from the same schedule end up competing for
the same upkeep instead of taking turns at it.
"""

import argparse
import base64
import contextlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType

import algokit_utils
from algosdk import constants, encoding

from scripts import network as net
from scripts.keeper_backoff import Backoff, default_state_path
from smart_contracts.artifacts.keeper.keeper_client import (
    ExecuteArgs,
    KeeperClient,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# The ARC-4 head of an Upkeep, in bytes. Also the value the contract writes as
# the offset to the argument list, which makes it a version fingerprint.
HEAD_BYTES = 130
# Covers the two inner transactions (app call + keeper payment); the outer
# fee is the standard 1,000 µALGO.
EXTRA_FEE_MICROALGO = 2_000
# The bonus transfer, when an upkeep pays one and this keeper can receive
# it. Overpaying is harmless: an unused fee is simply not charged.
BONUS_FEE_MICROALGO = 1_000
# A ceiling on the outer fee. Suggested params come from whatever node the
# operator pointed at, and verifying the genesis id proves which network that
# node speaks for, not that it is honest or healthy. An endpoint returning an
# inflated per-byte fee would otherwise be signed on the next execution. Ten
# times the minimum leaves room for genuine congestion pricing and still
# refuses a number that could only be wrong.
MAX_OUTER_FEE_MICROALGO = 10_000
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
    """A condition no amount of retrying fixes; exit non-zero and be noticed."""


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
    # The account that registered it, and the only one that can cancel it.
    # Decoded but long unused: the box always carried it and this dropped it,
    # so nothing downstream could tell one creator's upkeep from another's.
    # The notifier needs exactly that to say "this one is not ours".
    creator: str
    target_app: int
    interval_rounds: int
    next_execution_round: int
    fee_per_execution: int
    balance: int
    times_executed: int
    policy: int
    fee_cap: int
    last_serviced_round: int
    fee_asset: int
    asset_fee: int
    asset_balance: int


def effective_fee(upkeep: Upkeep, current_round: int) -> int:
    """What `execute` would pay for this upkeep right now.

    The twin of the escalation arithmetic in
    `smart_contracts/keeper/contract.py::execute`. The fee rises linearly from
    the base to the cap over one missed interval and then holds, and lateness
    is measured from the last service rather than from the schedule, so a
    keeper draining a backlog is paid the ceiling once, not once per replay.
    A zero cap means the fee never moves, and an upkeep never bids more than
    it holds: an escrow below the escalated fee drops back to the base fee
    rather than freezing the upkeep at a price it can never pay. A replay of a
    backlog never escalates at all: `next_execution_round <= last_serviced_round`
    means the upkeep was already behind when it last ran.
    """
    base, cap = upkeep.fee_per_execution, upkeep.fee_cap
    if cap <= base or upkeep.next_execution_round <= upkeep.last_serviced_round:
        return base
    interval = max(upkeep.interval_rounds, 1)
    lateness = max(current_round - upkeep.last_serviced_round, 0)
    excess = min(max(lateness - interval, 0), interval)
    fee = base + (cap - base) * excess // interval
    return base if upkeep.balance < fee else fee


def select_due(
    upkeeps: list[Upkeep],
    current_round: int,
    is_blocked=None,
) -> list[Upkeep]:
    """The work a keeper should take, in the order it should take it.

    Ordered by what each upkeep pays *now* rather than by registry order:
    escalation exists to change which work a keeper reaches for, and registry
    order would mean a neglected upkeep stays neglected however far its fee
    has risen. Anything `is_blocked` names is left out entirely, because its target
    is the thing that is broken, and a rising fee does not fix it.
    """
    return sorted(
        (
            upkeep
            for upkeep in upkeeps
            if current_round >= upkeep.next_execution_round
            and upkeep.balance >= effective_fee(upkeep, current_round)
            and not (is_blocked is not None and is_blocked(upkeep.upkeep_id))
        ),
        key=lambda upkeep: (-effective_fee(upkeep, current_round), upkeep.upkeep_id),
    )


def _merge_unnamed_resources(*accessed: dict | None) -> dict:
    """The union of every unnamed resource algod reported, across as many
    `unnamed-resources-accessed` objects as are passed in.

    A target's own resource needs can be attributed to the whole group or to
    the call's single transaction depending on how algod resolves them, so
    both are read (`_resolve_execute_references` passes both) and merged into
    one set of references to attach.
    """
    accounts: list[str] = []
    apps: list[int] = []
    assets: list[int] = []
    boxes: list[tuple[int, bytes]] = []
    extra_box_refs = 0

    def account(address: str) -> None:
        if address not in accounts:
            accounts.append(address)

    def app(app_id: int) -> None:
        if app_id not in apps:
            apps.append(app_id)

    def asset(asset_id: int) -> None:
        if asset_id not in assets:
            assets.append(asset_id)

    def box(app_id: int, name: bytes) -> None:
        if (app_id, name) not in boxes:
            boxes.append((app_id, name))

    for source in accessed:
        if not source:
            continue
        for address in source.get("accounts") or []:
            account(address)
        for app_id in source.get("apps") or []:
            app(int(app_id))
        for asset_id in source.get("assets") or []:
            asset(int(asset_id))
        for entry in source.get("boxes") or []:
            box(int(entry["app"]), base64.b64decode(entry["name"]))
        for holding in source.get("asset-holdings") or []:
            account(holding["account"])
            asset(int(holding["asset"]))
        for local in source.get("app-locals") or []:
            account(local["account"])
            app(int(local["app"]))
        extra_box_refs = max(extra_box_refs, source.get("extra-box-refs") or 0)

    return {
        "accounts": accounts,
        "apps": apps,
        "assets": assets,
        "boxes": boxes,
        "extra_box_refs": extra_box_refs,
    }


def _resolve_execute_references(
    client: KeeperClient, upkeep: Upkeep, extra_fee: int
) -> algokit_utils.CommonAppCallParams:
    """What `execute(upkeep_id)` needs to reach its target, named directly.

    algokit-utils' own resource populator would discover this for us -- it is
    what every execution used to rely on entirely -- but its default spreader
    caps at four direct account references per transaction
    (`MAX_APP_CALL_ACCOUNT_REFERENCES` in algokit-utils) and refuses a fifth
    with "No more transactions below reference limit", even though the AVM
    allows up to six references for a target once the two Arcron itself
    always spends are set aside (the upkeep's own box and the target app; see
    docs/arcron.md). Both of those are already known, so only what the target
    itself reaches for has to be discovered by simulating first, and
    everything is then attached by hand rather than left for that populator,
    which the real send is told not to run at all (see the `execute` call
    site below).
    """
    box_ref = algokit_utils.BoxReference(
        app_id=0, name=b"u" + upkeep.upkeep_id.to_bytes(8, "big")
    )
    base_params = algokit_utils.CommonAppCallParams(
        box_references=[box_ref],
        app_references=[upkeep.target_app],
        extra_fee=algokit_utils.AlgoAmount(micro_algo=extra_fee),
        # A ceiling on what this will sign, rather than trusting the node's
        # suggested per-byte fee.
        max_fee=algokit_utils.AlgoAmount(micro_algo=MAX_OUTER_FEE_MICROALGO + extra_fee),
    )
    simulated = (
        client.new_group()
        .execute(args=ExecuteArgs(upkeep_id=upkeep.upkeep_id), params=base_params)
        .simulate(allow_unnamed_resources=True)
    )
    group_response = simulated.simulate_response["txn-groups"][0]
    accessed = _merge_unnamed_resources(
        group_response.get("unnamed-resources-accessed"),
        *(
            result.get("unnamed-resources-accessed")
            for result in group_response.get("txn-results", [])
        ),
    )
    extra_boxes = [
        algokit_utils.BoxReference(app_id=box_app, name=box_name)
        for box_app, box_name in accessed["boxes"]
    ] + [
        algokit_utils.BoxReference(app_id=0, name=b"")
        for _ in range(accessed["extra_box_refs"])
    ]
    return replace(
        base_params,
        account_references=accessed["accounts"] or None,
        app_references=[upkeep.target_app, *accessed["apps"]],
        asset_references=accessed["assets"] or None,
        box_references=[box_ref, *extra_boxes],
    )


def _as_bytes(value: object) -> bytes:
    # algosdk returns box names/values as bytes or base64 str depending on
    # version; accept both.
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return base64.b64decode(value)  # type: ignore[arg-type]


def _decode_upkeep(upkeep_id: int, raw: bytes) -> Upkeep:
    """Decode a box value of the contract's Upkeep ARC-4 struct.

    ABI head/tail layout (see smart_contracts/keeper/contract.py): a 32-byte
    creator, then the static fields inline, with the dynamic argument list in
    the tail (the offset at bytes [40:42] points to it; the bot doesn't need
    it, since the contract stores and sends it itself). The head is 130 bytes;
    its TypeScript twin is `js/src/upkeep.ts`.

    Rejects anything that is not this struct rather than decoding it. A box
    from an older deployment is shorter, and reading past its end silently
    yields zeros and garbage, and a keeper would then compute a fee from numbers
    that were never in the box. The tail offset is the cheapest possible
    fingerprint: the contract always writes 130 there.
    """
    if len(raw) < HEAD_BYTES + 2:
        raise ValueError(
            f"upkeep {upkeep_id}: box is {len(raw)} bytes, too short to be an Upkeep"
        )
    tail_offset = int.from_bytes(raw[40:42], "big")
    if tail_offset != HEAD_BYTES:
        raise ValueError(
            f"upkeep {upkeep_id}: tail offset is {tail_offset}, not {HEAD_BYTES}; "
            f"this box was written by a different version of the contract"
        )
    return Upkeep(
        upkeep_id=upkeep_id,
        creator=encoding.encode_address(raw[0:32]),
        target_app=int.from_bytes(raw[32:40], "big"),
        interval_rounds=int.from_bytes(raw[42:50], "big"),
        next_execution_round=int.from_bytes(raw[50:58], "big"),
        fee_per_execution=int.from_bytes(raw[58:66], "big"),
        balance=int.from_bytes(raw[66:74], "big"),
        times_executed=int.from_bytes(raw[74:82], "big"),
        policy=int.from_bytes(raw[82:90], "big"),
        fee_cap=int.from_bytes(raw[90:98], "big"),
        last_serviced_round=int.from_bytes(raw[98:106], "big"),
        fee_asset=int.from_bytes(raw[106:114], "big"),
        asset_fee=int.from_bytes(raw[114:122], "big"),
        asset_balance=int.from_bytes(raw[122:130], "big"),
    )


@contextlib.contextmanager
def muffled(active: bool):
    """Keep algokit-utils' own error dump out of a JSON log stream.

    `--log-format json` promises one object per line, and that promise is what
    a log shipper is built on. A rejected execution makes algokit-utils print a
    multi-line traceback through the root logger, which turns the most
    interesting event a keeper has, losing a race, into forty lines of noise
    with the structured record buried in them.

    Nothing is lost by muting it: the bot re-emits the node's own text as the
    `reason` field of the event it raises itself. Left alone in text mode,
    where a person is reading and the traceback is the useful part.
    """
    if not active:
        yield
        return
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _balance(algod, address: str) -> int | None:
    """What an account holds, or None if the node would not say.

    Used either side of an execution to price a lost race, where the answer is
    evidence rather than control flow: a keeper must not stop working because
    it could not measure something.
    """
    try:
        return algod.account_info(address)["amount"]
    except Exception:
        return None


def failure_text(exc: Exception) -> str:
    """Everything the node said, not just what the client chose to show.

    `str(exc)` on algokit-utils' `LogicError` is a rendered summary; the raw
    algod text hangs off `logic_error_str`, and only that carries the
    "inner tx N failed" attribution that separates a target's failure from the
    keeper contract's own. Both are passed on, so neither has to be guessed at.
    """
    raw = getattr(exc, "logic_error_str", None)
    return f"{exc}" if not raw else f"{exc} | {raw}"


def read_upkeep(algod, app_id: int, upkeep_id: int) -> Upkeep | None:
    """One upkeep, or None if its box is gone (cancelled, or never existed).

    Only a genuine "no such box" is None; everything else the node says is
    raised. The difference matters more than it looks: a missing box is read
    as an upkeep that was cancelled mid-flight, which is a lost race and backs
    nothing off. Swallowing every error here would turn a rate-limited public
    endpoint, and free TestNet nodes return 403 under load, into "the upkeep is
    gone". That is how this was found, and a keeper would sail through an
    outage believing it had lost a hundred races.
    """
    name = b"u" + upkeep_id.to_bytes(8, "big")
    try:
        raw = _as_bytes(algod.application_box_by_name(app_id, name)["value"])
    except Exception as exc:
        if getattr(exc, "code", None) == 404 or "box not found" in str(exc).lower():
            return None
        raise
    return _decode_upkeep(upkeep_id, raw)


def registry_moved_on(algod, app_id: int, before: Upkeep) -> tuple[bool | None, Upkeep | None]:
    """Did somebody execute this upkeep while we were reaching for it?

    The contract records every execution in the box itself, so this is the one
    account of a lost race that no target and no error string can influence.
    Returns (moved, upkeep as it stands now); moved is None when the registry
    could not be read at all, which is a node problem rather than an answer.

    A box that has vanished counts as moved on: the upkeep was cancelled
    mid-flight, and there is nothing left to back off.
    """
    try:
        after = read_upkeep(algod, app_id, before.upkeep_id)
    except Exception:
        return None, None
    if after is None:
        return True, None
    moved = (
        after.times_executed > before.times_executed
        or after.next_execution_round > before.next_execution_round
    )
    return moved, after


def _as_address(value: object) -> str | None:
    """A sender field, however this node chose to render it.

    algod's block endpoint returns senders already in base32 (58 characters);
    other responses hand back the 32 raw bytes, or those bytes in base64.
    """
    if isinstance(value, str):
        return value if len(value) == constants.ADDRESS_LEN else _as_address(_as_bytes(value))
    if isinstance(value, (bytes, bytearray)) and len(value) == 32:
        return encoding.encode_address(bytes(value))
    return None


def find_winner(algod, app_id: int, upkeep_id: int, block_round: int) -> str | None:
    """Which keeper executed this upkeep, read out of the block it landed in.

    A losing keeper's own transaction never reaches a block, so the only
    durable record of a race is the winner's. Best effort by design: a node
    that does not serve blocks, or an archival gap, must never turn a lost
    race into an error.
    """
    if block_round <= 0:
        return None
    try:
        block = algod.block_info(block_round).get("block") or {}
        wanted = upkeep_id.to_bytes(8, "big")
        for entry in block.get("txns") or []:
            txn = (entry.get("txn") or {}) if isinstance(entry, dict) else {}
            if txn.get("apid") != app_id:
                continue
            args = txn.get("apaa") or []
            if args and _as_bytes(args[-1]) == wanted:
                return _as_address(txn.get("snd"))
    except Exception:
        return None
    return None


def align_to(period_seconds: int, stop=None) -> None:
    """Block until the next whole multiple of `period_seconds` in UTC.

    Two keepers started from the same schedule do not race: the one that
    finishes setting up first takes every due upkeep, and the other arrives
    seconds later to find nothing due. That is not competition, it is a queue.

    A shared wall-clock barrier turns it into competition, because both scan
    in the same round window and both reach for the same upkeep. Runner clocks
    are NTP-synced, so an absolute instant is something two machines that have
    never met can agree on, which a countdown from each one's own start is not.
    """
    now = time.time()
    target = (int(now) // period_seconds + 1) * period_seconds
    emit(
        "aligning",
        f"Waiting {target - now:.1f}s for the shared {period_seconds}s barrier, "
        f"so a keeper starting elsewhere scans the same round",
        wait_seconds=round(target - now, 1),
        period_seconds=period_seconds,
    )
    while time.time() < target:
        if stop is not None and stop.requested:
            return
        time.sleep(min(0.5, target - time.time()))


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
    """The app to service: --app-id, else KEEPER_APP_ID.

    Deliberately no default. There is no deployment of the current contract,
    and defaulting to a stale one is worse than asking: an older app's boxes
    are a different shape, so a keeper would scan them, decode nothing it
    could trust, and act on it.
    """
    if app_id is not None:
        return app_id
    from_env = os.environ.get("KEEPER_APP_ID")
    if from_env:
        return int(from_env)
    parser.error(
        f"--app-id (or KEEPER_APP_ID) is required on {network}: there is no "
        f"canonical Arcron deployment to default to"
    )


def is_frozen(algod, app_id: int) -> bool:
    """Whether this app's creator has given up the power to replace its programs.

    An app deployed before governance carries no `frozen` key at all and has
    no update path, so a missing flag reads as frozen rather than unknown.
    """
    state = algod.application_info(app_id)["params"].get("global-state", [])
    for entry in state:
        if base64.b64decode(entry["key"]) == b"frozen":
            return int(entry["value"].get("uint", 0)) != 0
    return True


def check_registry(algod, app_id: int) -> int:
    """Report how healthy a registry looks. Returns a process exit code.

    Reads public box state only, with no account and no signing, so this works as an
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
            f"{current_fee} µALGO{escalated} fee. It needs a top-up, not a keeper",
            upkeep_id=upkeep.upkeep_id,
            balance=upkeep.balance,
            fee_per_execution=upkeep.fee_per_execution,
            effective_fee=current_fee,
        )
    for upkeep, overdue in stalled:
        emit(
            "stalled",
            f"  upkeep {upkeep.upkeep_id}: overdue by {overdue} rounds "
            f"({overdue / max(upkeep.interval_rounds, 1):.1f} intervals); "
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
    normally self-sustaining, right until it is empty, at which point it
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
        "--align",
        type=int,
        metavar="SECONDS",
        default=int(os.environ.get("KEEPER_ALIGN_SECONDS", 0)),
        help=(
            "wait for the next UTC multiple of SECONDS before the first scan, so "
            "keepers started independently reach for the same due upkeep in the "
            "same round instead of taking turns (default: %(default)s, off)"
        ),
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=os.environ.get("ARCRON_LOG_FORMAT", "text"),
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
        # While an app is unfrozen its creator can replace the programs and
        # reach every escrow in it. DEPLOYER is usually that creator, so
        # falling back to it here would put the key that can rewrite the
        # contract on a hot machine polling a public endpoint around the
        # clock, to save an operator one environment variable. The fallback
        # exists for a developer against their own throwaway LocalNet app;
        # it has no business on a deployment holding somebody else's money.
        if not is_frozen(algod, app_id):
            raise UnrecoverableError(
                f"Refusing to fall back to DEPLOYER on app {app_id}, which is not frozen. "
                "Its creator can still replace the programs and reach every escrow in it, "
                "so that key must not sign routine executions from a long-running bot. "
                "Set KEEPER_MNEMONIC to a separate account "
                "(deploy/keeper.env.example shows the file this belongs in)."
            )
        try:
            keeper = algorand.account.from_environment("DEPLOYER")
        except Exception as cause:
            # DEPLOYER is the fallback for a developer running this from a
            # checkout. An operator who configured KEEPER_MNEMONIC and got it
            # slightly wrong would otherwise be told that DEPLOYER_MNEMONIC is
            # missing, and go looking for a variable their config never
            # mentions.
            raise UnrecoverableError(
                "No keeper account. Set KEEPER_MNEMONIC to the 25-word mnemonic of the "
                "account that should sign executions and collect the fees "
                "(deploy/keeper.env.example shows the file this belongs in). "
                "DEPLOYER_MNEMONIC is accepted as a fallback when running from a "
                "checkout, and is also unset."
            ) from cause
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

    if args.align > 0:
        align_to(args.align, shutdown)

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
            # Re-read every scan rather than caching: an operator can opt in
            # to a new bonus asset while the bot is running, and should not
            # have to restart it to start earning that bonus.
            opted_in_assets = {
                holding["asset-id"]
                for holding in algod.account_info(keeper.address).get("assets", [])
            }
            due = select_due(
                upkeeps, current, lambda upkeep_id: backoff.blocked(upkeep_id, current)
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
                balance_before = None
                try:
                    # An upkeep offering an ASA bonus sends a third inner
                    # transaction when the bonus is actually paid, and only a
                    # keeper opted in to that asset can receive it. Paying the
                    # base fee would leave exactly those executions a thousand
                    # microAlgos short, so the keeper best placed to earn the
                    # bonus is the one whose call fails, and the upkeep paying
                    # the most is the one that goes unserviced.
                    # The contract pays the bonus only to a keeper opted in
                    # to the asset, so this has to ask the same question the
                    # contract does. Adding the surcharge without checking
                    # meant a keeper that could never receive a bonus paid for
                    # its transfer anyway: Algorand pools fees and does not
                    # refund the unused part, so those executions netted
                    # nothing instead of the full fee.
                    extra_fee = EXTRA_FEE_MICROALGO
                    if (
                        upkeep.fee_asset > 0
                        and upkeep.asset_balance >= upkeep.asset_fee
                        and upkeep.fee_asset in opted_in_assets
                    ):
                        extra_fee += BONUS_FEE_MICROALGO
                    # Read before reaching, so that if this call loses a race
                    # the keeper's own log can say what losing cost it. That
                    # number is the whole argument for running a keeper at all
                    # (docs/arcron.md), and until now nothing but a controlled
                    # experiment had ever measured it.
                    balance_before = _balance(algod, keeper.address)
                    with muffled(as_json):
                        response = client.send.execute(
                            args=ExecuteArgs(upkeep_id=upkeep.upkeep_id),
                            params=_resolve_execute_references(client, upkeep, extra_fee),
                            # Every reference the call needs is already named
                            # directly by _resolve_execute_references, so the
                            # populator has nothing left to add. It is told not
                            # to run at all rather than left to try: its own
                            # spreader still caps at four direct accounts, which
                            # this sidesteps rather than collides with.
                            send_params=algokit_utils.SendParams(
                                populate_app_call_resources=False
                            ),
                        )
                    executed_count += 1
                    backoff.record_success(upkeep.upkeep_id)
                    # Price it at the round it confirmed in, not the round it
                    # was picked in. The contract charges at confirmation, and
                    # while escalation is live those differ.
                    fee = effective_fee(
                        upkeep, int(response.confirmation.get("confirmed-round", current))
                    )
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
                    reason = failure_text(exc)
                    # Ask the registry what happened before deciding what this
                    # failure was. The box is the contract's own record, so it
                    # settles the question the error string can only hint at.
                    moved, after = registry_moved_on(algod, app_id, upkeep)
                    entry = backoff.record_failure(
                        upkeep.upkeep_id,
                        reason,
                        current,
                        upkeep.interval_rounds,
                        advanced=moved,
                    )
                    if entry is None:
                        # Another keeper got there first, or it was cancelled
                        # mid-flight. Nothing was spent and nothing is wrong, and
                        # backing off here would only reduce our coverage.
                        #
                        # A losing transaction is rejected at validation and
                        # never reaches a block, so it leaves nothing on chain
                        # to look up afterwards. This line is the only record
                        # the race ever gets, which is why it carries the
                        # winner and the cost rather than just an apology.
                        balance_after = _balance(algod, keeper.address)
                        spent = (
                            None
                            if balance_before is None or balance_after is None
                            else balance_before - balance_after
                        )
                        # Only name a winner when the registry has actually
                        # moved. Observed on TestNet: a keeper refused while
                        # the winner's transaction was still in the pool read
                        # `last_serviced_round` from *before* this race and
                        # reported the keeper who serviced the upkeep an hour
                        # earlier. A stale attribution is worse than none,
                        # which is the same mistake as reading a target's
                        # error text and believing it.
                        won_at = after.last_serviced_round if (moved and after) else 0
                        winner = find_winner(algod, app_id, upkeep.upkeep_id, won_at)
                        emit(
                            "race_lost",
                            f"Lost upkeep {upkeep.upkeep_id} to "
                            f"{winner or 'another keeper'}"
                            + (f" at round {won_at}" if won_at else "")
                            + f"; forfeited {effective_fee(upkeep, current)} µALGO"
                            + (
                                f" and paid {spent} µALGO to find out"
                                if spent is not None
                                else " (what it cost went unmeasured)"
                            ),
                            round=current,
                            upkeep_id=upkeep.upkeep_id,
                            target_app=upkeep.target_app,
                            winner=winner,
                            won_at_round=won_at or None,
                            fee_forgone=effective_fee(upkeep, current),
                            spent=spent,
                            registry_advanced=moved,
                            # The id of the transaction that was thrown away.
                            # Nothing on chain will ever have it, which is the
                            # claim: an indexer lookup that comes back empty is
                            # how anyone else can check this line was honest.
                            tx_id=getattr(exc, "transaction_id", None),
                            reason=reason[:400],
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
                            registry_advanced=moved,
                            reason=reason[:400],
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
