# pyright: reportMissingModuleSource=false
"""A dead man's switch: something happens because you stopped.

The owner checks in on a cadence. If they stop — for whatever reason — the
switch fires and the escrow becomes the beneficiary's. Nobody can prevent it,
because there is no operator to lean on and the firing is done by whichever
keeper happens to be watching.

This is the thing a cron job cannot do for you, since the scenario *is* that
your infrastructure went away.

Two shapes worth noting:

* `sweep` is zero-argument, permissionless, cheap when nothing has changed,
  and permanently inert once fired. Archon calls it on every cadence whether
  or not anything has happened, so the quiet path is the common path.
* `sweep` does not pay anyone. Paying the beneficiary would mean reaching an
  account a scheduled call cannot reach — an Archon inner call sees only what
  the keeper's transaction makes available. So firing *allocates*, and the
  beneficiary pulls. Same reason the rain draw resolves in a separate
  transaction; see "pull the resource" in docs/archon.md.
"""

from algopy import (
    ARC4Contract,
    Account,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
)
from algopy.arc4 import abimethod

# A check-in interval shorter than this cannot be serviced reliably: Archon's
# own minimum cadence is 10 rounds, so a switch must allow more slack than the
# keeper can possibly deliver.
MIN_INTERVAL_ROUNDS = 30


class Fired(arc4.Struct):
    """Emitted once, when the owner has gone quiet for too long."""

    fired_round: arc4.UInt64
    deadline: arc4.UInt64
    beneficiary: arc4.Address
    amount: arc4.UInt64


class DeadMan(ARC4Contract):
    """Check in, or the escrow changes hands."""

    def __init__(self) -> None:
        self.owner = GlobalState(Global.creator_address)
        self.beneficiary = GlobalState(Account())
        self.interval_rounds = GlobalState(UInt64(0))
        self.deadline = GlobalState(UInt64(0))
        self.escrow = GlobalState(UInt64(0))
        self.allocated = GlobalState(UInt64(0))
        self.fired_round = GlobalState(UInt64(0))
        self.check_ins = GlobalState(UInt64(0))

    @abimethod()
    def arm(
        self,
        deposit: gtxn.PaymentTransaction,
        beneficiary: arc4.Address,
        interval_rounds: UInt64,
    ) -> UInt64:
        """Arm the switch. Creator only, once. Returns the first deadline."""
        assert Txn.sender == self.owner.value, "Only the owner can arm it"
        assert self.interval_rounds.value == 0, "Already armed"
        assert interval_rounds >= MIN_INTERVAL_ROUNDS, "Interval below minimum"
        assert (
            deposit.receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert deposit.amount > 0, "Nothing to release"
        assert beneficiary.native != self.owner.value, "Beneficiary must not be the owner"

        self.beneficiary.value = beneficiary.native
        self.interval_rounds.value = interval_rounds
        # Arithmetic rather than assignment: a payment's amount arrives as a
        # plain int under algorand-python-testing, and += coerces it the same
        # way Puya would.
        self.escrow.value += deposit.amount
        self.deadline.value = Global.round + interval_rounds
        return self.deadline.value

    @abimethod()
    def check_in(self) -> UInt64:
        """"Still here." Owner only. Returns the new deadline."""
        assert Txn.sender == self.owner.value, "Only the owner can check in"
        assert self.interval_rounds.value > 0, "Not armed"
        assert self.fired_round.value == 0, "Already fired"

        self.deadline.value = Global.round + self.interval_rounds.value
        self.check_ins.value += 1
        return self.deadline.value

    @abimethod()
    def sweep(self) -> UInt64:
        """Fire if the owner has gone quiet. Zero arguments — Archon's shape.

        Returns the round it fired in, or 0 for the ordinary case where there
        is nothing to do. Never fails: a failing target would trip keeper
        backoff and stop the switch being watched at all, which is the one
        outcome that must not happen.
        """
        if (
            self.interval_rounds.value == 0
            or self.fired_round.value > 0
            or Global.round < self.deadline.value
        ):
            return UInt64(0)

        self.fired_round.value = Global.round
        self.allocated.value = self.escrow.value
        self.escrow.value = UInt64(0)
        arc4.emit(
            Fired(
                fired_round=arc4.UInt64(Global.round),
                deadline=arc4.UInt64(self.deadline.value),
                beneficiary=arc4.Address(self.beneficiary.value),
                amount=arc4.UInt64(self.allocated.value),
            )
        )
        return self.fired_round.value

    @abimethod()
    def claim(self) -> UInt64:
        """The beneficiary pulls what the switch released."""
        assert self.fired_round.value > 0, "Switch has not fired"
        assert Txn.sender == self.beneficiary.value, "Only the beneficiary can claim"
        amount = self.allocated.value
        assert amount > 0, "Nothing left to claim"

        self.allocated.value = UInt64(0)
        itxn.Payment(receiver=Txn.sender, amount=amount).submit()
        return amount

    @abimethod(readonly=True)
    def rounds_remaining(self) -> UInt64:
        """Rounds until the switch fires, or zero once it is due or fired."""
        if self.fired_round.value > 0 or Global.round >= self.deadline.value:
            return UInt64(0)
        return self.deadline.value - Global.round

    @abimethod(readonly=True)
    def has_fired(self) -> bool:
        return self.fired_round.value > 0
