# pyright: reportMissingModuleSource=false
"""A treasury that distributes on a schedule nobody controls.

Deposits arrive from anywhere. On a cadence, Arcron calls `distribute`, which
snapshots what has accumulated and credits each recipient their fixed share.
It moves no money: recipients pull their own allocations, for the usual reason
— a scheduled call cannot reach an account it was not handed, and a push to a
closed account would fail the whole execution and stall the schedule for
everyone.

The property worth having is governance, not automation: **nobody can delay a
distribution to a convenient moment, front-run it, or quietly skip one.** The
schedule is credible because no one is running it.

Recipients and shares are fixed when the treasury is configured and cannot be
changed afterwards. A mutable recipient set needs someone with authority to
redirect the money, which is precisely the position this design exists to
eliminate. To change the split, deploy another treasury and point deposits at
it — leaving the old one's history intact and auditable.
"""

from algopy import (
    ARC4Contract,
    Box,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
)
from algopy.arc4 import abimethod

# Shares are basis points and must total exactly this.
TOTAL_SHARE_BPS = 10_000
# Bounded so `distribute` stays cheap: it walks every recipient on every call,
# and that call must never fail.
MAX_RECIPIENTS = 8
RECIPIENTS_KEY = b"recipients"


class Recipient(arc4.Struct):
    who: arc4.Address
    share_bps: arc4.UInt64
    owed: arc4.UInt64


class Distributed(arc4.Struct):
    """Emitted each time the treasury is divided up."""

    distribution: arc4.UInt64
    round_number: arc4.UInt64
    snapshot: arc4.UInt64
    allocated: arc4.UInt64


class Treasury(ARC4Contract):
    """Accumulate from anyone, allocate on a schedule, pay on request."""

    def __init__(self) -> None:
        self.balance = GlobalState(UInt64(0))
        self.allocated_total = GlobalState(UInt64(0))
        self.distributions = GlobalState(UInt64(0))
        self.configured = GlobalState(UInt64(0))

    @abimethod()
    def configure(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        recipients: arc4.DynamicArray[Recipient],
    ) -> UInt64:
        """Fix the recipients and their shares. Creator only, once, forever."""
        assert Txn.sender == Global.creator_address, "Only the creator can configure"
        assert self.configured.value == 0, "Already configured"
        count = recipients.length
        assert UInt64(0) < count <= MAX_RECIPIENTS, "Recipient count out of bounds"
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"

        total: UInt64 = UInt64(0)
        index = UInt64(0)
        while index < count:
            recipient = recipients[index].copy()
            assert recipient.share_bps.as_uint64() > 0, "A share must be positive"
            assert recipient.owed.as_uint64() == 0, "Owed must start at zero"
            total += recipient.share_bps.as_uint64()
            index += 1
        assert total == TOTAL_SHARE_BPS, "Shares must total 10,000 basis points"

        Box(arc4.DynamicArray[Recipient], key=RECIPIENTS_KEY).value = recipients.copy()
        self.configured.value = UInt64(1)
        return count

    @abimethod()
    def deposit(self, payment: gtxn.PaymentTransaction) -> UInt64:
        """Contribute, from anywhere. Returns the balance awaiting distribution."""
        assert (
            payment.receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert payment.amount > 0, "Amount must be positive"
        self.balance.value += payment.amount
        return self.balance.value

    @abimethod()
    def distribute(self) -> UInt64:
        """Divide what has accumulated. Zero arguments — Arcron's shape.

        Returns the amount allocated, or 0 when there is nothing to do. Never
        fails: a quiet period must be uneventful, because a failing hook trips
        keeper backoff and would end the schedule entirely.
        """
        if self.configured.value == 0 or self.balance.value == 0:
            return UInt64(0)

        box = Box(arc4.DynamicArray[Recipient], key=RECIPIENTS_KEY)
        recipients = box.value.copy()
        snapshot: UInt64 = self.balance.value
        allocated: UInt64 = UInt64(0)

        index = UInt64(0)
        while index < recipients.length:
            recipient = recipients[index].copy()
            # Integer division: the remainder stays in the treasury for the
            # next distribution rather than being stranded or over-allocated.
            amount: UInt64 = snapshot * recipient.share_bps.as_uint64() // TOTAL_SHARE_BPS
            recipients[index] = recipient._replace(
                owed=arc4.UInt64(recipient.owed.as_uint64() + amount)
            ).copy()
            allocated += amount
            index += 1

        assert allocated <= snapshot, "Allocated more than the snapshot"
        box.value = recipients.copy()
        self.balance.value = snapshot - allocated
        self.allocated_total.value += allocated
        self.distributions.value += 1

        arc4.emit(
            Distributed(
                distribution=arc4.UInt64(self.distributions.value),
                round_number=arc4.UInt64(Global.round),
                snapshot=arc4.UInt64(snapshot),
                allocated=arc4.UInt64(allocated),
            )
        )
        return allocated

    @abimethod()
    def claim(self) -> UInt64:
        """Pull everything allocated to you across every distribution so far."""
        box = Box(arc4.DynamicArray[Recipient], key=RECIPIENTS_KEY)
        recipients = box.value.copy()

        index = UInt64(0)
        while index < recipients.length:
            recipient = recipients[index].copy()
            if recipient.who.native == Txn.sender:
                amount: UInt64 = recipient.owed.as_uint64()
                assert amount > 0, "Nothing owed to you"
                recipients[index] = recipient._replace(owed=arc4.UInt64(0)).copy()
                box.value = recipients.copy()
                self.allocated_total.value -= amount
                itxn.Payment(receiver=Txn.sender, amount=amount).submit()
                return amount
            index += 1

        # Falling out of the loop means the sender is not on the list at all.
        assert False, "Not a recipient"  # noqa: B011

    @abimethod(readonly=True)
    def owed_to(self, who: arc4.Address) -> UInt64:
        """What `who` can claim right now."""
        recipients = Box(arc4.DynamicArray[Recipient], key=RECIPIENTS_KEY).value.copy()
        index = UInt64(0)
        while index < recipients.length:
            recipient = recipients[index].copy()
            if recipient.who.native == who.native:
                return recipient.owed.as_uint64()
            index += 1
        return UInt64(0)
