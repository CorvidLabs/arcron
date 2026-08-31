"""Bring upkeeps back up to a target number of days of runway.

Written after upkeep 91 came within 1.5 days of starving. Upkeep 91 was the
rain hub draw, which at the time was the dogfood the MainNet clock was
measured against; rain has since moved to CorvidLabs/arcron-rain and the
registry itself is the dogfood now, but the arithmetic is the same for every
upkeep in it. A 30 day hold cannot be served by an upkeep with 1.5 days of
escrow in it, whoever owns the target. The
top-up itself is one call; what was missing was the arithmetic that says
which upkeeps need one and how much, and a record of it that does not live in
somebody's shell history.

**The unit is days, not microalgo.** An upkeep's escrow means nothing on its
own: 0.15 ALGO is six weeks at one call a day and forty minutes at one call a
minute. Every number this prints is a duration, and the microalgo figure is
derived from it rather than the other way round.

The second thing it exists to say out loud: *some upkeeps must not be funded*.
Upkeeps 98 to 109 were registered on a 20 round cadence, 54 seconds, at 4,000
uALGO a call. Reaching 30 days costs 192 ALGO each, 2,308 ALGO for the twelve,
and they will starve again within hours of any smaller amount. That is not an
underfunded upkeep, it is one priced to burn, and the answer is `cancel`, not
a top-up. So a plan skips anything above `--max-per-upkeep` and says why,
rather than draining an account into it.

`top_up` is permissionless: the contract binds the funding payment to the
caller, so anybody may fund anybody's upkeep as long as they pay for it
themselves. This does not need the creator's key, only its own.

Signs transactions, but only with `--send`. The default is a plan.

Run:  poetry run python -m scripts.keeper_topup [--network N] --app-id N
      poetry run python -m scripts.keeper_topup --app-id N --send
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from scripts import network as net
from scripts.keeper_bot import Upkeep, scan_upkeeps

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Days of runway a top-up aims for. Comfortably past a 30 day hold, and the
#: figure `docs/status.md` quotes, so the two agree by construction.
DEFAULT_TARGET_DAYS = 30.0

#: Above this, an upkeep is reported rather than funded. See the module
#: docstring: the twelve 54-second upkeeps cost 192 ALGO each to carry.
DEFAULT_MAX_PER_UPKEEP_MICROALGO = 10_000_000

#: Left in the sending account, so funding the registry never strands the
#: account that has to sign the next cancel or redeploy.
DEFAULT_RESERVE_MICROALGO = 500_000


def runway_days(balance: int, fee: int, interval_rounds: int, seconds_per_round: float) -> float:
    """How long an escrow lasts at a given price per call.

    The twin of `registry_health.read_upkeeps`, which uses the base fee. Whole
    executions only: an escrow that cannot pay for one more call has no runway,
    however many microalgo are left in it.
    """
    if fee <= 0 or interval_rounds <= 0:
        return 0.0
    return (balance // fee) * interval_rounds * seconds_per_round / 86_400


def required_balance(days: float, fee: int, interval_rounds: int, seconds_per_round: float) -> int:
    """The escrow that buys `days` at `fee` a call. The inverse of the above."""
    if fee <= 0 or interval_rounds <= 0:
        return 0
    return int(days * 86_400 / (interval_rounds * seconds_per_round)) * fee


@dataclass(frozen=True)
class TopUp:
    upkeep_id: int
    target_app: int
    microalgo: int
    days_now: float
    days_after: float
    #: Days after, if every call escalated to the ceiling. Equal to
    #: `days_after` where no cap is set.
    floor_days_after: float

    @property
    def escalates(self) -> bool:
        """A capped upkeep can drain faster than `days_after` promises.

        Not a reason to over-fund: the ceiling is only reached when keepers
        are already late, so funding to it would pay for a failure mode
        rather than for the schedule. It is a reason to print both numbers.
        """
        return self.floor_days_after < self.days_after


@dataclass(frozen=True)
class Skipped:
    upkeep_id: int
    target_app: int
    microalgo: int
    days_now: float
    reason: str


def plan(
    upkeeps: list[Upkeep],
    seconds_per_round: float,
    target_days: float = DEFAULT_TARGET_DAYS,
    max_per_upkeep: int = DEFAULT_MAX_PER_UPKEEP_MICROALGO,
    only: set[int] | None = None,
) -> tuple[list[TopUp], list[Skipped]]:
    """What to fund, what to leave alone, and what to cancel instead.

    Plans at the base fee rather than the ceiling, so the days here mean the
    same thing as the days in `fledge run health`.
    """
    funding: list[TopUp] = []
    skipped: list[Skipped] = []
    for upkeep in sorted(upkeeps, key=lambda u: u.upkeep_id):
        if only is not None and upkeep.upkeep_id not in only:
            continue
        fee, interval = upkeep.fee_per_execution, upkeep.interval_rounds
        days_now = runway_days(upkeep.balance, fee, interval, seconds_per_round)
        if fee <= 0 or interval <= 0:
            skipped.append(Skipped(upkeep.upkeep_id, upkeep.target_app, 0, days_now,
                                   "no fee or no interval; nothing to compute"))
            continue
        if days_now >= target_days:
            continue
        needed = required_balance(target_days, fee, interval, seconds_per_round) - upkeep.balance
        if needed <= 0:
            continue
        if needed > max_per_upkeep:
            skipped.append(Skipped(
                upkeep.upkeep_id, upkeep.target_app, needed, days_now,
                f"{needed / 1e6:,.0f} ALGO to carry {target_days:.0f} days at "
                f"{interval} rounds a call; cancel it rather than fund it",
            ))
            continue
        after = upkeep.balance + needed
        ceiling = max(fee, upkeep.fee_cap)
        funding.append(TopUp(
            upkeep_id=upkeep.upkeep_id,
            target_app=upkeep.target_app,
            microalgo=needed,
            days_now=days_now,
            days_after=runway_days(after, fee, interval, seconds_per_round),
            floor_days_after=runway_days(after, ceiling, interval, seconds_per_round),
        ))
    return funding, skipped


def affordable(funding: list[TopUp], spendable: int, reserve: int) -> tuple[list[TopUp], int]:
    """As much of the plan as the account can pay for, neediest first.

    Ordered by how little runway an upkeep has rather than by id, so a short
    account buys days where they are scarcest. Partial funding of a single
    upkeep is deliberately not done: it turns one clear number into two
    unclear ones.
    """
    budget = max(spendable - reserve, 0)
    taken: list[TopUp] = []
    for top_up in sorted(funding, key=lambda t: t.days_now):
        # ~0.002 ALGO of group fees per top-up, so a plan that exactly
        # exhausts the budget still leaves the last call payable.
        if top_up.microalgo + 2_000 <= budget:
            taken.append(top_up)
            budget -= top_up.microalgo + 2_000
    return sorted(taken, key=lambda t: t.upkeep_id), budget


def send_top_up(algorand, app_id: int, sender, top_up: TopUp) -> str:
    """One `top_up` group: the payment and the call, signed by `sender`."""
    import algokit_utils
    from smart_contracts.artifacts.keeper.keeper_client import KeeperClient, TopUpArgs

    client = KeeperClient(
        algorand=algorand, app_id=app_id,
        default_sender=sender.address, default_signer=sender.signer,
    )
    result = client.send.top_up(args=TopUpArgs(
        upkeep_id=top_up.upkeep_id,
        funding_payment=algorand.create_transaction.payment(algokit_utils.PaymentParams(
            sender=sender.address,
            receiver=client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=top_up.microalgo),
        )),
    ))
    return result.tx_ids[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--target-days", type=float, default=DEFAULT_TARGET_DAYS)
    parser.add_argument("--max-per-upkeep", type=int, default=DEFAULT_MAX_PER_UPKEEP_MICROALGO,
                        help="microALGO; above this an upkeep is reported, not funded")
    parser.add_argument("--reserve", type=int, default=DEFAULT_RESERVE_MICROALGO,
                        help="microALGO to leave in the sending account")
    parser.add_argument("--upkeep", type=int, action="append", dest="upkeeps",
                        help="only this upkeep; repeatable")
    parser.add_argument("--account", default="DEPLOYER",
                        help="environment prefix of the funding account")
    parser.add_argument("--send", action="store_true",
                        help="actually send; without it this only prints the plan")
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    spr = net.seconds_per_round(args.network)

    sender = algorand.account.from_environment(args.account)
    info = algod.account_info(sender.address)
    spendable = int(info["amount"]) - int(info["min-balance"])

    upkeeps = scan_upkeeps(algod, args.app_id)
    only = set(args.upkeeps) if args.upkeeps else None
    funding, skipped = plan(upkeeps, spr, args.target_days, args.max_per_upkeep, only)
    chosen, left = affordable(funding, spendable, args.reserve)

    logger.info(f"app {args.app_id} on {args.network}, {len(upkeeps)} upkeep(s)")
    logger.info(f"funding from {args.account} {sender.address[:12]}..., "
                f"{spendable / 1e6:.3f} ALGO spendable, {args.reserve / 1e6:.3f} reserved")
    logger.info(f"target {args.target_days:.0f} days of runway")
    logger.info("")

    if not funding and not skipped:
        logger.info(f"  Every upkeep already holds {args.target_days:.0f} days. Nothing to do.")
        return 0

    for top_up in funding:
        mark = " " if top_up in chosen else "!"
        line = (f" {mark} #{top_up.upkeep_id:<4} target {top_up.target_app}  "
                f"{top_up.days_now:>5.1f}d -> {top_up.days_after:>5.1f}d   "
                f"{top_up.microalgo / 1e6:>8.3f} ALGO")
        if top_up.escalates:
            line += f"   ({top_up.floor_days_after:.1f}d if every call escalates)"
        if top_up in chosen:
            logger.info(line)
        else:
            logger.warning(f"{line}   NOT AFFORDABLE")

    for skip in skipped:
        logger.warning(f" x #{skip.upkeep_id:<4} target {skip.target_app}  "
                       f"{skip.days_now:>5.1f}d           {skip.reason}")

    total = sum(t.microalgo for t in chosen)
    logger.info("")
    logger.info(f"  {len(chosen)} of {len(funding)} fundable upkeep(s), "
                f"{total / 1e6:.3f} ALGO, {left / 1e6:.3f} ALGO of budget unspent")
    if skipped:
        logger.warning(f"  {len(skipped)} upkeep(s) not funded on purpose; see above.")

    if not args.send:
        logger.info("")
        logger.info("  Plan only. Re-run with --send to fund it.")
        return 0

    logger.info("")
    for top_up in chosen:
        txid = send_top_up(algorand, args.app_id, sender, top_up)
        logger.info(f"  #{top_up.upkeep_id} funded {top_up.microalgo / 1e6:.3f} ALGO   {txid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
