"""What is wrong with a live registry right now, and who is servicing it.

Written after hand-rolling this query once and getting it wrong. That version
grouped every application call by sender and reported the one account that had
only ever called `register` as a keeper executing upkeeps. The number was real;
the label was not.

So the rule this module exists to hold: **an execution is a call to `execute`,
not a call to the application.** Everything else here follows from reading the
same box state the keeper reads, and the same account state a keeper operator
would check.

Three questions, which are the ones that actually go wrong:

  is any upkeep about to starve       escrow divided by burn rate
  does any upkeep pay a keeper nothing  fee less what an execution costs
  can the keepers still afford to run   spendable, not total, balance

Reads public state. Holds no account and signs nothing.

Run:  poetry run python -m scripts.registry_health [--network N] --app-id N
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
from collections import Counter
from dataclasses import dataclass

from scripts import network as net
from scripts.keeper_bot import (
    ACCOUNT_MBR_MICROALGO,
    BONUS_FEE_MICROALGO,
    EXECUTION_COST_MICROALGO,
    _decode_upkeep,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Runway below which an upkeep is worth mentioning, in days.
LOW_RUNWAY_DAYS = 7.0

#: A keeper with fewer executions than this in the tank is worth mentioning.
LOW_KEEPER_RUNS = 50

#: How far back to look for executions.
LOOKBACK_ROUNDS = 32_000


def execute_selector() -> bytes:
    """The ARC-4 selector for `execute`, which is what an execution *is*.

    Derived rather than pasted, so it follows the ABI if the signature ever
    changes instead of silently matching nothing.
    """
    sig = "execute(uint64)uint64"
    return hashlib.new("sha512_256", sig.encode()).digest()[:4]


@dataclass(frozen=True)
class UpkeepHealth:
    upkeep_id: int
    target_app: int
    times_executed: int
    net_to_keeper: int
    runway_days: float
    rounds_late: int
    interval_rounds: int

    @property
    def pays_nothing(self) -> bool:
        """The keeper clears nothing, or loses money, on every execution.

        Not hypothetical: an upkeep at `MIN_UPKEEP_FEE` with an ASA bonus costs
        a keeper exactly what it pays, because the bonus transfer is a third
        inner transaction.
        """
        return self.net_to_keeper <= 0

    @property
    def low_runway(self) -> bool:
        return self.runway_days < LOW_RUNWAY_DAYS

    @property
    def overdue(self) -> bool:
        """Late by more than one whole cycle, which is not a race being lost."""
        return self.rounds_late > self.interval_rounds

    def flags(self) -> list[str]:
        found = []
        if self.pays_nothing:
            found.append("PAYS THE KEEPER NOTHING")
        if self.low_runway:
            found.append(f"{self.runway_days:.1f} DAYS OF RUNWAY")
        if self.overdue:
            found.append(f"OVERDUE BY {self.rounds_late:,} ROUNDS")
        return found


def read_upkeeps(algod, app_id: int, seconds_per_round: float, current_round: int) -> list[UpkeepHealth]:
    found: list[UpkeepHealth] = []
    for box in algod.application_boxes(app_id, limit=1_000)["boxes"]:
        name = base64.b64decode(box["name"])
        if not name.startswith(b"u"):
            continue
        upkeep_id = int.from_bytes(name[1:], "big")
        raw = base64.b64decode(algod.application_box_by_name(app_id, name)["value"])
        upkeep = _decode_upkeep(upkeep_id, raw)
        cost = EXECUTION_COST_MICROALGO + (BONUS_FEE_MICROALGO if upkeep.fee_asset else 0)
        runs = upkeep.balance // upkeep.fee_per_execution if upkeep.fee_per_execution else 0
        found.append(
            UpkeepHealth(
                upkeep_id=upkeep_id,
                target_app=upkeep.target_app,
                times_executed=upkeep.times_executed,
                net_to_keeper=upkeep.fee_per_execution - cost,
                runway_days=runs * upkeep.interval_rounds * seconds_per_round / 86_400,
                rounds_late=max(0, current_round - upkeep.next_execution_round),
                interval_rounds=upkeep.interval_rounds,
            )
        )
    return sorted(found, key=lambda u: u.upkeep_id)


def read_keepers(algod, indexer, app_id: int, current_round: int) -> list[tuple[str, int, int, int]]:
    """Who executed recently, and whether they can afford to keep going.

    Filters on the `execute` selector. Counting every application call instead
    is what produced a "keeper" that had only ever registered an upkeep.
    """
    wanted = execute_selector()
    response = indexer.search_transactions(
        application_id=app_id, min_round=current_round - LOOKBACK_ROUNDS, limit=1_000
    )
    counts: Counter[str] = Counter()
    for txn in response["transactions"]:
        args = txn.get("application-transaction", {}).get("application-args", [])
        if args and base64.b64decode(args[0]) == wanted:
            counts[txn["sender"]] += 1

    rows = []
    for address, count in counts.most_common():
        info = algod.account_info(address)
        spendable = int(info["amount"]) - int(info.get("min-balance", ACCOUNT_MBR_MICROALGO))
        rows.append((address, count, spendable, spendable // EXECUTION_COST_MICROALGO))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True)
    args = parser.parse_args(argv)

    net.load_network(args.network)
    algorand = net.connect(args.network)
    algod, indexer = algorand.client.algod, algorand.client.indexer
    spr = net.seconds_per_round(args.network)
    current = int(algod.status()["last-round"])

    upkeeps = read_upkeeps(algod, args.app_id, spr, current)
    keepers = read_keepers(algod, indexer, args.app_id, current)

    logger.info(f"app {args.app_id} on {args.network}, round {current:,}")
    logger.info(f"{len(upkeeps)} upkeep(s), {len(keepers)} keeper(s) executing in the last "
                f"{LOOKBACK_ROUNDS:,} rounds")
    logger.info("")

    problems = 0
    for upkeep in upkeeps:
        flags = upkeep.flags()
        line = (f"  #{upkeep.upkeep_id:<4} target {upkeep.target_app}  "
                f"{upkeep.times_executed} run(s)  {upkeep.net_to_keeper:>6} uALGO to a keeper  "
                f"{upkeep.runway_days:>5.1f}d runway")
        if flags:
            problems += 1
            logger.warning(f"{line}   {' / '.join(flags)}")
        else:
            logger.info(line)

    logger.info("")
    if not keepers:
        problems += 1
        logger.warning("  No keeper has executed anything recently. The registry is unserviced.")
    for address, count, spendable, runs in keepers:
        line = (f"  {address[:12]}...  {count:>3} execution(s)  "
                f"{spendable / 1e6:>9.3f} ALGO spendable  ~{runs} more")
        if runs < LOW_KEEPER_RUNS:
            problems += 1
            logger.warning(f"{line}   RUNNING OUT")
        else:
            logger.info(line)

    logger.info("")
    logger.info(f"{problems} thing(s) worth looking at.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
