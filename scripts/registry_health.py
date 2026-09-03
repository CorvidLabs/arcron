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
  why is an overdue upkeep overdue      simulate the execution and read the error
  can the app pay out what it escrows   spendable, against the sum of the boxes

The fourth was added after upkeep 87 sat overdue for 9,000 rounds with 5.75
ALGO in it and its fee escalated to the ceiling, while twelve others were
overdue because they had run out of money. This report printed all thirteen
identically, as OVERDUE, and telling them apart took six commands and a
disassembler. They are not the same problem: one is a funding problem and the
other is a broken target, which no amount of funding fixes.

The fifth came out of the 2026-09-01 audit. The contract charges every box
its exact minimum balance and every asset opt-in its exact deposit, and
charges nobody for the 0.1 ALGO the app account itself needs to exist.
`deploy_config` sends that; the MainNet path, `govern create`, only says to.
On a keeper where nobody did, a box read 120,000 with 57,900 spendable behind
it, and the ledger refused the fifteenth execution and then the creator's
`cancel` until a stranger paid the 0.1 ALGO in. Nothing in the contract can
notice that, so this report has to.

Reads public state. Holds no account and signs nothing: the simulation sends
unsigned transactions with `allow_empty_signatures`, from a keeper address
already observed executing, so it asks what a real keeper would find.

Run:  poetry run python -m scripts.registry_health [--network N] --app-id N
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, replace

from algosdk import transaction
from algosdk.logic import get_application_address
from algosdk.v2client.models import SimulateRequest, SimulateRequestTransactionGroup

from scripts import network as net
from scripts.keeper_backoff import is_target_refusal
from scripts.keeper_bot import (
    ACCOUNT_MBR_MICROALGO,
    BONUS_FEE_MICROALGO,
    EXECUTION_COST_MICROALGO,
    effective_fee,
    scan_upkeeps,
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
    #: Whether the escrow covers what `execute` would pay right now, which is
    #: the escalated fee rather than the base one. Decides the funding case,
    #: because simulate does not return the assert message that would.
    can_pay_fee: bool = True
    #: Why an execution would fail right now, from `classify_failure`. Empty
    #: when nothing was simulated, which is not the same as "would succeed":
    #: only overdue upkeeps are worth the round trip.
    blocked: str = ""
    #: What the box says it holds, which is what `cancel` would try to refund.
    escrow: int = 0
    #: The target executed cleanly and said it did nothing. See
    #: `reports_no_work`. False also means "not asked", which is not the same
    #: as "did something".
    idle_target: bool = False

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
    def idle(self) -> bool:
        """Paying a keeper on schedule to call a target with nothing to do.

        The gap this closes is that such an upkeep looks perfect from every
        other angle: the target simulates clean, so it is not `TARGET
        REVERTS`; the escrow pays, so it is not the funding case; and the
        keeper is paid, so it is not `PAYS THE KEEPER NOTHING`. Four upkeeps
        reached this state before anything reported it (73, 79, 91 and 113),
        and rain 2 declined sixty-three consecutive scheduled draws while
        every reading here was green.
        """
        return self.idle_target

    @property
    def due(self) -> bool:
        """Executable right now, which is what makes it worth simulating."""
        return self.rounds_late > 0

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
        if self.idle:
            found.append("TARGET REPORTS NO WORK")
        if self.blocked:
            found.append(self.blocked)
        return found


def read_upkeeps(algod, app_id: int, seconds_per_round: float, current_round: int) -> list[UpkeepHealth]:
    """Every upkeep in the registry, in the terms the questions above ask in.

    The box list is paginated, and this used to take one `limit=1_000` page and
    call it the registry. At the 33 upkeeps live on TestNet that is the whole
    registry, so nothing was wrong yet; past a thousand boxes it would have
    dropped the rest without saying so, and the sum below is what
    `RegistrySolvency` weighs the app account against. A short read there can
    only make the registry look like it owes *less* than it does, which is the
    same unsafe direction as guessing the ledger's floor low: the check added
    to catch a registry that cannot pay would have been the first thing to lie.

    So the paging is `keeper_bot.scan_upkeeps`, which the bot itself runs on
    every scan, rather than a second copy of the loop here. Two copies of it is
    how one of them came to be missing the `next-token` in the first place.
    """
    found: list[UpkeepHealth] = []
    for upkeep in scan_upkeeps(algod, app_id):
        cost = EXECUTION_COST_MICROALGO + (BONUS_FEE_MICROALGO if upkeep.fee_asset else 0)
        runs = upkeep.balance // upkeep.fee_per_execution if upkeep.fee_per_execution else 0
        found.append(
            UpkeepHealth(
                upkeep_id=upkeep.upkeep_id,
                target_app=upkeep.target_app,
                times_executed=upkeep.times_executed,
                net_to_keeper=upkeep.fee_per_execution - cost,
                runway_days=runs * upkeep.interval_rounds * seconds_per_round / 86_400,
                rounds_late=max(0, current_round - upkeep.next_execution_round),
                interval_rounds=upkeep.interval_rounds,
                can_pay_fee=upkeep.balance >= effective_fee(upkeep, current_round),
                escrow=upkeep.balance,
            )
        )
    return sorted(found, key=lambda u: u.upkeep_id)


@dataclass(frozen=True)
class RegistrySolvency:
    """Whether the app account can pay out every µALGO its boxes promise.

    The boxes are the contract's book; the ledger keeps its own. They agree
    only if the account holds its base minimum balance on top of what the
    boxes and opt-ins reserve, and the contract never collects that base, so
    the two can disagree by up to 0.1 ALGO for as long as nobody sends it.
    While they disagree the last executions and the last `cancel` fail at the
    ledger with the box still saying they are payable.
    """

    amount: int
    min_balance: int
    escrowed: int

    @property
    def spendable(self) -> int:
        return self.amount - self.min_balance

    @property
    def shortfall(self) -> int:
        """How much escrow the ledger would refuse to pay out. Zero is the norm."""
        return max(0, self.escrowed - self.spendable)

    def flags(self) -> list[str]:
        if self.shortfall:
            return [f"THE APP CANNOT PAY OUT {self.shortfall:,} uALGO IT HOLDS IN ESCROW"]
        return []


def read_escrowed(algod, app_id: int) -> int:
    """Sum what the boxes say the app owes, without the rest of the report.

    `govern status` needs the number and nothing else around it, and reading
    the boxes twice to get it would be the more expensive way to be wrong
    later.

    The seconds-per-round and current-round arguments below are placeholders:
    they only feed `runway_days` and `rounds_late`, which nothing here reads.
    Passing the real ones would mean `govern` knowing the network's block time
    to compute a number it throws away.
    """
    return sum(u.escrow for u in read_upkeeps(algod, app_id, 1.0, 0))


def read_solvency(algod, app_id: int, escrowed: int) -> RegistrySolvency:
    """The ledger's side of the book, read rather than assumed.

    A node that does not report `min-balance` gets an error here instead of the
    100,000 floor every other reader falls back to. The floor is a *lower*
    bound: an app account's real minimum rises by the exact MBR of every box it
    holds and every asset it has opted into, which on the registry is most of
    it. `spendable` is `amount - min_balance`, so substituting the floor
    inflates the spendable side, shrinks the shortfall, and reports an app that
    cannot pay out its escrow as solvent. That is precisely the failure this
    function exists to catch, so it must never be the failure it produces. A
    solvency check that cannot see the ledger's floor has no answer to give,
    and saying so is the only honest thing it can do.
    """
    info = algod.account_info(get_application_address(app_id))
    min_balance = info.get("min-balance")
    if min_balance is None:
        raise ValueError(
            f"the node reported no min-balance for the account of app {app_id}, so how "
            f"much of its balance is spendable cannot be known. Refusing to assume the "
            f"{ACCOUNT_MBR_MICROALGO:,} uALGO floor: that is the smallest a minimum "
            f"balance can be, and assuming it would report an app that cannot pay out "
            f"its escrow as solvent."
        )
    return RegistrySolvency(
        amount=int(info["amount"]),
        min_balance=int(min_balance),
        escrowed=escrowed,
    )


def classify_failure(message: str, can_pay_fee: bool = True) -> str:
    """A simulate failure, as the one thing an operator has to decide.

    **The assert message is not in the response.** `assert x, "Insufficient
    funding"` puts that string in the source map, not on chain, so simulate
    returns `assert failed pc=1181 ... opcodes=dig 15; >=; assert` and nothing
    more. Matching on the text would have quietly classified nothing, and
    matching on the pc would break on the next build. So the funding case is
    decided from the upkeep's own numbers, which are already known, and only
    the shape of the failure is read from the message.

    What an operator actually has to tell apart:

      inner tx failed    the target's own code rejected the call. Upkeep 87:
                         a target its author reconfigured to revert. No fee
                         fixes this; the upkeep wants cancelling or the target
                         wants fixing.
      cannot pay         the escrow cannot cover the escalated fee. This is
                         the funding case, and `keeper_topup` is the answer.

    Anything else is quoted rather than guessed at: a classifier that invents
    a category for an error it has not seen is worse than one that quotes it.

    "Did the target refuse?" is asked in three places — here, in
    `keeper_backoff.is_lost_race`, and in the schedule `record_failure` picks —
    and it used to be spelled three ways, one of which additionally required
    the word "failed". They are one function now (`is_target_refusal`), because
    a report that says TARGET REVERTS while the bot treats the same message as
    a keeper-side problem is worse than either answer on its own.
    """
    if not message:
        return ""
    if is_target_refusal(message):
        return "TARGET REVERTS"
    if not can_pay_fee:
        return "ESCROW CANNOT PAY THE FEE"
    # Drop the `transaction <52 chars>: ` prefix before truncating. Leaving it
    # in spends the whole line on an id that identifies a simulated
    # transaction which never existed.
    quoted = re.sub(r"^transaction [A-Z2-7]{52}: ", "", message)
    return f"WOULD FAIL: {quoted.split('. Details')[0][:80]}"


#: An ARC-4 return value is logged by the callee, prefixed with this.
ARC4_RETURN_PREFIX = b"\x15\x1f\x7c\x75"


def reports_no_work(group: dict) -> bool:
    """Did the target execute cleanly and say it did nothing?

    Read narrowly. This is a **convention**, not a guarantee: a target that
    returns a count of what it did, and returns zero, is telling us it did
    nothing. `rain`'s `draw()` returns how many rains it fired, and a hub with
    empty pots returns 0 on every scheduled call, forever, while looking
    healthy from every other angle.

    So this answers "the target says it did nothing", not "the target did
    nothing". A target returning void, a string, or a meaningful zero is not
    flagged, and a target that changes state while returning zero is missed.
    That is the honest bound, and it is worth having anyway: the alternative
    on the four upkeeps this would have caught was nothing at all.

    Only the target's own call is read, not the keeper's fee payment, and only
    an eight-byte unsigned return: anything else is left alone rather than
    guessed at.
    """
    for txn in group.get("txn-results", []):
        for inner in txn.get("txn-result", {}).get("inner-txns", []):
            if "application-transaction" not in inner.get("txn", {}).get("txn", {}):
                if not inner.get("application-index") and "logs" not in inner:
                    continue
            for entry in inner.get("logs", []):
                raw = base64.b64decode(entry)
                if not raw.startswith(ARC4_RETURN_PREFIX):
                    continue
                value = raw[len(ARC4_RETURN_PREFIX):]
                if len(value) != 8:
                    continue
                return int.from_bytes(value, "big") == 0
    return False


def simulate_execute(algod, app_id: int, upkeep: UpkeepHealth, sender: str) -> tuple[str, bool]:
    """What `execute` would do right now, without doing it.

    Unsigned, with `allow_empty_signatures`, so this needs no key and cannot
    send anything. The sender is a real keeper address so the contract's own
    sender checks see what they would really see.
    """
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = EXECUTION_COST_MICROALGO
    txn = transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=app_id,
        app_args=[execute_selector(), upkeep.upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep.upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[upkeep.target_app],
    )
    request = SimulateRequest(
        txn_groups=[SimulateRequestTransactionGroup(
            txns=[transaction.SignedTransaction(txn, "")]
        )],
        allow_empty_signatures=True,
        allow_unnamed_resources=True,
    )
    try:
        response = algod.simulate_transactions(request)
    except Exception as error:  # a simulate that cannot run is not a verdict
        return f"WOULD FAIL: simulate unavailable ({type(error).__name__})", False
    group = response["txn-groups"][0]
    verdict = classify_failure(
        group.get("failure-message", "").replace("\n", " "),
        can_pay_fee=upkeep.can_pay_fee,
    )
    # Only a clean run can say the target did nothing. A failed one has not
    # reached the target at all, or has, and refused.
    return verdict, (not verdict and reports_no_work(group))


def diagnose_overdue(algod, app_id: int, upkeeps: list[UpkeepHealth], sender: str) -> list[UpkeepHealth]:
    """Annotate every due upkeep with what a run of it would do.

    This used to simulate only the overdue ones, on the reasoning that an
    upkeep on schedule is being executed, which is a stronger statement than
    any simulation could make. That reasoning is wrong, and the registry
    proved it: being executed on schedule says nothing about whether the
    execution *does* anything. Upkeeps 73, 79, 91 and 113 were all serviced
    punctually while their targets had nothing to do, and every reading on
    this page was green for all four.

    So the simulate now runs for anything actually due. `blocked` is still
    only set for the overdue, because a due-and-serviced upkeep failing a
    simulate is usually a race being lost rather than a fault, but the idle
    reading is taken wherever it can be: a not-yet-due upkeep cannot be
    simulated at all, since `execute` asserts the schedule before it ever
    reaches the target.
    """
    out = []
    for upkeep in upkeeps:
        if not upkeep.due:
            out.append(upkeep)
            continue
        verdict, idle = simulate_execute(algod, app_id, upkeep, sender)
        out.append(replace(
            upkeep,
            blocked=verdict if upkeep.overdue else "",
            idle_target=idle,
        ))
    return out


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
        # The floor fallback stays here, unlike in `read_solvency`. This row is
        # a courtesy about somebody else's wallet: the execution count beside
        # it is measured, and the worst a floor guessed low can do is leave off
        # the RUNNING OUT note next to a keeper we do not run and cannot fund.
        # Solvency is a verdict on whether this registry can pay out what its
        # boxes promise, and a wrong verdict there is the whole reason that
        # check was written. Raising here would also lose every keeper row
        # because one third-party account's response was short a field, which
        # is a worse report than an optimistic estimate labelled with a `~`.
        spendable = int(info["amount"]) - int(info.get("min-balance", ACCOUNT_MBR_MICROALGO))
        rows.append((address, count, spendable, spendable // EXECUTION_COST_MICROALGO))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--no-simulate", action="store_true",
                        help="skip asking the chain why an overdue upkeep is overdue")
    args = parser.parse_args(argv)

    net.load_network(args.network)
    algorand = net.connect(args.network)
    algod, indexer = algorand.client.algod, algorand.client.indexer
    spr = net.seconds_per_round(args.network)
    current = int(algod.status()["last-round"])

    upkeeps = read_upkeeps(algod, args.app_id, spr, current)
    keepers = read_keepers(algod, indexer, args.app_id, current)
    solvency = read_solvency(algod, args.app_id, sum(u.escrow for u in upkeeps))

    # Ask from the busiest keeper's address: it is the account most likely to
    # be executing, so its view is the one that matters. With no keeper to
    # borrow, there is nobody to ask on behalf of, and the report says less
    # rather than guessing.
    if not args.no_simulate and keepers and any(u.overdue for u in upkeeps):
        upkeeps = diagnose_overdue(algod, args.app_id, upkeeps, keepers[0][0])

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
    line = (f"  app account  {solvency.spendable / 1e6:>9.3f} ALGO spendable  "
            f"{solvency.escrowed / 1e6:>9.3f} ALGO escrowed")
    if solvency.flags():
        problems += 1
        logger.warning(f"{line}   {' / '.join(solvency.flags())}")
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
