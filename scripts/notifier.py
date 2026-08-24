"""A read-only watcher that says what the keeper network is doing.

A network whose work is invisible looks dead even when it is running fine.
This watches the registry and announces what changed — executions and who
earned them, upkeeps registered and cancelled, and the failures: upkeeps gone
dormant for lack of funds, or overdue by an unusual margin. Surfacing those
builds more trust than hiding them.

**It holds no keys and cannot sign anything.** That boundary is deliberate and
enforced by a test: a notifier that could sign would be a liability with no
upside. It reads algod and posts to a webhook, and that is all it can do.

No indexer is needed. Box state gives everything except *which* keeper earned
an execution — and for that the watcher already knows the exact round to look
at, so it fetches that one block.

Run:  poetry run python -m scripts.notifier [--once] [--network N] [--app-id N]
"""

import argparse
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
from scripts.keeper_bot import Upkeep, resolve_app_id, scan_upkeeps

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
POLL_SECONDS = 5


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
                }
                for upkeep in upkeeps
            },
            dormant={u.upkeep_id for u in upkeeps if u.balance < u.fee_per_execution},
            stalled={
                u.upkeep_id
                for u in upkeeps
                if u.balance >= u.fee_per_execution
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


def diff(previous: Snapshot, current: Snapshot) -> list[Event]:
    """What changed between two views of the registry.

    Pure, so the interesting cases can be tested without a chain. Conditions
    are edge-triggered: an upkeep that is dormant for a week is announced once,
    not on every scan.
    """
    events: list[Event] = []

    for upkeep_id, now in sorted(current.upkeeps.items()):
        before = previous.upkeeps.get(upkeep_id)
        if before is None:
            if previous.upkeeps:  # a first run is not a flood of "new upkeep"
                events.append(
                    Event(
                        "registered",
                        upkeep_id,
                        f"**Upkeep {upkeep_id} registered** — target app "
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
                    + f" — {_algos(now['fee_per_execution'] * runs)} paid, "
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
                    f"⚠️ **Upkeep {upkeep_id} has run dry** — escrow "
                    f"{_algos(state['balance'])} is below its "
                    f"{_algos(state['fee_per_execution'])} fee, so no keeper can run it. "
                    f"Anyone can top it up.",
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
                f"⚠️ **Upkeep {upkeep_id} is going unserviced** — funded and due, "
                f"but {overdue} rounds late. Nobody is keeping it.",
            )
        )

    return events


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


def attribute(algod, app_id: int, since_round: int, until_round: int) -> str | None:
    """Which account executed, read from the blocks that can say.

    Box state records that an upkeep ran, never who ran it. An indexer would
    answer this, but so does algod: the execution happened between the last
    scan and this one, which in normal operation is a couple of blocks.

    Deliberately not derived from the upkeep's schedule. An upkeep catching up
    after an outage runs in a round far ahead of the one it was *scheduled*
    for, and using the schedule would attribute it to the wrong block — or to
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
            if inner.get("type") == "appl" and inner.get("apid") == app_id:
                sender = inner.get("snd")
                if sender:
                    return _as_address(sender)
    return None


def summarise(snapshot: Snapshot, executions: int, paid: int) -> str:
    dormant = len(snapshot.dormant)
    return (
        f"📊 **Registry** — {len(snapshot.upkeeps)} upkeeps, {executions} executions "
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
            retry_after = float(exc.headers.get("Retry-After", "2"))
            logger.warning(f"Rate limited; sleeping {retry_after}s")
            time.sleep(retry_after)
        else:
            logger.warning(f"Discord rejected the post ({exc.code})")
    except Exception as exc:
        logger.warning(f"Could not post: {exc}")


def state_path(network: str, app_id: int) -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "archon" / f"notifier-{network}-{app_id}.json"


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
    parser.add_argument("--no-state", action="store_true", help="announce from scratch each run")
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id, args.network)
    algod = algorand.client.algod
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    path = None if args.no_state else (args.state_file or state_path(args.network, app_id))

    logger.info(
        f"Watching app {app_id} on {args.network}; "
        f"{'posting to Discord' if webhook else 'printing here (set DISCORD_WEBHOOK_URL to post)'}"
    )
    previous = load(path)
    executions_since_summary = 0
    paid_since_summary = 0
    scans = 0

    while True:
        try:
            current_round = algod.status()["last-round"]
            snapshot = Snapshot.of(scan_upkeeps(algod, app_id), current_round)
            for event in diff(previous, snapshot):
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
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Stopping")
            return
        except Exception as exc:
            if args.once:
                raise
            logger.warning(f"{exc}; retrying")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
