"""Forward what a keeper earns to somewhere else, without starving the keeper.

A keeper earns into the same account it spends from. That is what makes it
self-sustaining, and it is also why the balance climbs forever on a profitable
registry while the operator's actual wallet stays empty. Moving the surplus is
a payment, not a contract: the bot already holds the key.

**The whole risk is sweeping too much.** A keeper below
`keeper_bot.HARD_MINIMUM_MICROALGO` cannot broadcast, and a keeper that cannot
broadcast cannot earn its way back out. So every decision here is expressed as
what may *leave* while a reserve stays behind, and the reserve is floored at
the bot's own low balance warning threshold rather than at the minimum balance:
sweeping down to the point where the bot immediately warns is not a sweep, it
is a slow outage. `reserve_for` is where that is enforced, and it cannot be
configured below that floor.

Two triggers, either of which fires:

  threshold   the surplus has reached an amount worth a transaction fee
  duration    a period has elapsed and there is anything worth sending

Nothing sweeps by default. `--sweep-to` is what turns it on, and without a
destination every function here returns "send nothing".

An ASA sweep additionally needs the destination opted in to that asset. This
checks and declines rather than broadcasting a transfer the network will
reject, because a rejected transfer still costs the fee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from scripts.keeper_bot import EXECUTION_COST_MICROALGO, emit

#: What a sweep costs to send. Sweeping less than this loses money.
SWEEP_FEE_MICROALGO = 1_000

#: The default reserve, when an operator names no other: enough that the bot
#: is not warning about its own balance the moment a sweep lands.
#:
#: Spendable headroom, so it does not include the account minimum. That is
#: excluded before this is applied, and including it here would hold back the
#: minimum twice.
DEFAULT_RESERVE_MICROALGO = 100 * EXECUTION_COST_MICROALGO


def reserve_for(requested: int | None, warn_below: int) -> int:
    """The balance a sweep must leave behind.

    Floored at `warn_below` deliberately. An operator who asks to keep 0, or
    to keep only the account minimum, has asked for a keeper that stops
    working, and the fee income that would refill it stops at the same moment.
    The flag configures headroom above that floor, never below it.
    """
    floor = max(warn_below, EXECUTION_COST_MICROALGO)
    if requested is None:
        return max(DEFAULT_RESERVE_MICROALGO, floor)
    return max(requested, floor)


def sweepable(spendable: int, reserve: int) -> int:
    """What may leave the account, in microAlgos.

    **Spendable, never the total balance.** An account's minimum balance is
    not a constant: every asset opt-in adds 100,000, and so does every app or
    asset it created. `keeper_bot.account_floor` measured a live keeper
    holding eleven bonus assets at 5,439,000 against the 100,000 a bare
    account needs. A sweep computed from the total would have treated 5.34
    ALGO of untouchable minimum balance as surplus and tried to send it.

    The reserve is therefore headroom *above* the minimum, not a figure that
    includes it, which is also what makes it comparable with `--min-balance`.
    """
    return max(0, spendable - reserve - SWEEP_FEE_MICROALGO)


@dataclass(frozen=True)
class SweepDecision:
    """Whether to sweep, how much, and the reason, which is logged either way."""

    amount: int
    reason: str

    def __bool__(self) -> bool:
        return self.amount > 0


def decide(
    spendable: int,
    *,
    reserve: int,
    threshold: int | None,
    seconds_since_last: float | None,
    every_seconds: int | None,
) -> SweepDecision:
    """Whether this heartbeat should sweep.

    `spendable` is what the account can actually part with, as
    `keeper_bot.guard_balance` returns it. Passing a total here would
    overstate the surplus by the whole minimum balance.

    Either trigger fires on its own, which is what "a threshold or a duration"
    means. With neither configured nothing sweeps, so turning the destination
    on without a trigger is inert rather than surprising.
    """
    available = sweepable(spendable, reserve)
    if available <= 0:
        return SweepDecision(0, "nothing above the reserve")

    if threshold is not None and available >= threshold:
        return SweepDecision(available, f"surplus {available} reached threshold {threshold}")

    if every_seconds is not None and seconds_since_last is not None:
        if seconds_since_last >= every_seconds:
            return SweepDecision(
                available,
                f"{seconds_since_last:.0f}s since the last sweep, period is {every_seconds}s",
            )

    if threshold is None and every_seconds is None:
        return SweepDecision(0, "no trigger configured")
    return SweepDecision(0, f"surplus {available} below threshold and period not elapsed")


def asset_sweepable(held: int, reserve_units: int) -> int:
    """Bonus units that may leave. Assets have no minimum balance of their own."""
    return max(0, held - reserve_units)


def destination_holds(algod, address: str, asset_id: int) -> bool:
    """Whether the destination can receive this asset.

    An ASA transfer to an account that is not opted in is rejected, and a
    rejected transfer still costs its fee. Asking first is one free read.
    """
    try:
        info = algod.account_info(address)
    except Exception:
        return False
    return any(int(a["asset-id"]) == asset_id for a in info.get("assets", []))


def send(algorand, sender: str, destination: str, amount: int, *, dry_run: bool) -> str | None:
    """Move ALGO. Returns the transaction id, or None when nothing was sent."""
    if amount <= 0:
        return None
    if dry_run:
        emit(
            "sweep_dry_run",
            f"Would sweep {amount} µALGO to {destination}, but --sweep-dry-run is set.",
            amount=amount,
            destination=destination,
        )
        return None

    import algokit_utils

    result = algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=sender,
            receiver=destination,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
        )
    )
    txid = result.tx_ids[0]
    emit(
        "sweep",
        f"Swept {amount} µALGO to {destination} ({txid}).",
        level=logging.INFO,
        amount=amount,
        destination=destination,
        txid=txid,
    )
    return txid
