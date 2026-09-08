"""A read-only watcher that says what the keeper network is doing.

A network whose work is invisible looks dead even when it is running fine.
This watches the registry and announces what changed. It reports executions
and who earned them, upkeeps registered and cancelled, and the failures:
upkeeps gone dormant for lack of funds, or overdue by an unusual margin.
Surfacing those builds more trust than hiding them.

**It holds no keys and cannot sign anything.** That boundary is deliberate and
enforced by a test: a notifier that could sign would be a liability with no
upside. It reads algod and posts to a webhook, and that is all it can do.

No indexer is needed. Box state gives everything except *which* keeper earned
an execution, and for that the watcher already knows the exact round to look
at, so it fetches that one block.

**What it can and cannot see.** It compares snapshots of the box list, so it
sees every state that lasts at least one scan interval and nothing else. A
register/cancel pair that both land entirely between two scans is not seen,
and no amount of priority-sorting a finished scan makes the first stranger
alert arrive before the rest of the scan has been read: the snapshot is built
first, then diffed. That coverage boundary is accepted in
`docs/design/mainnet-rollout.md` ("What quiet protects, and what it does
not") until registration history is consumed instead.

**Two kinds of announcement.** Ordinary events (executed, registered,
cancelled, dormant, revived, stalled) are best-effort: a post that fails after
its retries is logged and dropped, because the next scan will say something
newer. A *stranger* is different: it is the one event the unfrozen MainNet
window exists to catch, so it is written to a pending file before the
snapshot advances, re-posted at the top of every loop before the node is
asked anything (a node outage must not hold back an alert Discord can take),
and forgotten only when Discord has answered 2xx. At-least-once, so a crash
between the answer and the acknowledgement yields a duplicate rather than a
silence. A node the bot would refuse to work with (`UnrecoverableError`) stops
this process too, non-zero, after one more delivery attempt.

Run:  poetry run python -m scripts.notifier [--once] [--network N] [--app-id N]
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from algosdk import encoding

from scripts import network as net
# One decoder, not a third copy: this is the same one the bot uses.
from scripts.keeper_bot import (
    UnrecoverableError,
    Upkeep,
    effective_fee,
    require_keeper_app,
    resolve_app_id,
    scan_upkeeps,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# An upkeep this far past due, in multiples of its own interval, is worth
# saying out loud.
STALL_INTERVALS = 3
# Discord rejects anything longer; batches are split rather than truncated.
MAX_MESSAGE_CHARS = 1_900
# Between posts, to stay well inside Discord's rate limits.
POST_INTERVAL_SECONDS = 1.0
# How far back to look for the transaction behind an execution. Bounded so a
# notifier restarted after a long gap does not crawl the chain. Each block is
# fetched whole, and a MainNet block is megabytes where a TestNet one is
# kilobytes; the public edge meters by bytes, so a scan with executions can
# spend more quota on these 24 fetches than on the box reads. That is why the
# runbook points the MainNet notifier at our own node (ALGOD_SERVER in
# `deploy/notifier.env.example`), not at the public endpoint.
MAX_ATTRIBUTION_BLOCKS = 24
# Between scans. This was 5 seconds, which on a registry whose shortest live
# cadence is an hour is 17,000 scans a day of a few dozen requests each, and
# the public endpoint's daily quota is what a keeper on the same address was
# already being refused over (scripts/node_retry.py, "Whose quota this is").
# Thirty seconds is about eleven rounds: an execution is announced within a
# scan of landing, and a stranger within a scan of registering, at a fortieth
# of the requests. `--poll-seconds` for anything else.
DEFAULT_POLL_SECONDS = 30
# Discord's `Retry-After` is honoured up to this. It is a header a hostile or
# misconfigured proxy could set to anything, and a notifier asleep for a day
# on somebody else's say-so is the watcher not watching.
MAX_RETRY_AFTER_SECONDS = 30
# How many times one `post` tries before giving the message back to its
# caller as undelivered: three attempts, so two retries. Enough to ride out a
# blip and a single 429 without holding the scan loop for long: the worst
# case is two ceilings of sleep, about a minute, and everything longer than
# that is an outage the pending-stranger file exists for rather than
# something a retry loop should sit through.
POST_ATTEMPTS = 3
# The wait after a 5xx or a network error, doubled per attempt and never
# above MAX_RETRY_AFTER_SECONDS. A 429 uses what Discord asked for instead.
POST_BACKOFF_SECONDS = 2.0
# The least time between two re-posts of one undelivered stranger alert.
# Checked at loop boundaries, so the real gap is this rounded up to the next
# scan (at least five minutes, not exactly five). Every scan would be thirty
# seconds, which against a Discord outage is a hundred wasted attempts an
# hour and, against a rate limit, the cause of the next one. Five minutes
# keeps the alert pressing without becoming the flood; on a webhook that has
# come back it lands within one window of the recovery. The first attempt for
# a fresh sighting is immediate; this only paces the retries, and the last
# attempt is written into the record so a crash-looping unit cannot dodge it
# by restarting.
STRANGER_RETRY_SECONDS = 300
# The ARC-4 selector of `execute(uint64)uint64`, so attribution only credits
# an execution to the account that actually executed. Every other call to the
# app (a `register`, a `cancel`, a `top_up`) is an application call too, and
# reading the first one in a block as the execution named the wrong keeper.
EXECUTE_SELECTOR = hashlib.new("sha512_256", b"execute(uint64)uint64").digest()[:4]


@dataclass
class Event:
    """Something worth telling the channel about."""

    kind: str
    upkeep_id: int
    text: str
    # For an `executed` event: how many runs this one announcement covers, and
    # what they are estimated to have cost. The summary used to count every
    # event as one run at the base fee, which undercounted a burst and ignored
    # escalation; `diff` already knew both numbers and threw them away.
    runs: int = 0
    paid: int = 0


@dataclass
class Snapshot:
    """What the registry looked like, in the only terms that matter here."""

    upkeeps: dict[int, dict] = field(default_factory=dict)
    dormant: set[int] = field(default_factory=set)
    stalled: set[int] = field(default_factory=set)
    last_round: int = 0

    @classmethod
    def of(cls, upkeeps: list[Upkeep], current_round: int) -> "Snapshot":
        return cls(
            upkeeps={
                upkeep.upkeep_id: {
                    "times_executed": upkeep.times_executed,
                    "balance": upkeep.balance,
                    "fee_per_execution": upkeep.fee_per_execution,
                    "interval_rounds": upkeep.interval_rounds,
                    "next_execution_round": upkeep.next_execution_round,
                    "target_app": upkeep.target_app,
                    "policy": upkeep.policy,
                    "fee_cap": upkeep.fee_cap,
                    "fee_asset": upkeep.fee_asset,
                    "last_serviced_round": upkeep.last_serviced_round,
                    "creator": upkeep.creator,
                }
                for upkeep in upkeeps
            },
            # Escalation raises the bar an upkeep has to clear to be
            # executable, so "run dry" is measured against what it would pay
            # now, not against the fee its creator wrote down.
            dormant={
                u.upkeep_id
                for u in upkeeps
                if u.balance < effective_fee(u, current_round)
            },
            stalled={
                u.upkeep_id
                for u in upkeeps
                if u.balance >= effective_fee(u, current_round)
                and current_round - u.next_execution_round
                > STALL_INTERVALS * max(u.interval_rounds, 1)
            },
            last_round=current_round,
        )

    def to_json(self) -> dict:
        return {
            "upkeeps": {str(k): v for k, v in self.upkeeps.items()},
            "dormant": sorted(self.dormant),
            "stalled": sorted(self.stalled),
            "last_round": self.last_round,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "Snapshot":
        return cls(
            upkeeps={int(k): v for k, v in payload.get("upkeeps", {}).items()},
            dormant=set(payload.get("dormant", [])),
            stalled=set(payload.get("stalled", [])),
            last_round=int(payload.get("last_round", 0)),
        )


def _algos(micro_algo: int) -> str:
    return f"{micro_algo / 1_000_000:.6f}".rstrip("0").rstrip(".") + " ALGO"


def diff(
    previous: Snapshot,
    current: Snapshot,
    known_creators: frozenset[str] = frozenset(),
) -> list[Event]:
    """What changed between two views of the registry.

    Pure, so the interesting cases can be tested without a chain. Conditions
    are edge-triggered: an upkeep that is dormant for a week is announced once,
    not on every scan.
    """
    events: list[Event] = []

    for upkeep_id, now in sorted(current.upkeeps.items()):
        before = previous.upkeeps.get(upkeep_id)
        if before is None:
            creator = now.get("creator", "")
            ours = not known_creators or creator in known_creators
            if not ours:
                # The one event that must never be suppressed. An unfrozen
                # MainNet deployment whose id is unpublished is supposed to
                # hold nobody's escrow but ours, and the moment it holds
                # somebody else's the operator owes a decision within 24
                # hours (`docs/design/mainnet-rollout.md`, "If a stranger
                # appears"). The decision is not made here and it is not an
                # automatic freeze: freezing is right only for bytecode
                # already accepted for permanence, and the alternatives are an
                # already-approved update sequence or an explicit, recorded
                # acceptance of the exposure. What this process owes is the
                # sighting, durably. Announced on a first run too, unlike an
                # ordinary registration: on an app that is supposed to be
                # empty, a stranger already present is exactly the thing being
                # watched for, and the flood this suppression avoids does not
                # exist.
                events.append(
                    Event(
                        "stranger",
                        upkeep_id,
                        f"🚨 **Upkeep {upkeep_id} was registered by {creator}, who is "
                        f"not one of us.** Somebody has escrowed real value into a "
                        f"deployment whose id was never published, and while it is "
                        f"unfrozen they are trusting a keyholder they did not agree to. "
                        f"This needs an **operator decision within 24 hours** of first "
                        f"sighting, recorded with who decided and on what evidence: "
                        f"freeze, only if the running bytecode is already accepted for "
                        f"permanence; or run an already-approved update sequence; or "
                        f"explicitly accept the temporary unfrozen exposure while "
                        f"responding. Not an automatic freeze, not an unsoaked update, "
                        f"and not cancel: `cancel` is creator-only, so their box cannot "
                        f"be removed by us. Targets app {now['target_app']}, every "
                        f"{now['interval_rounds']} rounds.",
                    )
                )
            elif previous.upkeeps:  # a first run is not a flood of "new upkeep"
                events.append(
                    Event(
                        "registered",
                        upkeep_id,
                        f"**Upkeep {upkeep_id} registered** targeting app "
                        f"{now['target_app']}, every {now['interval_rounds']} rounds, "
                        f"paying {_algos(now['fee_per_execution'])} per run",
                    )
                )
            continue

        runs = now["times_executed"] - before["times_executed"]
        if runs > 0:
            paid = _burst_cost(before, now, runs)
            events.append(
                Event(
                    "executed",
                    upkeep_id,
                    f"**Upkeep {upkeep_id} executed**"
                    + (f" ×{runs}" if runs > 1 else "")
                    # An estimate, marked as one here as well as in the
                    # summary: `_burst_cost` reads the escrow drawdown, which a
                    # top-up in the same window can hide part of.
                    + f", ≈ {_algos(paid)} paid (escrow drawdown), "
                    f"next due at round {now['next_execution_round']}",
                    runs=runs,
                    paid=paid,
                )
            )

    for upkeep_id in sorted(set(previous.upkeeps) - set(current.upkeeps)):
        events.append(
            Event("cancelled", upkeep_id, f"Upkeep {upkeep_id} cancelled; escrow returned")
        )

    for upkeep_id in sorted(current.dormant - previous.dormant):
        if upkeep_id in current.upkeeps:
            state = current.upkeeps[upkeep_id]
            events.append(
                Event(
                    "dormant",
                    upkeep_id,
                    f"⚠️ **Upkeep {upkeep_id} has run dry**: escrow "
                    f"{_algos(state['balance'])} is below its "
                    f"{_algos(_fee_now(state, current.last_round))} fee, so no keeper "
                    f"can run it. Anyone can top it up.",
                )
            )
    for upkeep_id in sorted(previous.dormant - current.dormant):
        if upkeep_id in current.upkeeps:
            events.append(Event("revived", upkeep_id, f"Upkeep {upkeep_id} funded again"))

    for upkeep_id in sorted(current.stalled - previous.stalled):
        state = current.upkeeps[upkeep_id]
        overdue = current.last_round - state["next_execution_round"]
        events.append(
            Event(
                "stalled",
                upkeep_id,
                f"⚠️ **Upkeep {upkeep_id} is going unserviced**. Funded and due, "
                f"but {overdue} rounds late. Nobody is keeping it.",
            )
        )

    return events


def _fee_now(state: dict, current_round: int) -> int:
    """What one execution of this upkeep would pay at `current_round`.

    The twin of the escalation arithmetic in the contract and in
    `scripts/keeper_bot.py::effective_fee`, over a snapshot's plain dict.
    """
    base, cap = state["fee_per_execution"], state.get("fee_cap", 0)
    # A snapshot written before escalation existed has neither key. Read that
    # as "no escalation" rather than defaulting the service round to zero,
    # which would make every upkeep in an old state file look maximally late
    # and report the ceiling as the price of everything.
    if (
        cap <= base
        or "last_serviced_round" not in state
        or state["next_execution_round"] <= state["last_serviced_round"]
    ):
        return base
    interval = max(state["interval_rounds"], 1)
    lateness = max(current_round - state["last_serviced_round"], 0)
    excess = min(max(lateness - interval, 0), interval)
    fee = base + (cap - base) * excess // interval
    # An upkeep never bids more than it holds; see the contract's `execute`.
    return base if state["balance"] < fee else fee


def _burst_cost(before: dict, now: dict, runs: int) -> int:
    """What a run of `runs` executions is estimated to have taken out of escrow.

    The balance delta is sitting in the two snapshots and beats any model of
    the fee curve, which would be wrong for a burst whose runs were not all
    priced the same. It is still an estimate: a positive drop is read as the
    whole cost, and a top-up that landed in the same window hides part of it
    (a top-up larger than the payments makes the delta meaningless, and only
    then does this fall back to the curve). The summary says so. The exact
    figure is the inner payment `execute` sends, which `keeper-preview` reads
    from the indexer and this watcher does not.
    """
    drawdown = before["balance"] - now["balance"]
    if drawdown > 0:
        return drawdown
    base = now["fee_per_execution"]
    return _fee_now(now, now.get("last_serviced_round", 0)) + (runs - 1) * base


def _as_address(sender: object) -> str | None:
    """A block's sender, however this algod chose to represent it.

    algosdk hands back an already-decoded address string; other paths give raw
    public key bytes. Neither is worth crashing a notifier over, so both are
    accepted and anything else is simply not attributed.
    """
    if isinstance(sender, str) and len(sender) == 58:
        return sender
    if isinstance(sender, (bytes, bytearray)) and len(sender) == 32:
        return encoding.encode_address(bytes(sender))
    return None


def _app_args(inner: dict) -> list[bytes]:
    """An application call's arguments as bytes, however this algod spelt them.

    algosdk hands back raw bytes; the REST shape is base64 strings. Anything
    that is neither ends the list, because a call this cannot read is not one
    it should attribute anything to.
    """
    out: list[bytes] = []
    for arg in inner.get("apaa") or []:
        if isinstance(arg, str):
            try:
                arg = base64.b64decode(arg)
            except Exception:
                break
        if not isinstance(arg, (bytes, bytearray)):
            break
        out.append(bytes(arg))
    return out


def _executed_upkeep(inner: dict) -> int | None:
    """The upkeep id an `execute` call names, or None for any other call.

    The selector alone was checked before, so the first `execute` found in the
    window was credited with every execution in it. Two upkeeps run by two
    keepers in one window were both attributed to whichever keeper's block
    was read first. The id is the second argument, an 8-byte big-endian
    uint64, which is what `execute(uint64)uint64` says it is.
    """
    args = _app_args(inner)
    if len(args) < 2 or args[0][:4] != EXECUTE_SELECTOR or len(args[1]) != 8:
        return None
    return int.from_bytes(args[1], "big")


def executors(algod, app_id: int, since_round: int, until_round: int) -> dict[int, str]:
    """Who executed which upkeep in the window between two scans.

    Box state records that an upkeep ran, never who ran it. An indexer would
    answer this, but so does algod: the execution happened between the last
    scan and this one, which in normal operation is a couple of blocks. Read
    once per scan and looked up per event, rather than once per event: a
    burst of N executions used to cost N passes over the same blocks, against
    the same public quota the keeper is already refused over.

    Deliberately not derived from the upkeep's schedule. An upkeep catching up
    after an outage runs in a round far ahead of the one it was *scheduled*
    for, and using the schedule would attribute it to the wrong block, or to
    a block that has since been pruned.

    Newest block first, so an upkeep executed twice in the window is credited
    to its latest keeper. A block that cannot be fetched ends the pass with
    what was gathered so far; attribution is a nicety and the announcement
    goes out either way, saying it does not know.
    """
    found: dict[int, str] = {}
    newest = max(until_round, 0)
    oldest = max(since_round + 1, newest - MAX_ATTRIBUTION_BLOCKS + 1, 1)
    for round_number in range(newest, oldest - 1, -1):
        try:
            block = algod.block_info(round_number)
        except Exception:  # pruned or unavailable
            break
        for txn in block.get("block", {}).get("txns") or []:
            inner = txn.get("txn", {})
            if inner.get("type") != "appl" or inner.get("apid") != app_id:
                continue
            upkeep_id = _executed_upkeep(inner)
            if upkeep_id is None or upkeep_id in found:
                continue
            sender = _as_address(inner.get("snd"))
            if sender:
                found[upkeep_id] = sender
    return found


def attribute(
    algod, app_id: int, since_round: int, until_round: int, upkeep_id: int
) -> str | None:
    """Which account executed one upkeep in the window; `executors` for many."""
    return executors(algod, app_id, since_round, until_round).get(upkeep_id)


def _attribution_line(keeper: str | None) -> str:
    """The keeper line under an execution, honest about not knowing.

    Silently omitting the line when no block named a keeper made an
    unattributed execution read like a formatting choice. The summary's
    numbers are already estimates; the per-event text should not add a quiet
    gap of its own.
    """
    if keeper:
        return f"\n↳ keeper `{keeper[:8]}…{keeper[-6:]}`"
    return (
        f"\n↳ keeper: attribution unknown (no `execute` call for this upkeep found in "
        f"the last {MAX_ATTRIBUTION_BLOCKS} blocks, or a block is no longer available)"
    )


def summarise(snapshot: Snapshot, executions: int, paid: int) -> str:
    """The periodic summary, which doubles as the watcher's pulse.

    Two-hourly by default (240 scans of 30 seconds). Under the MainNet runbook
    it is the liveness signal: its absence for a day is read as a dead watcher
    and acted on within the same 24-hour budget as a stranger alert. The
    payment figure is labelled an estimate because it is one: `_burst_cost`
    reads escrow drawdown, and a top-up in the same window hides part of what
    was paid. The exact figure comes from the indexer via `keeper-preview`;
    neither number is gate evidence.
    """
    dormant = len(snapshot.dormant)
    return (
        f"📊 **Registry**: {len(snapshot.upkeeps)} upkeeps, {executions} executions "
        f"since the last summary, ≈ {_algos(paid)} paid to keepers (estimated from "
        f"escrow drawdown)"
        + (f", {dormant} out of funds" if dormant else "")
    )


def _retry_after(exc: urllib.error.HTTPError) -> float:
    try:
        asked = float(exc.headers.get("Retry-After", "2"))
    except (TypeError, ValueError):
        asked = 2.0
    return min(max(asked, 0.0), MAX_RETRY_AFTER_SECONDS)


def valid_webhook(url: str) -> bool:
    """Whether a webhook URL is one `urllib` can be asked to post to.

    `urllib.request.Request` raises ValueError on a URL with no scheme, and
    it did so from inside `post`, outside the `try`, on every call: a
    DISCORD_WEBHOOK_URL pasted without its `https://` aborted every scan at
    the first stranger re-post, before the snapshot ever advanced. Refused at
    startup instead, where the fix is one line of an env file.
    """
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def post(webhook: str | None, message: str) -> bool:
    """Send to Discord, or to the terminal when no webhook is configured.

    True only when the message was accepted (a 2xx, or printed because there
    is nowhere else for it to go). Never raises: the loop that calls this has
    a scan to finish, so the request is built inside the `try` too, not only
    sent there. Retries are bounded, because a stranger alert that could not
    be delivered has a file to wait in (`PendingStrangers`) and an ordinary
    announcement is not worth stalling the watcher over.

    A 429 used to sleep what Discord asked for and then *not retry*, so the
    one message Discord had just said it would accept in a moment was dropped
    anyway; and every other failure was swallowed with a warning the caller
    could not see. Both are why this returns something now.
    """
    if not webhook:
        logger.info(message)
        return True
    body = json.dumps({"content": message[:MAX_MESSAGE_CHARS]}).encode()
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                webhook, data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(request, timeout=15).close()
            return True
        except ValueError as exc:
            # A URL urllib cannot even form a request for. Startup refuses
            # these, so this is belt and braces, and asking again cannot help.
            logger.error(f"Webhook URL is unusable ({exc}); not retrying")
            return False
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = _retry_after(exc)
                logger.warning(f"Rate limited; retrying in {wait}s")
            elif exc.code >= 500:
                wait = min(POST_BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_RETRY_AFTER_SECONDS)
                logger.warning(f"Discord answered {exc.code}; retrying in {wait}s")
            else:
                # A 4xx other than 429 is our mistake (a deleted webhook, a
                # malformed body) and will not change by asking again.
                logger.warning(f"Discord rejected the post ({exc.code}); not retrying")
                return False
        except Exception as exc:  # timeouts, DNS, a reset connection
            wait = min(POST_BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_RETRY_AFTER_SECONDS)
            logger.warning(f"Could not post ({exc}); retrying in {wait}s")
        if attempt < POST_ATTEMPTS:
            time.sleep(wait)
    logger.warning(f"Gave up posting after {POST_ATTEMPTS} attempts")
    return False


def state_path(network: str, app_id: int) -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "arcron" / f"notifier-{network}-{app_id}.json"


def pending_path(snapshot_path: Path | None) -> Path | None:
    """Where undelivered stranger alerts wait, beside the snapshot.

    A separate file, not a field in the snapshot, because the two have
    different lifetimes: the snapshot is replaced every scan, and a stranger
    record must survive any number of them until Discord answers.
    `--no-state` leaves both in memory, which is fine for `--once` and for
    tests, is not how the VPS runs, and is refused on MainNet.
    """
    if snapshot_path is None:
        return None
    return snapshot_path.with_name(f"{snapshot_path.stem}-pending.json")


def load(path: Path | None) -> Snapshot:
    """Pick up where we left off, so a restart does not replay history."""
    if path is None or not path.exists():
        return Snapshot()
    try:
        return Snapshot.from_json(json.loads(path.read_text()))
    except Exception as exc:
        logger.warning(f"Ignoring unreadable state {path}: {exc}")
        return Snapshot()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)


def save(path: Path | None, snapshot: Snapshot) -> None:
    if path is None:
        return
    _write_json(path, snapshot.to_json())


def _log_write_failure(path: Path | None, what: str, exc: OSError) -> None:
    """The one line said when the disk will not have a state file.

    A permission mistake in the state directory is a real deployment failure
    this repository has already had once (`deploy/Dockerfile`, the root-owned
    volume). It must not become "no stranger ever delivered and no execution
    ever announced", which is what an unguarded pending-file save did, nor
    "no summary ever posted", which is what an unguarded snapshot save did:
    the save sat before the scan counter and the summary block, so every scan
    ended in the retry clause and the runbook's liveness signal never fired.
    Logged at error on every failure, because a state directory that stays
    unwritable is worth a line per scan in the journal.
    """
    logger.error(
        f"Could not write {path} ({what}): {exc}. Continuing from memory; a restart "
        f"will replay what was announced since the last successful write."
    )


def save_or_log(path: Path | None, snapshot: Snapshot) -> None:
    try:
        save(path, snapshot)
    except OSError as exc:
        _log_write_failure(path, "saving the snapshot", exc)


class PendingStrangers:
    """Stranger alerts that have not yet been acknowledged by the webhook.

    The failure this exists for was found in review (#250, F01): a stranger
    was announced once, best-effort, and a 5xx or a rate limit at that moment
    lost the only alert the unfrozen window depends on. Worse, a "delivered
    id" set that re-read the live boxes would drop an alert whose box was
    cancelled before the retry, which is exactly what a careful stranger
    might do. So the record carries its payload: the text is fixed at the
    sighting and nothing is re-derived from the chain.

    The file is a map keyed `network/app/upkeep`, one entry per sighting:

        {"mainnet/123/7": {"upkeep_id": 7,
                           "first_seen_round": 51234567,
                           "first_seen_at": "2026-09-08T10:15:00+00:00",
                           "last_attempt": 1789000000.0,
                           "text": "..."}}

    Written before the snapshot advances, so a crash between the two leaves
    the sighting on disk and not merely in a snapshot that now thinks the
    upkeep is old news. Removed only after `post` returns True; a crash
    between the 2xx and that removal replays the alert on the next start.
    Duplicates beat silence.

    `last_attempt` is wall-clock time and lives in the record rather than in
    memory, because a unit that crash-loops restarts more often than the
    retry window and would otherwise re-post every record, three attempts
    each, on every start. A restart waits out whatever is left of the window;
    a record never attempted is still posted at once.

    The disk is not allowed to stop delivery either way. A save that fails
    after a 2xx is logged and the in-memory deletion stands, because the
    alternative (reviewed, and reproduced) was a scan aborted by the save,
    a snapshot that never advanced, the same stranger re-posted every window
    and nothing ordinary announced at all. The cost of that choice is one
    duplicate after the next restart, which is the side this file errs on.
    """

    def __init__(self, path: Path | None, records: dict[str, dict] | None = None) -> None:
        self.path = path
        self.records: dict[str, dict] = records or {}

    @classmethod
    def load(cls, path: Path | None) -> "PendingStrangers":
        if path is None or not path.exists():
            return cls(path)
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict):
                raise ValueError(f"expected a JSON object, found {type(payload).__name__}")
        except Exception as exc:
            # Unlike the snapshot, this is not something to ignore quietly: a
            # pending file that cannot be read may hold the alert. An earlier
            # version said so and left the file in place, and the next `add`
            # then overwrote it, so the evidence was lost anyway. Moved aside
            # instead, under a name that says when, for a person to read.
            aside = path.with_name(
                f"{path.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            logger.error(
                f"Cannot read pending stranger alerts at {path} ({exc}); moved to {aside}. "
                f"Read it by hand: it may hold an undelivered stranger."
            )
            try:
                path.replace(aside)
            except OSError as move:
                logger.error(f"Could not move the unreadable file aside either: {move}")
            return cls(path)
        records = {key: cls._repaired(key, value) for key, value in payload.items()}
        return cls(path, records)

    @staticmethod
    def _repaired(key: str, value: object) -> dict:
        """A record with every field this class reads, filled in where missing.

        An entry without `text` used to be dropped on load and erased by the
        next save. A record in this file is a sighting somebody has not yet
        acted on, so a half-written one is kept and delivered as what it is: a
        stranger alert whose details were lost, naming the upkeep from the
        key. Logged, so the operator knows the text is a reconstruction.
        """
        record = dict(value) if isinstance(value, dict) else {}
        missing = [f for f in ("upkeep_id", "first_seen_round", "first_seen_at", "text") if f not in record]
        if missing:
            logger.warning(
                f"Pending stranger record {key} is missing {missing}; keeping it and "
                f"delivering what is known"
            )

        # Types as well as presence. `deliver` sorts on `first_seen_round` and
        # subtracts `last_attempt` from the clock, and a hand-edited file with
        # `"last_attempt": "yesterday"` (or a "5" beside a 5) raised TypeError
        # as the first statement of the scan loop, which the retry clause then
        # slept through forever: zero scans, logged as "retrying". Every field
        # is coerced to what the code reads, or replaced, and each repair is
        # said out loud so the operator knows the record was not as written.
        def coerce(name: str, kind, fallback, allow_none: bool = False):
            present = record.get(name, fallback)
            if present is None and allow_none:
                return None
            try:
                if kind is int and isinstance(present, float) and present != int(present):
                    raise ValueError(present)
                if kind is str and not isinstance(present, str):
                    # `str()` would happily render a list; a text that is not
                    # a string is not the stored text, whatever it prints as.
                    raise TypeError(present)
                return kind(present)
            except (TypeError, ValueError, OverflowError):
                logger.warning(
                    f"Pending stranger record {key} has {name}={present!r}, which is not "
                    f"{kind.__name__}; using {fallback!r}"
                )
                return fallback

        try:
            id_from_key = int(key.rsplit("/", 1)[-1])
        except ValueError:
            id_from_key = -1
        record["upkeep_id"] = coerce("upkeep_id", int, id_from_key)
        record["first_seen_round"] = coerce("first_seen_round", int, 0)
        record["first_seen_at"] = coerce("first_seen_at", str, "unknown")
        record["last_attempt"] = coerce("last_attempt", float, None, allow_none=True)
        text = coerce("text", str, "")
        record["text"] = text or (
            f"🚨 **Upkeep {record['upkeep_id']} was registered by somebody who is not one "
            f"of us** (pending record {key}; its stored text was lost, details were not). "
            f"This needs an operator decision within 24 hours of first sighting, which the "
            f"record put at round {record['first_seen_round']}, {record['first_seen_at']}."
        )
        return record

    def __len__(self) -> int:
        return len(self.records)

    def save(self) -> None:
        if self.path is None:
            return
        _write_json(self.path, self.records)

    def _save_or_log(self, what: str) -> None:
        try:
            self.save()
        except OSError as exc:
            _log_write_failure(self.path, what, exc)

    @staticmethod
    def key(network: str, app_id: int, upkeep_id: int) -> str:
        return f"{network}/{app_id}/{upkeep_id}"

    def add(self, network: str, app_id: int, event: Event, current_round: int) -> bool:
        """Record a sighting, durably, before anything else happens to it.

        False when the same sighting is already pending, which keeps its
        original first-seen time: the 24-hour budget runs from the first
        sighting, not from the most recent scan that noticed it again.
        """
        key = self.key(network, app_id, event.upkeep_id)
        if key in self.records:
            return False
        seen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records[key] = {
            "upkeep_id": event.upkeep_id,
            "first_seen_round": current_round,
            "first_seen_at": seen_at,
            "last_attempt": None,
            # The sighting time travels with the alert, so an operator reading
            # it after an outage knows when the budget actually started.
            "text": (
                f"{event.text}\nFirst sighted at round {current_round}, {seen_at}; "
                f"the 24-hour decision budget runs from then."
            ),
        }
        self._save_or_log("recording a sighting")
        return True

    def deliver(self, webhook: str | None, now: float | None = None) -> None:
        """Post every pending alert whose retry window has passed, oldest first.

        Called at the top of every loop, before the node is asked anything,
        so a node outage cannot hold back an alert Discord is ready to take;
        and again after a scan records new sightings, so a registry busy with
        executions cannot starve the one alert that matters. Paced by
        STRANGER_RETRY_SECONDS per record, on the wall clock, persisted; the
        first attempt is immediate.
        """
        now = time.time() if now is None else now
        for key in sorted(self.records, key=lambda k: (self.records[k]["first_seen_round"], k)):
            record = self.records[key]
            last = record.get("last_attempt")
            if last is not None and now - last < STRANGER_RETRY_SECONDS:
                continue
            record["last_attempt"] = now
            self._save_or_log("pacing a retry")
            if post(webhook, record["text"]):
                # Acknowledged only after the answer. The order of these two
                # lines is the at-least-once guarantee: a crash between them
                # re-posts on the next start rather than losing the alert.
                del self.records[key]
                self._save_or_log("acknowledging a delivery")
            else:
                logger.warning(
                    f"Stranger alert for upkeep {record['upkeep_id']} is still undelivered; "
                    f"kept in {self.path or 'memory'}, next attempt after {STRANGER_RETRY_SECONDS}s"
                )
            time.sleep(POST_INTERVAL_SECONDS)


def _is_unrecoverable(exc: BaseException) -> bool:
    """Whether `scripts.keeper_bot` has said this node cannot be worked with.

    `UnrecoverableError` is what `_box_page` raises when the node ignores
    `limit` and answers in legacy mode, and what `require_keeper_app` raises
    for a wrong id. The bot exits 2 on it; the notifier's retry clause used to
    swallow it with a warning and spin, which is a watcher that looks alive
    and watches nothing. Matched by name as well as by class because a test
    suite that reloads `scripts.keeper_bot` leaves this module holding the
    old class object, and an `isinstance` alone would then let it through.
    """
    return isinstance(exc, UnrecoverableError) or type(exc).__name__ == "UnrecoverableError"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one scan, then exit")
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None)
    parser.add_argument(
        "--summary-every",
        type=int,
        default=240,
        # 240 scans at the default 30 seconds is a two-hourly summary. On
        # MainNet that periodic summary is the liveness signal: the runbook
        # treats its absence for a day as a dead watcher, to be acted on
        # within the same 24-hour budget as a stranger alert. Its presence is
        # routine; its absence is the alarm, so do not turn it off there.
        help=(
            "scans between registry summaries (default: %(default)s, two-hourly at the "
            "default poll). The periodic summary is the watcher's liveness signal: under "
            "the MainNet runbook its absence for a day is a dead watcher, acted on within "
            "the 24-hour response budget. 0 disables it"
        ),
    )
    parser.add_argument(
        "--state-file", type=Path, default=None, help="where to remember what was announced"
    )
    parser.add_argument(
        "--ours",
        default=None,
        help=(
            "comma-separated 58-character addresses whose upkeeps are expected. Any other "
            "creator is announced as a stranger. NFD names are not resolved (corvid.algo is "
            "refused). Empty means announce nobody as a stranger, which is right on a "
            "shared TestNet app and wrong on a MainNet one whose id is supposed to be "
            "unpublished. Defaults to ARCRON_OURS in the environment, which is how the "
            "container and the systemd unit pass it."
        ),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="seconds between scans (default: %(default)s)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="on MainNet, mean it when there is no webhook and print announcements instead",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help=(
            "announce from scratch each run, and keep the pending stranger file in memory "
            "only, so an undelivered alert dies with the process. Refused on MainNet"
        ),
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id, args.network)
    algod = algorand.client.algod
    try:
        # A watcher pointed at an id that is not a keeper watches an empty box
        # list forever and reports a quiet registry. Refused at startup.
        require_keeper_app(algod, app_id, args.network)
    except RuntimeError as wrong:
        # `UnrecoverableError` is a RuntimeError; caught by the base class so a
        # test suite that reloads `scripts.keeper_bot` cannot leave this clause
        # holding a stale class object and let the refusal fall through.
        parser.error(str(wrong))
    webhook = os.environ.get("DISCORD_WEBHOOK_URL") or None
    if webhook is not None and not valid_webhook(webhook):
        # Refused here, not discovered on the first post: `post` would refuse
        # it too, but every scan would then end with the stranger it could not
        # deliver, and the fix is one line of an env file.
        parser.error(
            "DISCORD_WEBHOOK_URL is not an http(s) URL with a host (is the scheme "
            "missing?). Fix it, or unset it to print announcements instead"
        )
    path = None if args.no_state else (args.state_file or state_path(args.network, app_id))

    ours = args.ours if args.ours is not None else os.environ.get("ARCRON_OURS", "")
    known_creators = frozenset(a.strip() for a in ours.split(",") if a.strip())
    for entry in sorted(known_creators):
        # Syntax only, and it is worth knowing which way that fails. A
        # mistyped address, or `corvid.algo` written where the address was
        # meant, makes our own creator look like a stranger: loud, wrong, and
        # noticed on the first registration. The failure this cannot catch is
        # the quiet one, an allowlist that includes a real outsider, which
        # suppresses exactly the alert this exists for and looks like nothing
        # at all. No check on the string can tell those apart; the list is
        # short and should be read by a person.
        if not encoding.is_valid_address(entry):
            parser.error(
                f"--ours entry {entry!r} is not an Algorand address. NFD names are not "
                f"resolved here (corvid.algo is refused); use the 58-character address"
            )
    if args.network == net.MAINNET:
        # On MainNet a watcher that cannot tell a stranger, or that tells nobody,
        # is the failure it exists to prevent, so both are refused at startup
        # rather than logged past. A stranger on the unfrozen deployment starts
        # a 24-hour clock for an operator decision (freeze, approved update, or
        # recorded acceptance of the exposure), and that clock starts only if
        # this process notices and somebody reads it.
        if not known_creators:
            parser.error(
                "--ours (or ARCRON_OURS) is required on MainNet: without it no creator "
                "is a stranger, and a stranger is the one event this watcher exists for"
            )
        if not webhook and not args.stdout:
            parser.error(
                "DISCORD_WEBHOOK_URL is unset on MainNet, so announcements would go to a "
                "log nobody reads. Set it, or pass --stdout to mean that"
            )
        if args.no_state:
            # The durable stranger alert is the whole of F01, and it is durable
            # only because it is a file. In memory it dies with the process,
            # and systemd restarting the process is the ordinary case.
            parser.error(
                "--no-state is refused on MainNet: it keeps undelivered stranger alerts in "
                "memory only, which is no delivery guarantee at all. Use --state-file"
            )

    logger.info(
        f"Watching app {app_id} on {args.network} every {args.poll_seconds:g}s; "
        f"{'posting to Discord' if webhook else 'printing here (set DISCORD_WEBHOOK_URL to post)'}"
    )
    if known_creators:
        logger.info(f"  {len(known_creators)} creator(s) expected; any other is a stranger")
    else:
        logger.info(
            "  No --ours given, so no creator is treated as a stranger. On a deployment "
            "whose id is meant to be unpublished, pass it."
        )
    previous = load(path)
    pending = PendingStrangers.load(pending_path(path))
    if pending:
        logger.warning(
            f"  {len(pending)} stranger alert(s) left undelivered by a previous run; "
            f"posting them first"
        )
    executions_since_summary = 0
    paid_since_summary = 0
    scans = 0

    while True:
        try:
            # Anything still owed from earlier goes out before the node is
            # asked a single question. Delivery used to sit after the scan,
            # so a node outage or a 403 storm held back an alert that Discord
            # was ready to take; the two failures are independent and the
            # watcher should not couple them. Nor the other way round: a fault
            # in delivery (a record this code cannot read, a bug) must never
            # stop observation, so it is logged here and the scan goes ahead.
            try:
                pending.deliver(webhook)
            except Exception as exc:
                logger.error(f"Delivering pending stranger alerts failed ({exc!r}); scanning anyway")

            current_round = algod.status()["last-round"]
            snapshot = Snapshot.of(scan_upkeeps(algod, app_id), current_round)
            events = diff(previous, snapshot, known_creators)

            # New strangers to disk first, then to Discord first. The snapshot
            # is not advanced until the end of the scan, so a crash anywhere in
            # here re-diffs and re-records the same sighting rather than
            # forgetting it. Everything else waits behind them. Records
            # already attempted at the top of this loop are inside their
            # window and skipped here, so this second call posts only the
            # sightings this scan found.
            for event in events:
                if event.kind == "stranger":
                    pending.add(args.network, app_id, event, current_round)
            pending.deliver(webhook)

            # One pass over the window's blocks for the whole scan, however
            # many upkeeps ran in it; read lazily, so a quiet scan costs no
            # block fetches at all.
            keepers: dict[int, str] | None = None
            for event in events:
                if event.kind == "stranger":
                    continue
                text = event.text
                if event.kind == "executed":
                    if keepers is None:
                        keepers = executors(algod, app_id, previous.last_round, current_round)
                    text += _attribution_line(keepers.get(event.upkeep_id))
                    executions_since_summary += event.runs
                    paid_since_summary += event.paid
                if not post(webhook, text):
                    # Best-effort by design: the next scan has newer news, and
                    # the registry's own state is the record. Logged so an
                    # outage is visible in the journal rather than only as a
                    # gap in the channel.
                    logger.warning(
                        f"Dropped the '{event.kind}' announcement for upkeep {event.upkeep_id}"
                    )
                time.sleep(POST_INTERVAL_SECONDS)

            previous = snapshot
            save_or_log(path, snapshot)
            scans += 1
            if args.summary_every > 0 and scans % args.summary_every == 0:
                if not post(webhook, summarise(snapshot, executions_since_summary, paid_since_summary)):
                    logger.warning("Dropped the registry summary")
                executions_since_summary = 0
                paid_since_summary = 0

            if args.once:
                return
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            logger.info("Stopping")
            return
        except Exception as exc:
            if _is_unrecoverable(exc):
                # The node cannot be worked with (legacy box listing, wrong
                # app id). Retrying is spinning, and spinning looks like
                # watching. Stop loudly, as the bot does, so systemd's restart
                # limit turns it into the last line of the journal. But first,
                # once, whatever stranger alert is owed: the webhook may well
                # be fine even though the node is not. Pacing still applies,
                # and a record inside its window is picked up by the next
                # start, which reads `last_attempt` back from the file.
                logger.error(f"{exc}; the notifier cannot watch this node and is stopping")
                pending.deliver(webhook)
                raise SystemExit(2)
            if args.once:
                raise
            logger.warning(f"{exc}; retrying")
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
