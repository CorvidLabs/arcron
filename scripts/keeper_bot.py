"""Permissionless keeper bot for the Keeper network.

Reads the Keeper app's upkeep boxes, executes every upkeep that is due and
funded, and collects the per-execution fees. Loops until the next round any
upkeep could need something, or runs a single scan with --once.

It used to scan every couple of rounds and re-read all 33 boxes each time,
which measured at about 211,000 requests a day against a public node
(`docs/reviews/2026-09-01-opus-5-audit-verification.md` §5). `Registry` below
re-reads a box only when a stale copy could change a decision, and the loop
sleeps until the soonest of those rounds: about 3,000 a day, measured the same
way in `tests/test_keeper_bot.py::TestWhatOneDayCosts`.

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

from scripts import keeper_assets, network as net
from scripts.keeper_backoff import Backoff, default_state_path, is_target_refusal
from smart_contracts.artifacts.keeper.keeper_client import (
    ExecuteArgs,
    KeeperClient,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# The ARC-4 head of an Upkeep, in bytes. Also the value the contract writes as
# the offset to the argument list, which makes it a version fingerprint.
HEAD_BYTES = 130


def _env_int_at_import(name: str) -> int | None:
    """An integer from the environment, for constants defined at import time.

    `_env_int` lives with the rest of the argument handling further down, which
    is too late for a module-level constant. Unparseable is treated as unset,
    so a typo falls back to the default rather than to zero.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


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
#
# It cannot tell a lying node from a legitimately large transaction, and there
# is one real case where those look identical. Algorand charges
# `max(min_fee, size x fee_per_byte)`, and a Falcon-signed `execute` is 4,384
# bytes against ed25519's 340 (docs/arcron.md). The per-byte rate is zero
# today, so both pay the minimum and this ceiling is never approached. If that
# rate ever becomes non-zero, a post-quantum keeper would hit this and stop
# rather than overpay.
#
# Stopping is the right way round, and an operator in that position should
# raise this constant rather than remove the guard. `KEEPER_MAX_OUTER_FEE`
# exists so it does not need a code change.
MAX_OUTER_FEE_MICROALGO = _env_int_at_import("KEEPER_MAX_OUTER_FEE") or 10_000
# First delay after an algod/endpoint error; it doubles up to the cap, so a
# node that is down does not get hammered and a blip costs almost nothing.
ERROR_RETRY_SECONDS = 5
MAX_ERROR_RETRY_SECONDS = 60
# What one execution costs the keeper: the outer fee plus the pooled extra.
EXECUTION_COST_MICROALGO = 1_000 + EXTRA_FEE_MICROALGO
# Quoted in the refusal to sweep to one's own address; the real constant
# lives in keeper_sweep, which cannot be imported here without a cycle.
SWEEP_FEE_NOTE = "a transaction fee"
# The minimum balance of an account that holds nothing but ALGO. Only a
# fallback for a node that does not report `min-balance`: the real floor is
# read from algod, because it is not a constant. Every asset opt-in adds
# 100,000, and so does every app and asset the account has created. Measured
# on a live keeper holding eleven assets: 5,439,000, against the 100,000 this
# used to assume, so the bot believed it had 5.34 ALGO more to spend than it
# did and would have found out by failing to broadcast.
ACCOUNT_MBR_MICROALGO = 100_000
# Default warning floor, in *spendable* microAlgos: about a hundred executions
# of headroom. Spendable rather than total, because a total compared against a
# fixed floor is the same bug in a second place: an operator opted in to a few
# assets sails past a threshold their spendable balance never reached.
LOW_BALANCE_MICROALGO = 100 * EXECUTION_COST_MICROALGO
# Scans between heartbeats while looping.
HEARTBEAT_SCANS = 20
# And never longer than this between them, however few scans there have been.
# Scans used to be one a round, so twenty of them was a minute; they are now
# spaced by what the registry is about to need, so on a quiet registry twenty
# could be most of a day. The heartbeat carries the balance check, which is the
# number that kills keepers silently, so it gets a clock of its own.
HEARTBEAT_ROUNDS = 1_286  # about an hour at the measured 2.752 s/round
# An upkeep overdue by more than this many of its own intervals is a stall.
STALL_INTERVALS = 2

# --- how often the loop reads anything --------------------------------------
#
# `docs/reviews/2026-09-01-opus-5-audit-verification.md` §5, and
# `scripts/node_retry.py`, which says plainly that the repair belongs here
# rather than in a retry helper. Measured there: this bot issued about
# **211,000 requests a day** against a public node whose refused quota counter
# stood at 230,824 — one process, essentially the whole allowance. The shape of
# it was 11,543 scans over 63,013 rounds, one every 5.46 rounds, and a scan was
# a status, the box listing, a read of every one of the 33 boxes, and the block
# wait.
#
# Two things were wrong with that and both are about re-reading state that had
# not changed:
#
#   * every box, every scan, to find the handful that were due, and
#   * a scan every couple of rounds whether or not anything could happen in
#     them. The shortest live cadence is 20 rounds and most of the registry
#     runs on 1,286 or 15,428.
#
# What makes caching safe is a property of the contract rather than a guess
# about traffic: `next_execution_round` is written once by `register` and only
# ever *advanced* by `execute` (smart_contracts/keeper/contract.py). No method
# moves it backwards. So a cached box that says "not due until round X" cannot
# be hiding an upkeep that has quietly become due — the true value is X or
# later — and the only staleness a cache can produce is being too eager, never
# too late. `Registry` below turns that into "read a box on the round its
# cached copy stops being able to change a decision".
#
# How much an upkeep that cannot pay is worth re-reading. It is due, so it is
# read every scan under the rule above, and it will never be executable until
# somebody tops it up — twelve of the 32 live upkeeps are in exactly that
# state and have been for 94,000 rounds. A top-up is a person with a wallet,
# not a race, so noticing one within the hour is enough, and that is the whole
# of what this constant relaxes.
STARVED_RECHECK_ROUNDS = 1_286
# Nothing is trusted for ever. If the reasoning above is wrong somewhere, this
# is what bounds how long it can be wrong for, at a cost of one read per box
# per day.
MAX_CACHE_ROUNDS = 30_857  # a day
# The longest the loop will sleep when the registry has nothing coming up. The
# box listing is what reveals a new registration, and `MIN_INTERVAL_ROUNDS` is
# 10, so a brand-new upkeep could in principle be due before this elapses; it
# stays due, and the next scan takes it. This is the ceiling on how late that
# first execution can be, against roughly 480 requests a day to hold it.
MAX_IDLE_ROUNDS = 128
# Within this many rounds of something falling due, wait on algod's own long
# poll: it returns the moment a block appears, which is what decides a race.
# Further out, sleep locally instead — the long poll times out after a minute,
# so sitting out a thousand rounds on it costs a request a minute, and sleeping
# costs nothing. Two rather than four because every extra long poll is a
# request: the local sleep lands within a round or two of the target anyway,
# since `seconds_per_round` is a measured per-network figure and the sleep it
# governs is capped at MAX_SLEEP_SECONDS, which bounds the error a wrong
# estimate can accumulate.
LONG_POLL_ROUNDS = 2
# …but never sleep longer than this in one go, so a wrong seconds-per-round
# estimate, a stalled chain or a dev-mode node that produces no blocks at all
# costs a minute of dozing rather than an unbounded one.
MAX_SLEEP_SECONDS = 300.0
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


def partition_due(
    upkeeps: list[Upkeep],
    current_round: int,
    is_blocked=None,
) -> tuple[list[Upkeep], list[Upkeep]]:
    """The work a keeper should take, and the work backoff is holding back.

    Both halves, from one pass, because the second half had no name and
    therefore no report. An upkeep that is due, funded and skipped is the
    keeper's own blind spot: `docs/reviews/2026-09-01-opus-5-audit-verification.md`
    §3 ends on it — for a liquidation, an oracle or a keep-alive the missed
    window can be worth more than every fee in that document, "and nothing
    meters it". The caller emits one line per upkeep in the second list, which
    is the meter.

    Ordered by what each upkeep pays *now* rather than by registry order:
    escalation exists to change which work a keeper reaches for, and registry
    order would mean a neglected upkeep stays neglected however far its fee
    has risen.
    """
    take: list[Upkeep] = []
    held: list[Upkeep] = []
    for upkeep in upkeeps:
        if current_round < upkeep.next_execution_round:
            continue
        if upkeep.balance < effective_fee(upkeep, current_round):
            continue
        blocked = is_blocked is not None and is_blocked(upkeep.upkeep_id)
        (held if blocked else take).append(upkeep)

    def order(upkeep: Upkeep) -> tuple[int, int]:
        return (-effective_fee(upkeep, current_round), upkeep.upkeep_id)

    return sorted(take, key=order), sorted(held, key=order)


def select_due(
    upkeeps: list[Upkeep],
    current_round: int,
    is_blocked=None,
) -> list[Upkeep]:
    """Just the work to take. See `partition_due` for what is left out and why."""
    return partition_due(upkeeps, current_round, is_blocked)[0]


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


def _box_page(algod, app_id: int, token: "str | None") -> dict:
    """One page of an app's box names, continuing from `token`.

    The continuation cannot go through `algod.application_boxes`. That is what
    this did until 2026-09-01 and it cannot work: algosdk builds that call's
    query string from `limit` alone and forwards every other keyword to
    `algod_request`, whose signature has no `next`. A second page therefore
    raised `TypeError: algod_request() got an unexpected keyword argument
    'next'` rather than paging, and every reader built on this — the bot's own
    scan, the health report, the solvency check — would have stopped at one
    page. It never fired because the live registry is 33 boxes against a
    server maximum in the thousands, and the unit tests passed because a mock
    accepts a keyword the real client rejects.

    So the first page keeps using the typed method, which every other reader
    here calls and every fake implements, and only the continuation drops to
    the request the typed API cannot express.

    Grok 4.6 found it reviewing the branch that added the third reader, by
    checking the fake against the client the production path actually uses.
    """
    if not token:
        # The first page goes through the typed client, which is what every
        # other reader in this repository calls and what every test fakes.
        return algod.application_boxes(app_id)
    return algod.algod_request(
        "GET", f"/applications/{app_id}/boxes", params={"next": token}
    )


def _box_names(algod, app_id: int) -> list[bytes]:
    """Every upkeep box name the app holds, following the pagination.

    Names only: this is one request per page, and it is the cheap half of a
    scan. The expensive half is a read per name, which `Registry` skips when it
    can.
    """
    names: list[bytes] = []
    token: str | None = None
    while True:
        page = _box_page(algod, app_id, token)
        for box in page["boxes"]:
            name = _as_bytes(box["name"])
            # Anyone can pay for a box under a name of their choosing; only the
            # `u`-prefixed ones are the contract's.
            if name[:1] == b"u" and len(name) >= 9:
                names.append(name)
        token = page.get("next-token") or None
        if not token:
            return names


def scan_upkeeps(algod, app_id: int) -> list[Upkeep]:
    """Every upkeep, read fresh. One request per box, plus the listing.

    Still the right thing for a one-shot reader — the health report, the
    top-up planner, the notifier's snapshot, `--check` — where the whole point
    is a consistent picture of the registry as it stands. The bot's loop uses
    `Registry` instead, because it asks the same question thousands of times a
    day and almost nothing changes between two of them.
    """
    return [
        _decode_upkeep(
            int.from_bytes(name[1:9], "big"),
            _as_bytes(algod.application_box_by_name(app_id, name)["value"]),
        )
        for name in _box_names(algod, app_id)
    ]


@dataclass
class Cached:
    """One upkeep box, and the round its bytes were read on."""

    upkeep: Upkeep
    read_at_round: int


class Registry:
    """The upkeep boxes, re-read only when a stale copy could change a decision.

    The bot used to call `scan_upkeeps` every scan: 33 box reads plus the
    listing, thousands of times a day, to find the handful of upkeeps that
    were due. That is most of the 211,000 requests a day
    `docs/reviews/2026-09-01-opus-5-audit-verification.md` §5 measured, and the
    constants above are the argument for each part of the rule.

    **Why a stale box cannot hide a due upkeep.** `next_execution_round` is
    written by `register` and only ever advanced by `execute`; nothing in the
    contract moves it earlier. So a cached copy saying "not due until X" is a
    *lower bound* on the truth, and re-reading at X can only ever be early.
    Everything else follows from that:

      not due yet          nothing about it can be acted on before X, and
                           while it is not due nobody can execute it, so its
                           escrow can only grow (`top_up`) and its schedule
                           cannot move. Read it at X.
      due and funded       about to be attempted. Read it now.
      due and cannot pay   no keeper can execute it whatever it says, and only
                           a top-up changes that. Read it every
                           STARVED_RECHECK_ROUNDS.
      backed off           the bot is not going to attempt it before
                           `next_attempt_round`, so its bytes cannot change a
                           decision before then either.
      cancelled            it leaves the box listing, which is read every scan.
      newly registered     it appears in that listing, and has never been read.

    The one thing a cache must never do is decide *not* to look at something
    that is about to matter, so every rule above is a round at which the copy
    stops being good enough, and the scan clock waits for the soonest of them
    (`next_wake_round`). Reading and waking come off the same number on
    purpose: a bot that slept past the round it had promised to re-read would
    be exactly the bug this class could introduce.
    """

    def __init__(self) -> None:
        self.cached: dict[int, Cached] = {}
        #: Box reads issued since the process started. The heartbeat reports it,
        #: because "how much am I asking of this node" is the question §5 says
        #: nobody could answer without going back through the logs. The
        #: authoritative count is
        #: `tests/test_keeper_bot.py::TestWhatOneDayCosts`, which counts at the
        #: client rather than taking a counter's word for it.
        self.box_reads = 0

    # -- queries ---------------------------------------------------------
    def upkeeps(self) -> list[Upkeep]:
        return [entry.upkeep for entry in self.cached.values()]

    def wanted_at(self, entry: Cached, backoff) -> int:
        """The round this cached copy stops being good enough to decide on."""
        upkeep = entry.upkeep
        wanted = max(upkeep.next_execution_round, backoff.next_attempt_round(upkeep.upkeep_id))
        # Judged against the *base* fee rather than the escalated one, because
        # the contract falls back to base when an escrow cannot cover the
        # escalation (`execute`, and `effective_fee`'s twin of it). An upkeep
        # holding the base fee is executable by somebody; one holding less is
        # executable by nobody, and reading it more often does not change that.
        if upkeep.balance < upkeep.fee_per_execution:
            wanted = max(wanted, entry.read_at_round + STARVED_RECHECK_ROUNDS)
        return min(wanted, entry.read_at_round + MAX_CACHE_ROUNDS)

    def next_wake_round(self, current_round: int, backoff) -> int:
        """The soonest round at which this registry could need anything done.

        Floored at the next round, because nothing on chain changes inside one
        and scanning twice in the same round asks the node the same question
        twice. Capped at MAX_IDLE_ROUNDS so a quiet registry still gets its box
        listing read, which is how a registration nobody told us about arrives.
        """
        soonest = min(
            (self.wanted_at(entry, backoff) for entry in self.cached.values()),
            default=current_round + MAX_IDLE_ROUNDS,
        )
        return max(current_round + 1, min(soonest, current_round + MAX_IDLE_ROUNDS))

    # -- updates ---------------------------------------------------------
    def refresh(self, algod, app_id: int, current_round: int, backoff) -> list[Upkeep]:
        """Bring the cache up to date for this round, reading as little as possible."""
        live: set[int] = set()
        for name in _box_names(algod, app_id):
            upkeep_id = int.from_bytes(name[1:9], "big")
            live.add(upkeep_id)
            entry = self.cached.get(upkeep_id)
            if entry is not None and current_round < self.wanted_at(entry, backoff):
                continue
            raw = _as_bytes(algod.application_box_by_name(app_id, name)["value"])
            self.cached[upkeep_id] = Cached(_decode_upkeep(upkeep_id, raw), current_round)
            self.box_reads += 1
        # Cancelled: the box is gone, and so is any reason to keep its escrow
        # and schedule in mind.
        for gone in set(self.cached) - live:
            del self.cached[gone]
        return self.upkeeps()

    def remember(self, upkeep: Upkeep, current_round: int) -> None:
        """Take a copy somebody else already paid for.

        `registry_moved_on` reads the box after a failure to settle whether a
        race was lost. That read is as fresh as one of this class's own, so
        throwing it away and reading the same box again on the next scan would
        be spending a request to learn what is already in hand.
        """
        self.cached[upkeep.upkeep_id] = Cached(upkeep, current_round)

    def remember_execution(self, upkeep_id: int, next_due: int, current_round: int) -> None:
        """Note that `execute` moved this upkeep's schedule to `next_due`.

        The contract returns the round it rescheduled to, so the one field that
        governs when this box next matters is known without reading it back —
        which is worth a request per execution, and a scan: without it the
        cached copy still says "due", so the bot would wake on the next round
        purely to discover it had already done the work.

        The rest of the copy is now stale — `balance` is the fee too high,
        `times_executed` and `last_serviced_round` are one execution behind —
        and none of it can change a decision before `next_due`, because the
        upkeep is not due until then and `wanted_at` reads it again when it is.
        A balance that is stale *high* is the safe direction as well: it makes
        this look funded, and a funded upkeep is read at its due round rather
        than left to the slower starved recheck.
        """
        entry = self.cached.get(upkeep_id)
        if entry is None or next_due <= entry.upkeep.next_execution_round:
            # No answer, or an answer that would move the schedule backwards.
            # Drop the copy rather than trust it; the next scan reads the box.
            self.cached.pop(upkeep_id, None)
            return
        self.cached[upkeep_id] = Cached(
            replace(entry.upkeep, next_execution_round=next_due), current_round
        )


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


def check_registry(algod, app_id: int, keeper_address: str | None = None) -> int:
    """Report how healthy a registry looks. Returns a process exit code.

    Reads public box state only, with no account and no signing, so this works as an
    external probe against a keeper you do not control. An upkeep overdue by
    more than a couple of its own intervals means nobody is servicing it.

    `keeper_address` is optional and adds the one thing box state alone cannot
    say: which bonuses this particular keeper is leaving behind. It is read,
    never signed for, so it can name a keeper somebody else runs.
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
    report_forgone_bonuses(upkeeps, current, keeper_address, algod)
    return 1 if stalled else 0


def report_forgone_bonuses(
    upkeeps: list[Upkeep], current: int, keeper_address: str | None, algod
) -> list:
    """Name every bonus this keeper is not opted in to earn.

    The leak `docs/design/asa-fees.md` predicted and asked for exactly this
    warning against: an un-opted-in keeper executes normally and takes the
    full ALGO fee, so nothing fails, nothing is logged, and the bonus stays in
    escrow. Without something saying so, the only symptom is earnings that are
    lower than the board says they should be.

    Deliberately not an error. Declining a bonus is a legitimate operator
    decision, and often the right one: opting in commits the keeper to 1,000
    µALGO more on every execution of every upkeep naming that asset, whether
    or not the bonus is worth having. So this reports the break-even and stops
    there. `scripts/keeper_assets.py` is the full account.
    """
    if keeper_address is None:
        return []
    try:
        held = keeper_assets.holdings(algod, keeper_address)
    except Exception:
        # A node that will not serve account state must not turn a registry
        # health check into a failure. The rest of the report stands.
        return []
    missed = keeper_assets.forgone(keeper_assets.positions(upkeeps, opted_in=held))
    for position in missed:
        break_even = position.break_even_micro_algo_per_unit
        emit(
            "bonus_forgone",
            f"  asset {position.asset_id}: {position.live} upkeep(s) are paying a "
            f"bonus {keeper_address} cannot receive, worth "
            f"{position.units_per_day:,.0f} base units a day. Opting in earns it "
            f"and costs {BONUS_FEE_MICROALGO} µALGO per execution, so it pays only "
            f"if a base unit is worth more than "
            f"{keeper_assets.micro_algo(break_even)}",
            level=logging.WARNING,
            asset_id=position.asset_id,
            upkeeps=position.live,
            units_per_day=round(position.units_per_day, 2),
            surcharge_per_day=round(position.surcharge_per_day, 2),
            break_even_micro_algo_per_unit=round(break_even, 6) if break_even else None,
        )
    return missed


def account_floor(algod, address: str) -> tuple[int, int]:
    """What the account holds, and how much of it is minimum balance.

    The floor is read rather than assumed. It is not a constant: it rises by
    100,000 µALGO for every asset the account is opted in to, and again for
    every app and every asset it has created. A keeper opted in to eleven
    bonus assets was measured at 5,439,000 µALGO, so assuming the 100,000 of a
    bare account overstated what it could spend by 5.34 ALGO.

    `ACCOUNT_MBR_MICROALGO` is the fallback for a node that does not report
    the field at all, and it is the only case where a guess is better than
    refusing to start.
    """
    info = algod.account_info(address)
    return int(info["amount"]), int(info.get("min-balance", ACCOUNT_MBR_MICROALGO))


def account_state(algod, address: str) -> tuple[int, int, set[int]]:
    """Balance, the ledger's floor under it, and the assets this keeper can hold.

    One request for all three, because the loop was spending two: one every
    scan to see which bonus assets the keeper had opted in to, and another on
    the heartbeat for the balance guard. They are the same `account_info`
    response and always were, which is a request a scan given away for nothing
    — about 11,500 of them across the 1.97 days
    `docs/reviews/2026-09-01-opus-5-audit-verification.md` §5 measured, and one
    the accounting there missed: it recorded the account read as being on the
    heartbeat, one scan in twenty, when the loop was making it every scan. So
    the 211,000 a day it landed on is a floor rather than a ceiling.
    """
    info = algod.account_info(address)
    return (
        int(info["amount"]),
        int(info.get("min-balance", ACCOUNT_MBR_MICROALGO)),
        {int(holding["asset-id"]) for holding in info.get("assets", [])},
    )


def sleep_until(seconds: float, stop=None) -> None:
    """Doze for `seconds`, in slices short enough that SIGTERM still lands."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if stop is not None and stop.requested:
            return
        time.sleep(min(0.5, deadline - time.time()))


def wait_for_work(algod, current_round: int, target_round: int, seconds_per_round: float, stop=None) -> dict:
    """Block until `target_round`, and hand back the node's status there.

    Two ways to wait, and the choice between them is about requests rather than
    precision. algod's `wait-for-block-after` long poll returns the instant a
    block appears, which is exactly what a keeper wants in the rounds around an
    upkeep falling due, because whoever is in the first block wins — but it
    gives up after a minute, so sitting out a thousand rounds on it costs a
    request a minute for the best part of an hour. A local sleep costs nothing
    and knows nothing. So the long stretch is slept through and only the last
    LONG_POLL_ROUNDS are watched.

    The waiting loops **here** rather than returning to the caller each time.
    Going back would make every long poll a whole scan — a box listing to be
    told nothing had changed — which measured at ten requests per retry against
    a refusing target instead of five.

    It gives up after MAX_SLEEP_SECONDS whether or not the target was reached,
    so the caller still gets its turn: a seconds-per-round estimate that is
    simply wrong ends up here, and it should not be able to hold a keeper
    indefinitely. It also gives up the moment a long poll comes back with the
    same round it went in with, which is what a stalled chain looks like — and
    what LocalNet looks like all the time, since a dev-mode node produces a
    block per transaction and none at all in between.

    The status this returns is the loop's clock for the next scan. The old loop
    called `status()` at the top and `status_after_block()` at the bottom, and
    the second answer already contains everything the first one would have
    said, which is one request a scan given away.
    """
    started = time.monotonic()
    status = None
    round_now = current_round
    while round_now < target_round:
        remaining = target_round - round_now
        if remaining > LONG_POLL_ROUNDS:
            sleep_until(
                min((remaining - LONG_POLL_ROUNDS) * seconds_per_round, MAX_SLEEP_SECONDS),
                stop,
            )
            status = algod.status()
        else:
            # `status_after_block(round_now)` returns as soon as a round later
            # than this one exists, so it always makes progress and never waits
            # out a round the bot has already seen.
            status = algod.status_after_block(round_now)
            if int(status["last-round"]) == round_now:
                # The node's own minute elapsed and no block arrived. Nothing
                # is going to change by asking again immediately, and the
                # caller may have a shutdown to honour.
                break
        round_now = int(status["last-round"])
        if (stop is not None and stop.requested) or time.monotonic() - started >= MAX_SLEEP_SECONDS:
            break
    return status if status is not None else algod.status()


def _env_int(name: str) -> int | None:
    """An integer from the environment, or None when unset or unreadable.

    Unset and unparseable are the same answer on purpose: a sweep setting that
    cannot be read must not fall back to a number nobody chose.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not an integer.", name, raw)
        return None


def _validate_sweep(args, keeper_address: str) -> None:
    """Refuse a sweep configuration that cannot do what it says.

    Checked before the first scan rather than at the first heartbeat, because
    a keeper that runs for an hour and then reveals it has been misconfigured
    the whole time is worse than one that refuses to start.
    """
    if not encoding.is_valid_address(args.sweep_to):
        raise UnrecoverableError(
            f"--sweep-to {args.sweep_to!r} is not a valid Algorand address."
        )
    if args.sweep_to == keeper_address:
        raise UnrecoverableError(
            "--sweep-to is the keeper's own address. That sweeps nothing and "
            f"burns {SWEEP_FEE_NOTE} every period for as long as it runs."
        )
    if args.sweep_above is None and args.sweep_every is None:
        raise UnrecoverableError(
            "--sweep-to needs a trigger: --sweep-above for an amount, "
            "--sweep-every for a period, or both. Without one nothing would "
            "ever sweep, which is not what naming a destination means."
        )
    for name, value in (("--sweep-above", args.sweep_above), ("--sweep-every", args.sweep_every)):
        if value is not None and value <= 0:
            raise UnrecoverableError(f"{name} must be positive, not {value}.")


def _maybe_sweep(algorand, address: str, args, *, spendable: int, backoff) -> None:
    """Forward the surplus if a trigger says to.

    Deliberately on the heartbeat rather than after each execution: a sweep is
    a transaction, and one per execution would spend a meaningful part of what
    it is forwarding on its own fees.

    The period is measured in **wall time, persisted**, and both halves of
    that were bugs. It first passed `seconds_since_last=None` until a sweep
    had happened, and `decide` skips the period branch when it cannot measure
    an interval, so `--sweep-every` on its own could never fire at all. The
    fix for that used `time.monotonic` from process start, which is no clock
    for this: launchd restarts the keeper on every crash and every login, and
    monotonic time does not advance while a laptop sleeps, so on exactly the
    machine `docs/hosting.md` recommends it, "every 86400s" meant "every
    86400 seconds of awake, uninterrupted uptime" and a laptop that never
    stayed up a full day never swept. So it comes off the state file the bot
    already keeps, and survives both.

    Never raises into the loop. A keeper that stopped executing because a
    sweep failed would have traded the thing that earns for the thing that
    tidies up, so a failure here is logged and the next heartbeat tries again.
    """
    from scripts import keeper_sweep

    reserve = keeper_sweep.reserve_for(args.sweep_reserve, args.min_balance)
    now = time.time()
    if backoff.last_sweep is None:
        # Nothing has ever swept, so start the clock rather than leaving the
        # period unmeasurable. The threshold is still evaluated below on this
        # same heartbeat, so seeding costs the other trigger nothing.
        backoff.record_sweep(now)
    decision = keeper_sweep.decide(
        spendable,
        reserve=reserve,
        threshold=args.sweep_above,
        # A clock that has gone backwards -- an NTP correction, a restored
        # machine -- must read as "no time has passed", never as a negative
        # interval that silently disables the period.
        seconds_since_last=max(0.0, now - backoff.last_sweep),
        every_seconds=args.sweep_every,
    )
    if not decision:
        logger.debug("No sweep: %s", decision.reason)
        return
    try:
        keeper_sweep.send(
            algorand, address, args.sweep_to, decision.amount, dry_run=args.sweep_dry_run
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        emit(
            "sweep_failed",
            f"Sweep of {decision.amount} µALGO to {args.sweep_to} failed, "
            f"continuing to execute: {failure_text(exc)}",
            level=logging.WARNING,
            amount=decision.amount,
            destination=args.sweep_to,
        )
        return
    backoff.record_sweep(now)


def guard_balance(algod, address: str, warn_below: int, state: tuple[int, int] | None = None) -> int:
    """Refuse to run below what it takes to broadcast; warn while it is low.

    A keeper earns its fees into the same account it spends from, so it is
    normally self-sustaining, right until it is empty, at which point it
    cannot earn its way back out. That is the failure this catches.

    Returns what the account can actually spend, which is the number every
    decision here is about: an account can hold several ALGO and be unable to
    pay a 1,000 µALGO fee, because minimum balance is not a balance.

    `state` is `(balance, floor)` already read, for the caller that has just
    read the same `account_info` for something else. Passing it makes this
    check free rather than a second request for the same response.
    """
    balance, floor = state if state is not None else account_floor(algod, address)
    spendable = balance - floor
    if spendable < EXECUTION_COST_MICROALGO:
        raise UnrecoverableError(
            f"Keeper {address} holds {balance} µALGO, of which {floor} is minimum "
            f"balance it cannot spend. That leaves {spendable} µALGO, below the "
            f"{EXECUTION_COST_MICROALGO} µALGO one execution costs. Every asset "
            f"opt-in, and every app or asset this account created, raises that "
            f"floor by 100,000 µALGO. Fund it before starting."
        )
    if spendable < warn_below:
        runs = spendable // EXECUTION_COST_MICROALGO
        emit(
            "low_balance",
            f"Keeper has {spendable} µALGO spendable of {balance} held: about "
            f"{runs} execution(s) of headroom. Collected fees top it up, but a "
            f"quiet registry will not.",
            level=logging.WARNING,
            balance=balance,
            min_balance=floor,
            spendable=spendable,
            executions_remaining=runs,
            warn_below=warn_below,
        )
    return spendable


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
        "--keeper-address",
        default=os.environ.get("KEEPER_ADDRESS"),
        help=(
            "with --check, also report the ASA bonuses this account is not opted "
            "in to earn. Reads public state only, so it can name a keeper you do "
            "not run (default: KEEPER_ADDRESS)"
        ),
    )
    parser.add_argument(
        "--min-balance",
        type=int,
        default=int(os.environ.get("KEEPER_MIN_BALANCE", LOW_BALANCE_MICROALGO)),
        help=(
            "warn below this many *spendable* µALGO, which is what the account "
            "holds less its minimum balance (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--sweep-to",
        default=os.environ.get("KEEPER_SWEEP_TO"),
        help="forward surplus earnings to this address; nothing sweeps without it",
    )
    parser.add_argument(
        "--sweep-above",
        type=int,
        default=_env_int("KEEPER_SWEEP_ABOVE"),
        help="sweep once the surplus reaches this many µALGO",
    )
    parser.add_argument(
        "--sweep-every",
        type=int,
        default=_env_int("KEEPER_SWEEP_EVERY"),
        help="sweep this many seconds after the last one, if there is a surplus",
    )
    parser.add_argument(
        "--sweep-reserve",
        type=int,
        default=_env_int("KEEPER_SWEEP_RESERVE"),
        help=(
            "µALGO to keep behind. Floored at --min-balance whatever is asked, "
            "because a keeper swept below that stops earning and cannot refill"
        ),
    )
    parser.add_argument(
        "--sweep-dry-run",
        action="store_true",
        help="log what a sweep would send, and send nothing",
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
        # require one. --keeper-address is an address, not a key: it adds the
        # forgone-bonus report without making the probe hold anything.
        raise SystemExit(check_registry(algod, app_id, args.keeper_address))

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

    if args.sweep_to:
        _validate_sweep(args, keeper.address)
    balance, floor, opted_in_assets = account_state(algod, keeper.address)
    spendable = guard_balance(algod, keeper.address, args.min_balance, state=(balance, floor))
    registry = Registry()
    seconds_per_round = net.seconds_per_round(args.network)
    status = algod.status()
    # Said once at startup rather than every scan. An operator who has decided
    # to decline these does not need telling every round, and one who has not
    # decided at all needs telling before the first execution, not after a
    # month of them.
    try:
        report_forgone_bonuses(
            # Through the cache, not a bare `scan_upkeeps`: this is a full read
            # of every box, and the loop is about to want exactly those bytes.
            # Reading them twice a few milliseconds apart was 33 requests spent
            # on nothing.
            registry.refresh(algod, app_id, int(status["last-round"]), backoff),
            int(status["last-round"]),
            keeper.address,
            algod,
        )
    except Exception as cause:
        # Advisory. A registry it cannot read is the scan loop's problem to
        # report, and it reports it properly; refusing to start over a warning
        # would be worse than starting without one.
        emit(
            "bonus_check_failed",
            f"Could not check which bonus assets this keeper is opted in to: {cause}",
            level=logging.WARNING,
            reason=str(cause),
        )
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
        spendable=spendable,
        backoff_state=str(state_file) if state_file else "memory",
    )

    if args.align > 0:
        align_to(args.align, shutdown)

    error_delay = ERROR_RETRY_SECONDS
    executed_count = 0
    scans = 0
    last_heartbeat_round = 0
    # None rather than "now": the duration trigger measures from the last
    # sweep, and on a fresh start there has not been one. Seeding it with the
    # clock would make the first sweep wait a whole period for no reason.
    while True:
        if shutdown.requested:
            emit("stopped", "Shutting down cleanly")
            return
        try:
            if status is None:  # a node blip; re-read the clock before deciding
                status = algod.status()
            current = int(status["last-round"])
            upkeeps = registry.refresh(algod, app_id, current, backoff)
            due, held = partition_due(
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
                box_reads=registry.box_reads,
            )
            for upkeep in held:
                # The meter §3 asked for. A due, funded upkeep that this keeper
                # is deliberately not touching is invisible in every other line
                # it prints, and for an upkeep with escalation off it is
                # invisible on chain too: no fee rises, no report notices, the
                # window simply passes. Warned rather than logged once the
                # blackout has outlasted one of the upkeep's own intervals,
                # because by then it is a missed cycle rather than a pause.
                entry = backoff.entry(upkeep.upkeep_id)
                since = entry.since_round if entry and entry.since_round else current
                unserviced = current - since
                emit(
                    "blackout",
                    f"Not touching due upkeep {upkeep.upkeep_id} (target app "
                    f"{upkeep.target_app}) until round "
                    f"{entry.next_attempt_round if entry else current}: "
                    f"{entry.failures if entry else 0} failure(s) over {unserviced} "
                    f"rounds, {effective_fee(upkeep, current)} µALGO unclaimed"
                    + (f", last at {entry.site}" if entry and entry.site else ""),
                    level=(
                        logging.WARNING
                        if unserviced > max(upkeep.interval_rounds, 1)
                        else logging.DEBUG
                    ),
                    round=current,
                    upkeep_id=upkeep.upkeep_id,
                    target_app=upkeep.target_app,
                    unserviced_rounds=unserviced,
                    failures=entry.failures if entry else 0,
                    next_attempt_round=entry.next_attempt_round if entry else current,
                    fee_forgone=effective_fee(upkeep, current),
                    escalating=upkeep.fee_cap > upkeep.fee_per_execution,
                    site=entry.site if entry else "",
                )
            for upkeep in due:
                if shutdown.requested:
                    break
                balance_before = None
                broadcast = False
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
                    # This simulates, and algokit-utils raises on a failed
                    # group (`TransactionComposer._handle_simulate_error`), so
                    # a target that refuses is refused *here* and nothing is
                    # ever broadcast. Two things follow, and both are why the
                    # ordering below changed:
                    #   * an attempt that a target refuses costs one request
                    #     and puts nothing in the transaction pool, which is
                    #     what makes the short retry in `keeper_backoff`
                    #     affordable, and
                    #   * a balance read either side of it would be measuring
                    #     a transaction that never happened.
                    params = _resolve_execute_references(client, upkeep, extra_fee)
                    # Read before reaching, so that if this call loses a race
                    # the keeper's own log can say what losing cost it. That
                    # number is the whole argument for running a keeper at all
                    # (docs/arcron.md), and until now nothing but a controlled
                    # experiment had ever measured it.
                    balance_before = _balance(algod, keeper.address)
                    broadcast = True
                    with muffled(as_json):
                        response = client.send.execute(
                            args=ExecuteArgs(upkeep_id=upkeep.upkeep_id),
                            params=params,
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
                    # `execute` returns the round it rescheduled to, so the
                    # cache learns the one field that decides when this box
                    # next matters without reading it back.
                    registry.remember_execution(
                        upkeep.upkeep_id,
                        int(response.abi_return) if isinstance(response.abi_return, int) else 0,
                        current,
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
                    if not broadcast and is_target_refusal(reason):
                        # The simulate is what refused, and it refused *inside
                        # the target*. To get that far the call passed
                        # `execute`'s own asserts against the very latest
                        # state — the upkeep exists, `Global.round >= due`, the
                        # escrow covers the fee — so the registry demonstrably
                        # had not moved on, and asking it again would be a
                        # request spent to be told what the failure already
                        # says. This is the common failure once a target starts
                        # refusing, and it is now the cheapest one.
                        moved, after = False, None
                    else:
                        # Ask the registry what happened before deciding what
                        # this failure was. The box is the contract's own
                        # record, so it settles the question the error string
                        # can only hint at.
                        moved, after = registry_moved_on(algod, app_id, upkeep)
                        if after is not None:
                            # Already paid for; the cache would only read it
                            # again on the next scan.
                            registry.remember(after, current)
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
                        #
                        # Unless nothing was broadcast at all, which is what
                        # happens when the simulate saw the winner's execution
                        # first. Then the cost is zero by construction, and
                        # reading the balance twice to arrive at zero is two
                        # requests spent on arithmetic.
                        if not broadcast:
                            spent = 0
                        else:
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
                            broadcast=broadcast,
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
                            # False means the simulate refused and no
                            # transaction was ever sent, which is the claim
                            # the short retry schedule rests on. It is worth
                            # having in the log rather than in a comment.
                            broadcast=broadcast,
                            target_refusal=entry.target_refusal,
                            site=entry.site,
                            registry_advanced=moved,
                            reason=reason[:400],
                        )
            # Scans are no longer one a round, so counting them is no longer
            # counting time. Whichever clock runs out first wins: the balance
            # guard is the number that kills keepers silently, and on a quiet
            # registry twenty scans could otherwise be most of a day.
            due_a_heartbeat = (
                scans % HEARTBEAT_SCANS == 0
                or current - last_heartbeat_round >= HEARTBEAT_ROUNDS
                or args.once
            )
            if due_a_heartbeat:
                last_heartbeat_round = current
                # Proof of life, and the number that kills bots silently. One
                # `account_info` answers both this and which bonus assets the
                # keeper can receive; the loop used to spend a second request a
                # scan on the latter. Refreshing the asset set here rather than
                # every scan means an operator who opts in mid-run starts
                # earning the bonus within a heartbeat instead of within a
                # round, which is the whole of what it costs.
                balance, floor, opted_in_assets = account_state(algod, keeper.address)
                spendable = guard_balance(
                    algod, keeper.address, args.min_balance, state=(balance, floor)
                )
                if args.sweep_to:
                    _maybe_sweep(
                        algorand,
                        keeper.address,
                        args,
                        spendable=spendable,
                        backoff=backoff,
                    )
                emit(
                    "heartbeat",
                    f"Heartbeat: round {current}, {len(upkeeps)} upkeeps, "
                    f"{len(due)} due, {executed_count} executed this session, "
                    f"{spendable} µALGO spendable, {registry.box_reads} box read(s) "
                    f"over {scans} scan(s)",
                    round=current,
                    upkeeps=len(upkeeps),
                    due=len(due),
                    executed_session=executed_count,
                    backed_off=len(backoff.blocked_ids(current)),
                    spendable=spendable,
                    scans=scans,
                    box_reads=registry.box_reads,
                )
            if args.once:
                return
            # Wake when the registry could next need something, not every
            # round. `next_wake_round` is the soonest round at which any cached
            # box stops being good enough to decide on, which is the same
            # number the cache re-reads on, so the loop cannot sleep past a
            # promise the cache made.
            status = wait_for_work(
                algod,
                current,
                registry.next_wake_round(current, backoff),
                seconds_per_round,
                shutdown,
            )
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
            # Whatever failed may have been the clock itself, and a stale round
            # would have the next scan deciding against a chain that has moved.
            status = None
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
