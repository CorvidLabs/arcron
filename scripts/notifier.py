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
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from algosdk import encoding

from scripts import network as net
# One decoder, not a third copy: this is the same one the bot uses.
from scripts.keeper_bot import (
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
# notifier restarted after a long gap does not crawl the chain.
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
                # The one event that must never be suppressed. The plan for an
                # unfrozen MainNet deployment is to freeze the moment somebody
                # who is not us escrows into it, and that plan is worth nothing
                # if nothing notices. Announced on a first run too, unlike an
                # ordinary registration: on an app that is supposed to be empty,
                # a stranger already present is exactly the thing being watched
                # for, and the flood this suppression avoids does not exist.
                events.append(
                    Event(
                        "stranger",
                        upkeep_id,
                        f"**Upkeep {upkeep_id} was registered by {creator}, who is not "
                        f"one of us.** Somebody has escrowed real value here. If this "
                        f"deployment is unfrozen, the agreed answer is to freeze now "
                        f"rather than to wait out the schedule: they are trusting a "
                        f"keyholder, and they did not agree to that. Targets app "
                        f"{now['target_app']}, every {now['interval_rounds']} rounds.",
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
            events.append(
                Event(
                    "executed",
                    upkeep_id,
                    f"**Upkeep {upkeep_id} executed**"
                    + (f" ×{runs}" if runs > 1 else "")
                    + f", {_algos(_burst_cost(before, now, runs))} paid, "
                    f"next due at round {now['next_execution_round']}",
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
    """What a run of `runs` executions took out of the escrow.

    The exact answer is the balance delta, and it is sitting in the two
    snapshots. No model of the fee curve can beat it, and a model would be
    wrong for a burst whose runs were not all priced the same. Falls back to
    the curve only when a top-up landed in the same window and made the delta
    meaningless.
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


def _is_execute(inner: dict) -> bool:
    """Whether an application call's first argument is the execute selector."""
    args = inner.get("apaa") or []
    if not args:
        return False
    first = args[0]
    if isinstance(first, str):
        try:
            first = base64.b64decode(first)
        except Exception:
            return False
    if not isinstance(first, (bytes, bytearray)):
        return False
    return bytes(first)[:4] == EXECUTE_SELECTOR


def attribute(algod, app_id: int, since_round: int, until_round: int) -> str | None:
    """Which account executed, read from the blocks that can say.

    Box state records that an upkeep ran, never who ran it. An indexer would
    answer this, but so does algod: the execution happened between the last
    scan and this one, which in normal operation is a couple of blocks.

    Deliberately not derived from the upkeep's schedule. An upkeep catching up
    after an outage runs in a round far ahead of the one it was *scheduled*
    for, and using the schedule would attribute it to the wrong block, or to
    a block that has since been pruned.
    """
    newest = max(until_round, 0)
    oldest = max(since_round + 1, newest - MAX_ATTRIBUTION_BLOCKS + 1, 1)
    for round_number in range(newest, oldest - 1, -1):
        try:
            block = algod.block_info(round_number)
        except Exception:  # pruned or unavailable; attribution is a nicety
            return None
        for txn in block.get("block", {}).get("txns") or []:
            inner = txn.get("txn", {})
            if (
                inner.get("type") == "appl"
                and inner.get("apid") == app_id
                and _is_execute(inner)
            ):
                sender = inner.get("snd")
                if sender:
                    return _as_address(sender)
    return None


def summarise(snapshot: Snapshot, executions: int, paid: int) -> str:
    dormant = len(snapshot.dormant)
    return (
        f"📊 **Registry**: {len(snapshot.upkeeps)} upkeeps, {executions} executions "
        f"since the last summary, {_algos(paid)} paid to keepers"
        + (f", {dormant} out of funds" if dormant else "")
    )


def post(webhook: str | None, message: str) -> None:
    """Send to Discord, or to the terminal when no webhook is configured."""
    if not webhook:
        logger.info(message)
        return
    body = json.dumps({"content": message[:MAX_MESSAGE_CHARS]}).encode()
    request = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # Rate limited: wait what Discord asks for, then move on. Missing an
            # announcement is better than a stuck notifier.
            try:
                asked = float(exc.headers.get("Retry-After", "2"))
            except (TypeError, ValueError):
                asked = 2.0
            retry_after = min(max(asked, 0.0), MAX_RETRY_AFTER_SECONDS)
            logger.warning(f"Rate limited; sleeping {retry_after}s")
            time.sleep(retry_after)
        else:
            logger.warning(f"Discord rejected the post ({exc.code})")
    except Exception as exc:
        logger.warning(f"Could not post: {exc}")


def state_path(network: str, app_id: int) -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "arcron" / f"notifier-{network}-{app_id}.json"


def load(path: Path | None) -> Snapshot:
    """Pick up where we left off, so a restart does not replay history."""
    if path is None or not path.exists():
        return Snapshot()
    try:
        return Snapshot.from_json(json.loads(path.read_text()))
    except Exception as exc:
        logger.warning(f"Ignoring unreadable state {path}: {exc}")
        return Snapshot()


def save(path: Path | None, snapshot: Snapshot) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot.to_json(), indent=2))
    temporary.replace(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one scan, then exit")
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None)
    parser.add_argument(
        "--summary-every",
        type=int,
        default=240,
        help="scans between registry summaries (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file", type=Path, default=None, help="where to remember what was announced"
    )
    parser.add_argument(
        "--ours",
        default=None,
        help=(
            "comma-separated addresses whose upkeeps are expected. Any other creator is "
            "announced as a stranger. Empty means announce nobody as a stranger, which "
            "is right on a shared TestNet app and wrong on a MainNet one whose id is "
            "supposed to be unpublished. Defaults to ARCRON_OURS in the environment, "
            "which is how the container and the systemd unit pass it."
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
    parser.add_argument("--no-state", action="store_true", help="announce from scratch each run")
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
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    path = None if args.no_state else (args.state_file or state_path(args.network, app_id))

    ours = args.ours if args.ours is not None else os.environ.get("ARCRON_OURS", "")
    known_creators = frozenset(a.strip() for a in ours.split(",") if a.strip())
    if args.network == net.MAINNET:
        # On MainNet a watcher that cannot tell a stranger, or that tells nobody,
        # is the failure it exists to prevent, so both are refused at startup
        # rather than logged past. The plan for an unfrozen deployment is to
        # freeze the moment somebody who is not us escrows, and that plan is
        # this process noticing and somebody reading it.
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
    executions_since_summary = 0
    paid_since_summary = 0
    scans = 0

    while True:
        try:
            current_round = algod.status()["last-round"]
            snapshot = Snapshot.of(scan_upkeeps(algod, app_id), current_round)
            for event in diff(previous, snapshot, known_creators):
                text = event.text
                if event.kind == "executed":
                    keeper = attribute(algod, app_id, previous.last_round, current_round)
                    if keeper:
                        text += f"\n↳ keeper `{keeper[:8]}…{keeper[-6:]}`"
                    state = snapshot.upkeeps[event.upkeep_id]
                    executions_since_summary += 1
                    paid_since_summary += state["fee_per_execution"]
                post(webhook, text)
                time.sleep(POST_INTERVAL_SECONDS)

            previous = snapshot
            save(path, snapshot)
            scans += 1
            if args.summary_every > 0 and scans % args.summary_every == 0:
                post(webhook, summarise(snapshot, executions_since_summary, paid_since_summary))
                executions_since_summary = 0
                paid_since_summary = 0

            if args.once:
                return
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            logger.info("Stopping")
            return
        except Exception as exc:
            if args.once:
                raise
            logger.warning(f"{exc}; retrying")
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
