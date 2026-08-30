"""Would running a keeper here be worth it? Answered from the chain, not the docs.

Alpha task #93 asks somebody to run a keeper for an hour and say whether it
was worth it. Nobody has, and the reason is the order of the questions: to
find out what a keeper earns you currently have to install a toolchain, fund
an account and run a bot for an hour. This answers it first, and read-only, so
the hour is a decision rather than a gamble.

Everything here is measured rather than argued:

  what a keeper was paid    the inner payment `execute` sends the caller
  what it cost              the group fee on the execute transaction itself
  whether work exists now   the due upkeeps, simulated, so a target that
                            reverts is never counted as money on the table

**The competition is the point, and it is not hidden.** What the registry pays
is split among whoever shows up, so this reports the total *and* the current
split, and says plainly that arriving makes each share smaller. A tool that
quoted the gross and let the reader assume they would take all of it would be
the same lie as quoting a fee without its execution cost.

Reads public state. Holds no account and signs nothing.

Run:  poetry run python -m scripts.keeper_preview [--network N] --app-id N
"""

from __future__ import annotations

import argparse
import base64
import logging
from collections import Counter
from dataclasses import dataclass

from scripts import network as net
from scripts.keeper_bot import EXECUTION_COST_MICROALGO, effective_fee, scan_upkeeps
from scripts.registry_health import (
    LOOKBACK_ROUNDS,
    UpkeepHealth,
    execute_selector,
    read_upkeeps,
    simulate_execute,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Execution:
    """One `execute` that landed, and what it actually moved."""
    round: int
    keeper: str
    #: The inner payment the contract sent the caller.
    paid: int
    #: The group fee the keeper paid to send it. Not the inner fees, which
    #: the contract pools from this one.
    cost: int

    @property
    def net(self) -> int:
        return self.paid - self.cost


@dataclass(frozen=True)
class Earnings:
    executions: list[Execution]
    lookback_rounds: int
    seconds_per_round: float

    @property
    def days(self) -> float:
        return self.lookback_rounds * self.seconds_per_round / 86_400

    @property
    def gross(self) -> int:
        return sum(e.paid for e in self.executions)

    @property
    def net(self) -> int:
        return sum(e.net for e in self.executions)

    @property
    def per_day(self) -> float:
        """Net microalgo a day across every keeper, not for any one of them."""
        return self.net / self.days if self.days else 0.0

    @property
    def by_keeper(self) -> list[tuple[str, int, int]]:
        """(address, executions, net), busiest first."""
        counts: Counter[str] = Counter()
        nets: Counter[str] = Counter()
        for e in self.executions:
            counts[e.keeper] += 1
            nets[e.keeper] += e.net
        return [(k, c, nets[k]) for k, c in counts.most_common()]

    @property
    def share_if_one_more(self) -> float:
        """Net a day a newcomer might see, if the work split evenly.

        Deliberately pessimistic about the newcomer and honest about the
        arithmetic: an extra keeper does not create work, it divides it.
        Real races are won on latency, not evenly, so this is a middle
        estimate rather than a floor or a promise.
        """
        keepers = len(self.by_keeper) + 1
        return self.per_day / keepers if keepers else 0.0


@dataclass(frozen=True)
class Opportunity:
    """A due upkeep, and what taking it would pay right now."""
    upkeep_id: int
    target_app: int
    pays: int
    blocked: str

    @property
    def net(self) -> int:
        return self.pays - EXECUTION_COST_MICROALGO

    @property
    def real(self) -> bool:
        """Money actually on the table, rather than a call that would fail."""
        return not self.blocked and self.net > 0


def read_executions(indexer, app_id: int, min_round: int) -> list[Execution]:
    """Every `execute` since `min_round`, with what it paid and what it cost.

    Filtered on the `execute` selector, like `registry_health.read_keepers`:
    an execution is a call to `execute`, not a call to the application.
    """
    wanted = execute_selector()
    response = indexer.search_transactions(
        application_id=app_id, min_round=min_round, limit=1_000
    )
    found: list[Execution] = []
    for txn in response["transactions"]:
        args = txn.get("application-transaction", {}).get("application-args", [])
        if not (args and base64.b64decode(args[0]) == wanted):
            continue
        # The fee payment is the one inner transaction that pays the sender.
        paid = sum(
            inner["payment-transaction"]["amount"]
            for inner in txn.get("inner-txns", [])
            if inner.get("payment-transaction", {}).get("receiver") == txn["sender"]
        )
        found.append(Execution(
            round=txn["confirmed-round"], keeper=txn["sender"],
            paid=paid, cost=txn["fee"],
        ))
    return found


def read_opportunities(
    algod, app_id: int, upkeeps: list[UpkeepHealth], current_round: int, sender: str
) -> list[Opportunity]:
    """What is due right now, simulated so nothing unearnable is counted."""
    raw = {u.upkeep_id: u for u in scan_upkeeps(algod, app_id)}
    found = []
    for health in upkeeps:
        upkeep = raw.get(health.upkeep_id)
        if upkeep is None or current_round < upkeep.next_execution_round:
            continue
        found.append(Opportunity(
            upkeep_id=health.upkeep_id,
            target_app=health.target_app,
            pays=effective_fee(upkeep, current_round),
            blocked=simulate_execute(algod, app_id, health, sender),
        ))
    return sorted(found, key=lambda o: -o.net)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--lookback", type=int, default=LOOKBACK_ROUNDS)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod, indexer = algorand.client.algod, algorand.client.indexer
    spr = net.seconds_per_round(args.network)
    current = int(algod.status()["last-round"])

    executions = read_executions(indexer, args.app_id, current - args.lookback)
    earnings = Earnings(executions, args.lookback, spr)
    upkeeps = read_upkeeps(algod, args.app_id, spr, current)
    # Simulate as an address already executing, so the contract's own checks
    # see what they would really see. With nobody executing there is no view
    # to borrow, and the due list is reported without a verdict.
    asker = earnings.by_keeper[0][0] if earnings.by_keeper else ""
    opportunities = (
        read_opportunities(algod, args.app_id, upkeeps, current, asker) if asker else []
    )

    logger.info(f"app {args.app_id} on {args.network}, round {current:,}")
    logger.info(f"{len(upkeeps)} upkeep(s) registered")
    logger.info("")

    logger.info(f"── what the registry actually paid, last {earnings.days:.1f} day(s) ──")
    if not executions:
        logger.warning("  Nothing executed. Either the registry is idle or it is unserviced.")
    else:
        logger.info(f"  {len(executions)} execution(s), {earnings.gross / 1e6:.3f} ALGO in fees, "
                    f"{earnings.net / 1e6:.3f} ALGO net of what they cost to send")
        logger.info(f"  {earnings.per_day / 1e6:.3f} ALGO a day, across every keeper")
        logger.info("")
        for address, count, net_paid in earnings.by_keeper:
            logger.info(f"    {address[:12]}...  {count:>4} execution(s)  "
                        f"{net_paid / 1e6:>8.3f} ALGO net")

    logger.info("")
    logger.info("── what you would be joining ──")
    if executions:
        logger.info(f"  {len(earnings.by_keeper)} keeper(s) share that today. Arriving makes "
                    f"{len(earnings.by_keeper) + 1}, and an extra keeper divides the work "
                    f"rather than creating it:")
        logger.info(f"  roughly {earnings.share_if_one_more / 1e6:.3f} ALGO a day if it split "
                    f"evenly, which it will not: races are won on latency.")
    logger.info("  A race you lose costs nothing. Algorand rejects a failing transaction at "
                "validation, so it never reaches a block and you are never charged for it.")

    logger.info("")
    logger.info("── what is executable this round ──")
    real = [o for o in opportunities if o.real]
    if not opportunities:
        logger.info("  Nothing is due. Every upkeep is on schedule, which is the healthy case.")
    for opportunity in opportunities:
        line = (f"  #{opportunity.upkeep_id:<4} target {opportunity.target_app}  "
                f"pays {opportunity.pays:>6}  costs {EXECUTION_COST_MICROALGO}  "
                f"net {opportunity.net:>+7}")
        if opportunity.blocked:
            logger.warning(f"{line}   {opportunity.blocked}")
        else:
            logger.info(line)
    if real:
        logger.info("")
        logger.info(f"  {sum(o.net for o in real) / 1e6:.3f} ALGO on the table right now, "
                    f"across {len(real)} upkeep(s).")
    elif opportunities:
        logger.info("")
        logger.warning("  Nothing here is earnable: every due upkeep would fail. That is a "
                       "registry to fix, not a keeper to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
