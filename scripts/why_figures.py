"""Recompute every number on `docs/why.md` from one stated basis.

That page has had its arithmetic wrong three times: a multiplier that did not
reproduce from its own table, a crossover quoted against a host the page does
not recommend, and a claim about the ALGO price that was simply false. Each was
caught by a reader rather than by anything here.

The failures share a shape. The page states a basis and then quotes figures
derived from it, and nothing recomputed the derivation when the basis moved. So
the derivation lives here, `tests/test_why_figures.py` asserts the page agrees
with it, and changing the basis now fails until the page is updated.

Run:  poetry run python -m scripts.why_figures
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts.network import MAINNET, seconds_per_round

#: An upkeep on a nominal hourly cadence, which is what the page prices.
HOURLY_ROUNDS = 1_286

#: The page says "per month" and means 30 days, not a calendar month.
DAYS_PER_MONTH = 30

#: ALGO spot used throughout. Stated rather than live, because a page whose
#: numbers move on their own cannot be checked against itself.
ALGO_USD = 0.0907

#: `MIN_UPKEEP_FEE`, and what the console suggests instead.
FLOOR_MICROALGO = 4_000
SUGGESTED_MICROALGO = 10_000

#: A self-hosted call still pays this. It is the reason the crossover is not
#: the cost ratio: running your own bot makes the fee smaller, not absent.
OUTER_FEE_MICROALGO = 1_000

#: What executing costs the keeper in group fees, so its net per execution is
#: the fee minus this. At the 4,000 µALGO floor a keeper nets 1,000.
KEEPER_COST_MICROALGO = 3_000

#: The fees the page's fee table sweeps: the floor, the suggested fee, and one
#: step past it to show where the curve goes.
FEE_LADDER = (FLOOR_MICROALGO, SUGGESTED_MICROALGO, 20_000)

#: Monthly cost of the hosts the page compares against.
HOSTS: dict[str, float] = {"fly.io": 2.02, "Hetzner": 4.10, "a $5 host": 5.00}


@dataclass(frozen=True)
class Figures:
    seconds_per_round: float
    executions_per_month: float
    floor_algo: float
    floor_usd: float
    suggested_algo: float
    suggested_usd: float
    outer_usd: float
    saving_usd: float

    def multiple(self, host: str) -> float:
        """How many times cheaper Arcron is at the floor. A ratio of totals."""
        return HOSTS[host] / self.floor_usd

    def multiple_suggested(self, host: str) -> float:
        return HOSTS[host] / self.suggested_usd

    def crossover(self, host: str) -> float:
        """Upkeeps at which self-hosting overtakes, which is not the ratio.

        Divides by what self-hosting *saves*, not by what Arcron costs. A
        reader who divides by the cost gets the multiple and concludes the
        table is broken; a reviewer of this repository nearly did.
        """
        return HOSTS[host] / self.saving_usd

    def parity_algo_price(self, host: str) -> float:
        """The ALGO price at which Arcron at the floor costs what the host does.

        The ratio is a bet on the ALGO price rather than a property of the
        design, and it moves against Arcron precisely when Algorand succeeds.
        """
        return HOSTS[host] / self.floor_algo

    @property
    def minutes_per_firing(self) -> float:
        """How long a nominal-hour upkeep actually waits between firings."""
        return HOURLY_ROUNDS * self.seconds_per_round / 60

    @property
    def monthly_drift_hours(self) -> float:
        """How far a nominal-hour upkeep slides against the calendar per month.

        Each firing lands early by the gap between a wall-clock hour and what
        the rounds actually take, and the phase error accumulates without
        bound. This is the figure that was quoted as 36 hours from the
        abandoned 2.66 basis long after the table around it had moved on.
        """
        early_per_firing = 3_600 - HOURLY_ROUNDS * self.seconds_per_round
        return early_per_firing * self.executions_per_month / 3_600

    def monthly_usd(self, fee_microalgo: int) -> float:
        """What a creator pays per month for one hourly upkeep at this fee."""
        return self.executions_per_month * fee_microalgo / 1e6 * ALGO_USD

    def saving_usd_at(self, fee_microalgo: int) -> float:
        """What self-hosting saves at this fee: the fee less the outer fee."""
        return self.executions_per_month * (fee_microalgo - OUTER_FEE_MICROALGO) / 1e6 * ALGO_USD

    def crossover_at(self, host: str, fee_microalgo: int) -> float:
        """`crossover`, generalised to any fee for the fee table."""
        return HOSTS[host] / self.saving_usd_at(fee_microalgo)

    def keeper_net_usd(self, fee_microalgo: int) -> float:
        """What a keeper clears per upkeep per month at this fee.

        The keeper fronts about 3,000 µALGO of group fees per execution, so
        at the 4,000 µALGO floor it nets 1,000.
        """
        return self.executions_per_month * (fee_microalgo - KEEPER_COST_MICROALGO) / 1e6 * ALGO_USD

    def keeper_break_even(self, host: str, fee_microalgo: int) -> float:
        """Concurrent hourly upkeeps at this fee before a keeper funds a host.

        Like `crossover`, the page quotes it to the nearest integer: these are
        approximate crossings of continuous curves, not counts to bill by.
        """
        return HOSTS[host] / self.keeper_net_usd(fee_microalgo)


def compute(network: str = MAINNET) -> Figures:
    """Every figure, from the block time of the network being priced.

    MainNet by default: the comparison is against hosting billed in dollars,
    and MainNet is where anyone would actually make it.
    """
    spr = seconds_per_round(network)
    per_month = DAYS_PER_MONTH * 86_400 / (HOURLY_ROUNDS * spr)
    floor_algo = per_month * FLOOR_MICROALGO / 1e6
    suggested_algo = per_month * SUGGESTED_MICROALGO / 1e6
    floor_usd = floor_algo * ALGO_USD
    outer_usd = per_month * OUTER_FEE_MICROALGO / 1e6 * ALGO_USD
    return Figures(
        seconds_per_round=spr,
        executions_per_month=per_month,
        floor_algo=floor_algo,
        floor_usd=floor_usd,
        suggested_algo=suggested_algo,
        suggested_usd=suggested_algo * ALGO_USD,
        outer_usd=outer_usd,
        saving_usd=floor_usd - outer_usd,
    )


def main() -> None:
    figures = compute()
    print(f"basis: {figures.seconds_per_round} s/round measured, {HOURLY_ROUNDS} rounds,")
    print(f"       {DAYS_PER_MONTH} days, ALGO ${ALGO_USD}")
    print(f"  executions/month     {figures.executions_per_month:.0f}")
    print(f"  at the floor         {figures.floor_algo:.2f} ALGO = ${figures.floor_usd:.2f}")
    print(f"  at the suggested fee {figures.suggested_algo:.2f} ALGO = ${figures.suggested_usd:.2f}")
    print(f"  self-hosting saves   ${figures.saving_usd:.3f} per upkeep per month")
    for host in HOSTS:
        print(
            f"  vs {host:<10} {figures.multiple(host):.1f}x at floor, "
            f"{figures.multiple_suggested(host):.1f}x suggested, "
            f"crossover {figures.crossover(host):.0f}, "
            f"parity ${figures.parity_algo_price(host):.2f}"
        )
    print(
        f"  drift: fires every {figures.minutes_per_firing:.1f} min, "
        f"slides {figures.monthly_drift_hours:.0f} h/month"
    )
    print("  fee table (creator pays, crossover vs fly.io, keeper funds a $5 host at):")
    for fee in FEE_LADDER:
        print(
            f"    {fee:>6,} µALGO  ${figures.monthly_usd(fee):.2f}  "
            f"{figures.crossover_at('fly.io', fee):.0f}  "
            f"{figures.keeper_break_even('a $5 host', fee):.0f}"
        )


if __name__ == "__main__":
    main()
