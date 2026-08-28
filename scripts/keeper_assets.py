"""What an ASA bonus is worth to a keeper operator, and what accepting one costs.

The contract pays an upkeep's ASA bonus only to a keeper already opted in to
that asset (`smart_contracts/keeper/contract.py::execute`). An operator
therefore has a decision to make per asset, once, and until now nothing in
this repository helped them make it. `scripts/keeper_bot.py` decodes
`fee_asset`, `asset_fee` and `asset_balance` and otherwise ignores them.

**The cost of accepting a bonus is a flow, not the opt-in deposit.** The
deposit is 100,000 microAlgos of minimum balance, and it is locked rather than
spent: an asset holding can be closed out and the minimum balance released,
unlike the app account's own `opt_in_asset` deposit, which is permanent by
design. What the opt-in really costs is 1,000 microAlgos on *every* execution
of *every* upkeep naming that asset, for as long as the opt-in stands:

    not opted in    pays 3,000    receives the fee              net  fee - 3,000
    opted in        pays 4,000    receives the fee and a bonus  net  fee - 4,000 + bonus

The bonus transfer is a third inner transaction, so a keeper that can receive
one has to fund it. It cannot decline: Algorand pools fees and does not refund
the unused part, so a keeper that skipped the surcharge and then received a
bonus would have signed an underfunded group, and the execution would fail
outright. There is no per-execution opt-out, only opting out of the asset.

So the difference an opt-in makes is `bonus - 1,000` microAlgos per execution,
independent of the fee, of the escalation cap, and of how late the upkeep is.
That is the whole decision, and it turns on one number this cannot know: what
the asset is worth. So it does not guess. It reports the **break-even unit
price**, the value at which a bonus exactly covers the surcharge it costs to
receive, and leaves the operator to compare it with a price only they can
source. An asset worth less than that break-even is a permanent tax on every
upkeep naming it.

Reads public box state and public account state. Holds no account, signs
nothing, and opts in to nothing: acting on this is a deliberate operator
decision, made once, not something a loop does at 3am.

Run:  poetry run python -m scripts.keeper_assets [--network N] [--app-id N]
                                                 [--keeper-address ADDR]
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from scripts import network as net

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# What accepting a bonus adds to every execution of an upkeep naming the
# asset: the pooled fee for the bonus transfer, a third inner transaction.
# The twin of `keeper_bot.BONUS_FEE_MICROALGO`, which is what actually pays it.
SURCHARGE_MICROALGO = 1_000

#: What a keeper spends in group fees on an execution that pays no bonus: the
#: outer call plus the two inner transactions the contract submits.
#:
#: Mirrored from `keeper_bot.EXECUTION_COST_MICROALGO` rather than imported,
#: because the bot imports this module and the cycle is worse than the
#: duplication. `test_keeper_assets.py` asserts the two agree, so a change to
#: one that is not made to the other fails rather than drifts.
EXECUTION_COST_MICROALGO = 3_000

#: Below this, a daily margin is treated as zero.
#:
#: Every rate here is a float over a per-day divisor, so a margin that is
#: mathematically exactly zero, which is what an upkeep at `MIN_UPKEEP_FEE`
#: produces once opted in, lands a few parts in a trillion either side of it.
#: Comparing that against zero decides the most important case by rounding
#: error. One microAlgo a day is far below anything an operator would act on.
NEGLIGIBLE_MICROALGO_PER_DAY = 1.0
# What an opt-in locks in the keeper's own minimum balance. Released in full
# by a close-out, so this is capital tied up rather than money spent.
OPT_IN_MBR_MICROALGO = 100_000
# What is genuinely spent to hold an asset for a while and then stop: the
# opt-in transaction and the close-out that releases the deposit again.
OPT_IN_ROUND_TRIP_MICROALGO = 2_000
# Algorand's nominal block time, as everywhere else in this repository
# (`scripts/keeper_e2e.py`, `js/src/networks.ts`). Accrual is quoted per day,
# so it needs one; a real chain runs a little either side of this.
SECONDS_PER_ROUND = 2.8
SECONDS_PER_DAY = 86_400


class UpkeepLike(Protocol):
    """The fields of an upkeep this reads.

    Structural rather than an import of `keeper_bot.Upkeep`, so that the bot
    can import this module for its own startup warning without a cycle.
    """

    upkeep_id: int
    interval_rounds: int
    fee_per_execution: int
    balance: int
    fee_asset: int
    asset_fee: int
    asset_balance: int


@dataclass(frozen=True)
class AssetPosition:
    """One fee asset, as it stands across the whole registry.

    Every rate is per day at `SECONDS_PER_ROUND`, and every count is of
    upkeeps rather than of executions unless it says otherwise.
    """

    asset_id: int
    #: Every upkeep naming this asset, whether or not it can pay a bonus.
    upkeep_ids: tuple[int, ...]
    #: Those that would actually pay one on their next execution: the asset
    #: escrow covers the bonus and the ALGO escrow covers the fee. An upkeep
    #: failing either accrues nothing, so it is counted but not projected.
    live_upkeep_ids: tuple[int, ...]
    #: Bonus units this asset would pay per day across its live upkeeps.
    units_per_day: float
    #: Executions per day those upkeeps represent, which is what the
    #: surcharge is charged on.
    executions_per_day: float
    #: The ALGO fees those same live upkeeps pay per day. Needed because the
    #: surcharge is charged against this, not only against the bonus.
    micro_algo_per_day: float
    #: Bonus units left in escrow across every upkeep naming the asset,
    #: including ones that cannot currently execute.
    escrowed_units: int
    #: How many more bonuses the escrows can pay between them.
    remaining_bonuses: int
    #: True or False when a keeper address was given, None when none was.
    opted_in: bool | None

    @property
    def upkeeps(self) -> int:
        return len(self.upkeep_ids)

    @property
    def live(self) -> int:
        return len(self.live_upkeep_ids)

    @property
    def surcharge_per_day(self) -> float:
        """What being opted in to this asset costs per day, in microAlgos."""
        return self.executions_per_day * SURCHARGE_MICROALGO

    @property
    def units_per_execution(self) -> float | None:
        """The mean bonus, weighted by how often each upkeep runs.

        Weighted rather than averaged flat: an upkeep running hourly decides
        this asset's economics far more than a monthly one paying the same
        bonus, and a flat mean would hide that.
        """
        if self.executions_per_day <= 0:
            return None
        return self.units_per_day / self.executions_per_day

    @property
    def break_even_micro_algo_per_unit(self) -> float | None:
        """What one base unit must be worth for the opt-in to pay.

        None when nothing is accruing, which is not the same as zero: an asset
        with no live upkeep has no break-even because it has no bonus.
        """
        units = self.units_per_execution
        if units is None or units <= 0:
            return None
        return SURCHARGE_MICROALGO / units

    @property
    def runway_days(self) -> float | None:
        """How long the escrows last at the current rate.

        The count of bonuses left says nothing on its own: six is a fortnight
        for a daily upkeep and three minutes for one running every ten rounds.
        An asset accruing handsomely for another hour is not worth an opt-in.
        """
        if self.executions_per_day <= 0:
            return None
        return self.remaining_bonuses / self.executions_per_day

    @property
    def algo_margin_per_day(self) -> float:
        """What these upkeeps pay in ALGO per day, net of what they cost to run.

        This is the half the break-even price says nothing about. An operator
        can clear the bonus break-even comfortably and still be executing for
        nothing, because the surcharge is charged against the ALGO margin and
        not against the bonus.
        """
        return self.micro_algo_per_day - self.executions_per_day * EXECUTION_COST_MICROALGO

    @property
    def algo_margin_per_day_opted_in(self) -> float:
        """The same margin once the surcharge applies."""
        return self.algo_margin_per_day - self.surcharge_per_day

    @property
    def surcharge_takes_the_whole_algo_margin(self) -> bool:
        """True when opting in leaves nothing, or less than nothing, in ALGO.

        The case that makes this worth reporting is an upkeep at
        `MIN_UPKEEP_FEE`. It pays 4,000 and costs 3,000 to execute, so it
        earns 1,000 a run. Opting in makes it cost 4,000, and the margin is
        exactly zero: the keeper works for the token alone. Nothing in the
        bonus arithmetic shows that, because the bonus is unchanged.

        Compared against `NEGLIGIBLE_MICROALGO_PER_DAY` rather than against
        zero, because that exact case is where the subtraction cancels and
        floating point stops being reliable: the margin comes out at about
        -4e-12 rather than 0, and it could as easily have come out a hair
        positive, which would have silenced the warning on the one upkeep it
        exists for. A margin under a microAlgo a day is not a margin.
        """
        return (
            self.algo_margin_per_day > NEGLIGIBLE_MICROALGO_PER_DAY
            and self.algo_margin_per_day_opted_in <= NEGLIGIBLE_MICROALGO_PER_DAY
        )

    def net_micro_algo_per_day(self, micro_algo_per_unit: float) -> float:
        """What opting in would earn per day at an operator's own valuation.

        Negative is the answer that matters: it means the surcharge exceeds
        the bonus, and the opt-in would cost more than it collects for as long
        as it stands.
        """
        return self.units_per_day * micro_algo_per_unit - self.surcharge_per_day

    def days_to_repay(self, micro_algo_per_unit: float) -> float | None:
        """Days for the opt-in to earn back its round-trip transaction fees.

        The deposit is not in this: it comes back on close-out. Only the two
        transactions do not. None when the position never repays, at any
        horizon, because it does not clear the surcharge.
        """
        per_day = self.net_micro_algo_per_day(micro_algo_per_unit)
        if per_day <= 0:
            return None
        return OPT_IN_ROUND_TRIP_MICROALGO / per_day


def executions_per_day(interval_rounds: int) -> float:
    """How often an upkeep on this cadence runs, at nominal block time."""
    if interval_rounds <= 0:
        return 0.0
    return SECONDS_PER_DAY / (interval_rounds * SECONDS_PER_ROUND)


def positions(
    upkeeps: Iterable[UpkeepLike],
    *,
    opted_in: Iterable[int] | None = None,
) -> list[AssetPosition]:
    """Group a registry by fee asset, richest accrual first.

    `opted_in` is the keeper's own asset holdings; pass None when there is no
    keeper to speak for, and every position reports `opted_in=None` rather
    than a misleading False.

    Needs no current round, which is not an approximation. Whether an upkeep
    can pay its ALGO fee is `balance >= effective_fee(upkeep, round)`, and
    that is exactly `balance >= fee_per_execution` at every round: the
    escalation in `keeper_bot.effective_fee` drops back to the base fee
    whenever the escrow cannot cover the escalated one, so the escalated fee
    is never the binding constraint. `test_keeper_assets.py` pins that
    equivalence, because it is a property of the escalation rather than of
    this function, and it would stop holding silently.
    """
    held = None if opted_in is None else frozenset(opted_in)

    grouped: dict[int, list[UpkeepLike]] = {}
    for upkeep in upkeeps:
        if upkeep.fee_asset > 0:
            grouped.setdefault(upkeep.fee_asset, []).append(upkeep)

    found: list[AssetPosition] = []
    for asset_id, naming in grouped.items():
        live = [
            upkeep
            for upkeep in naming
            # Both escrows have to hold, and for the same reason the contract
            # checks both: a bonus is only paid alongside an execution, and an
            # upkeep that cannot pay its ALGO fee is not executed at all.
            if upkeep.asset_fee > 0
            and upkeep.asset_balance >= upkeep.asset_fee
            and upkeep.balance >= upkeep.fee_per_execution
        ]
        found.append(
            AssetPosition(
                asset_id=asset_id,
                upkeep_ids=tuple(sorted(upkeep.upkeep_id for upkeep in naming)),
                live_upkeep_ids=tuple(sorted(upkeep.upkeep_id for upkeep in live)),
                units_per_day=sum(
                    upkeep.asset_fee * executions_per_day(upkeep.interval_rounds)
                    for upkeep in live
                ),
                executions_per_day=sum(
                    executions_per_day(upkeep.interval_rounds) for upkeep in live
                ),
                micro_algo_per_day=sum(
                    upkeep.fee_per_execution * executions_per_day(upkeep.interval_rounds)
                    for upkeep in live
                ),
                escrowed_units=sum(upkeep.asset_balance for upkeep in naming),
                remaining_bonuses=sum(
                    upkeep.asset_balance // upkeep.asset_fee
                    for upkeep in naming
                    if upkeep.asset_fee > 0
                ),
                opted_in=None if held is None else asset_id in held,
            )
        )
    # By what is actually accruing, then by id: an operator reads this to pick
    # what to opt in to next, and registry order answers a different question.
    return sorted(found, key=lambda position: (-position.units_per_day, position.asset_id))


def forgone(found: Sequence[AssetPosition]) -> list[AssetPosition]:
    """Positions paying a bonus this keeper is not opted in to.

    The silent earnings leak `docs/design/asa-fees.md` predicted: the
    execution succeeds, the ALGO fee arrives, and the bonus stays in escrow
    with nothing anywhere saying it was missed.
    """
    return [
        position
        for position in found
        if position.opted_in is False and position.units_per_day > 0
    ]


# --- naming the asset -------------------------------------------------


@dataclass(frozen=True)
class AssetInfo:
    """What algod says an asset is called. Display only; never a decision."""

    asset_id: int
    unit_name: str
    decimals: int

    @property
    def label(self) -> str:
        return f"{self.unit_name or 'units'}"

    def whole(self, base_units: float) -> float:
        return base_units / (10**self.decimals)

    def amount(self, base_units: float) -> str:
        return f"{self.whole(base_units):,.{self.decimals}f} {self.label}"


def describe_asset(algod, asset_id: int) -> AssetInfo:
    """The asset's own name and decimals, or a usable placeholder.

    Best effort by design. A node that will not answer, or an asset destroyed
    since an upkeep named it, must not stop the report: the economics are the
    point here and they do not depend on what the asset is called.
    """
    try:
        params = algod.asset_info(asset_id)["params"]
        return AssetInfo(
            asset_id=asset_id,
            unit_name=params.get("unit-name") or f"asset {asset_id}",
            decimals=int(params.get("decimals", 0)),
        )
    except Exception:
        return AssetInfo(asset_id=asset_id, unit_name=f"asset {asset_id}", decimals=0)


def holdings(algod, address: str) -> set[int]:
    """Every asset an account is opted in to."""
    return {
        holding["asset-id"] for holding in algod.account_info(address).get("assets", [])
    }


def micro_algo(value: float) -> str:
    """Whole microAlgos, except where that would round the number away.

    A break-even of 0.004 microAlgos per base unit is the whole answer for a
    six-decimal asset, and printing it as "0 microAlgos" is worse than
    printing nothing.
    """
    if 0 < value < 1:
        return f"{value:,.6g} microAlgos"
    return f"{value:,.0f} microAlgos"


def algos(micro: float) -> str:
    return f"{micro / 1_000_000:,.6f}".rstrip("0").rstrip(".") + " ALGO"


def duration(days: float) -> str:
    """A span an operator can hold in their head, one unit of precision."""
    if days >= 2:
        return f"{days:,.0f} days"
    hours = days * 24
    if hours >= 2:
        return f"{hours:,.0f} hours"
    minutes = hours * 60
    return f"{minutes:,.0f} minutes" if minutes >= 1 else "moments"


def describe(position: AssetPosition, info: AssetInfo) -> list[str]:
    """The position as an operator reads it, one decision per block.

    Deliberately ends on the break-even rather than on the accrual. "This
    asset accrues 3 units a day" is a fact an operator still has to do
    arithmetic on; "each unit must be worth more than 0.004 ALGO" is the
    number they can take to a price and act on.
    """
    opt_in = {None: "unknown (no keeper address given)", True: "yes", False: "no"}[
        position.opted_in
    ]
    lines = [
        f"asset {position.asset_id} ({info.label}, {info.decimals} decimals)",
        f"  named by:      {position.upkeeps} upkeep(s) {list(position.upkeep_ids)}, "
        f"{position.live} able to pay a bonus now",
        f"  opted in:      {opt_in}",
    ]
    if position.executions_per_day <= 0:
        lines.append(
            "  accrual:       nothing. Every upkeep naming this asset is out of "
            "bonus escrow, out of ALGO escrow, or both, so opting in would cost "
            "the surcharge and collect no bonus."
        )
        return lines

    break_even = position.break_even_micro_algo_per_unit
    assert break_even is not None  # executions_per_day > 0 implies a bonus
    lines += [
        f"  accrual:       {info.amount(position.units_per_day)} per day "
        f"over {position.executions_per_day:,.1f} execution(s)",
        f"  surcharge:     {micro_algo(position.surcharge_per_day)} per day "
        f"({SURCHARGE_MICROALGO:,} per execution, unavoidable once opted in)",
        f"  runway:        {position.remaining_bonuses:,} bonus(es) left in escrow "
        f"({info.amount(position.escrowed_units)}), about "
        f"{duration(position.runway_days or 0)} at this rate",
        f"  BREAK EVEN:    one {info.label} must be worth more than "
        f"{algos(break_even * 10 ** info.decimals)} "
        f"({micro_algo(break_even)} per base unit) for this opt-in to pay.",
        f"  deposit:       {micro_algo(OPT_IN_MBR_MICROALGO)} locked while opted in, "
        f"released on close-out; {micro_algo(OPT_IN_ROUND_TRIP_MICROALGO)} of "
        f"transaction fees is the only part that does not come back.",
    ]

    # The break-even above answers "is the bonus worth its surcharge". It does
    # not answer "does the ALGO side still pay", and those come apart at the
    # fee floor: an upkeep paying MIN_UPKEEP_FEE earns 1,000 a run and opting
    # in costs exactly that. An operator who read only the break-even would
    # opt in, clear it, and execute for the token alone without being told.
    lines.append(
        f"  ALGO margin:   {micro_algo(position.algo_margin_per_day)} per day now, "
        f"{micro_algo(position.algo_margin_per_day_opted_in)} once opted in "
        f"(fees earned less {EXECUTION_COST_MICROALGO:,} per execution to run them)"
    )
    if position.surcharge_takes_the_whole_algo_margin:
        lines.append(
            "  WARNING:       opting in takes the whole ALGO margin on these "
            "upkeeps. Past this point the bonus is not a bonus, it is the "
            "entire payment, and the break-even above is the only thing "
            "standing between you and working for nothing."
        )
    elif position.algo_margin_per_day <= 0:
        lines.append(
            "  WARNING:       these upkeeps do not cover their own execution "
            "cost in ALGO before any opt-in. The bonus would have to carry "
            "them outright."
        )
    return lines


def report(algod, app_id: int, keeper_address: str | None) -> int:
    """Print the asset board. Returns a process exit code.

    Zero even when nothing is opted in: an operator who has decided not to
    chase bonuses is in a correct state, not a failing one, and a probe that
    said otherwise would be turned off.
    """
    from scripts.keeper_bot import scan_upkeeps

    current = algod.status()["last-round"]
    upkeeps = scan_upkeeps(algod, app_id)
    found = positions(
        upkeeps,
        opted_in=None if keeper_address is None else holdings(algod, keeper_address),
    )

    if not found:
        logger.info(
            "Round %s: no upkeep on app %s names a fee asset, so there is "
            "nothing to opt in to and nothing being forgone.",
            current,
            app_id,
        )
        return 0

    logger.info(
        "Round %s: %s fee asset(s) across %s upkeep(s) on app %s%s",
        current,
        len(found),
        sum(position.upkeeps for position in found),
        app_id,
        "" if keeper_address is None else f", as seen by {keeper_address}",
    )
    for position in found:
        for line in describe(position, describe_asset(algod, position.asset_id)):
            logger.info("%s", line)

    missed = forgone(found)
    if missed:
        logger.warning(
            "Not opted in to %s asset(s) that are paying bonuses right now: %s. "
            "Those executions still earn the full ALGO fee; the bonus stays in "
            "escrow and nothing else records that it was forgone.",
            len(missed),
            ", ".join(str(position.asset_id) for position in missed),
        )
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="Keeper app id (default: KEEPER_APP_ID)",
    )
    parser.add_argument(
        "--keeper-address",
        default=os.environ.get("KEEPER_ADDRESS"),
        help=(
            "report opt-in status as this account sees it. Any address: this "
            "reads public state only, so it works against a keeper you do not "
            "run (default: KEEPER_ADDRESS, else opt-in status is not reported)"
        ),
    )
    args = parser.parse_args(argv)

    from scripts.keeper_bot import resolve_app_id

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id, args.network)
    raise SystemExit(report(algorand.client.algod, app_id, args.keeper_address))


if __name__ == "__main__":
    main()
