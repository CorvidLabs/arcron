"""Recurring subscriptions, billed by a schedule instead of by a server.

A provider sets a price per period. Subscribers deposit ALGO. Arcron advances
the billing period on a cadence, and settlement moves the money.

The shape is the one `docs/integrating.md` argues for, and this contract exists
partly to show why. The obvious design bills every subscriber inside the
scheduled call: iterate the subscriber boxes, debit each, pay the provider.
That design cannot work here, for two separate reasons the guide gives:

* an Arcron inner call reaches only what the keeper's transaction made
  available, and a keeper has no way to know which subscriber boxes exist, so
  the hook cannot open them; and
* one closed or hostile account would fail the whole execution and wedge
  billing for every other subscriber.

So `charge` does the smallest thing that can be done without naming anybody:
it increments a period counter. It touches no boxes, moves no money, and has
no failure path — a hook that cannot fail is a schedule that cannot be dropped.

Everything else is arithmetic against that counter, performed in transactions
the interested party sends themselves, where their own box is available by
construction.
"""

from algopy import (  # pyright: ignore[reportMissingModuleSource]
    Account,
    Application,
    ARC4Contract,
    Box,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
)
from algopy.arc4 import abimethod  # pyright: ignore[reportMissingModuleSource]

# A subscriber box: 32-byte key prefix plus this struct.
BOX_PREFIX = b"s"
# 2,500 per box + 400 per byte: 1 prefix + 32 address = 33 name, 16 value.
SUBSCRIBER_BOX_MBR = 2_500 + 400 * (33 + 16)


class Subscriber(arc4.Struct):
    """What a subscriber has deposited, and how far they have been billed."""

    balance: arc4.UInt64
    paid_through_period: arc4.UInt64


class Subscription(ARC4Contract):
    def __init__(self) -> None:
        self.keeper_app = GlobalState(UInt64(0))
        self.provider = GlobalState(Account())
        self.price_per_period = GlobalState(UInt64(0))
        # Advanced by the scheduled call. Everything else is derived from it.
        self.period = GlobalState(UInt64(0))
        self.last_charged_round = GlobalState(UInt64(0))
        # Settled and owed to the provider, waiting to be claimed.
        self.provider_accrued = GlobalState(UInt64(0))

    # MARK: - Setup

    @abimethod(create="require")
    def create(self, provider: arc4.Address, price_per_period: arc4.UInt64) -> None:
        assert price_per_period.native > 0, "Price must be positive"
        self.provider.value = provider.native
        self.price_per_period.value = price_per_period.native

    @abimethod()
    def set_keeper(self, keeper_app: arc4.UInt64) -> None:
        """Name the keeper app whose calls advance the billing period."""
        assert Txn.sender == Global.creator_address, "Only the creator can set the keeper"
        assert self.keeper_app.value == 0, "Keeper already set"
        self.keeper_app.value = keeper_app.native

    # MARK: - The hook

    @abimethod()
    def charge(self) -> UInt64:
        """The scheduled call. One period passes.

        Deliberately the whole of it. No boxes, no payments, no iteration, and
        so no way to fail once authorization passes — which matters more than
        it sounds: a target that rejects gets skipped by the keeper for the
        rest of its run, and a target that keeps rejecting stops being
        serviced at all, quietly. Billing that silently stops is worse than
        billing that is late.
        """
        assert (
            Txn.sender == Application(self.keeper_app.value).address
        ), "Only the keeper app may advance billing"
        self.period.value += 1
        self.last_charged_round.value = Global.round
        return self.period.value

    # MARK: - Subscribers

    @abimethod()
    def subscribe(self, deposit: gtxn.PaymentTransaction) -> UInt64:
        """Open or top up a subscription, paying this contract.

        A new subscriber starts paid through the current period, so they are
        billed for periods that begin after they arrive rather than for the
        one already in progress.
        """
        assert deposit.receiver == Global.current_application_address, "Pay this app"
        assert deposit.sender == Txn.sender, "Deposit must come from the caller"

        box = Box(Subscriber, key=op.concat(BOX_PREFIX, Txn.sender.bytes))
        if box:
            existing = box.value.copy()
            box.value = Subscriber(
                balance=arc4.UInt64(existing.balance.native + deposit.amount),
                paid_through_period=existing.paid_through_period,
            )
        else:
            # The box costs the app account minimum balance, so the first
            # deposit has to cover it or the app becomes insolvent.
            assert deposit.amount > SUBSCRIBER_BOX_MBR, "First deposit must cover the box"
            box.value = Subscriber(
                balance=arc4.UInt64(deposit.amount - SUBSCRIBER_BOX_MBR),
                paid_through_period=arc4.UInt64(self.period.value),
            )
        balance: UInt64 = box.value.balance.native
        return balance

    @abimethod()
    def settle(self, subscriber: arc4.Address) -> UInt64:
        """Bill one subscriber for the periods that have passed.

        Callable by anyone, because the caller supplies the box reference and
        the arithmetic is the same whoever asks. In practice the provider runs
        it, and it is the counterpart to `charge`: the schedule decides *when*
        money is owed, this decides *how much* and by whom.

        A subscriber who cannot cover every period owed pays what they have
        and lapses, rather than blocking. Lapsing is a state, not an error.
        """
        box = Box(Subscriber, key=op.concat(BOX_PREFIX, subscriber.native.bytes))
        assert box, "No such subscriber"
        record = box.value.copy()

        periods = self.period.value - record.paid_through_period.native
        if periods == 0:
            return UInt64(0)

        owed = periods * self.price_per_period.value
        paid = owed if record.balance.native >= owed else record.balance.native
        # Only credit periods actually paid for, so a partial payment does not
        # forgive the rest: the subscriber still owes from where they stopped.
        periods_paid: UInt64 = paid // self.price_per_period.value

        box.value = Subscriber(
            balance=arc4.UInt64(record.balance.native - periods_paid * self.price_per_period.value),
            paid_through_period=arc4.UInt64(record.paid_through_period.native + periods_paid),
        )
        self.provider_accrued.value += periods_paid * self.price_per_period.value
        return periods_paid

    @abimethod()
    def withdraw(self) -> UInt64:
        """Close a subscription and take back what has not been billed.

        Settlement first: leaving without paying for periods already elapsed
        would let a subscriber outrun the schedule.
        """
        box = Box(Subscriber, key=op.concat(BOX_PREFIX, Txn.sender.bytes))
        assert box, "Not subscribed"
        record = box.value.copy()
        assert record.paid_through_period.native == self.period.value, "Settle first"

        refund: UInt64 = record.balance.native + SUBSCRIBER_BOX_MBR
        del box.value
        itxn.Payment(receiver=Txn.sender, amount=refund, fee=0).submit()
        return refund

    # MARK: - Provider

    @abimethod()
    def claim(self) -> UInt64:
        """The provider collects what settlement has credited.

        The provider sends this, so the provider is an available resource by
        construction. That is the whole reason the money waits here instead of
        being pushed from the scheduled call.
        """
        assert Txn.sender == self.provider.value, "Only the provider may claim"
        amount = self.provider_accrued.value
        assert amount > 0, "Nothing accrued"
        self.provider_accrued.value = UInt64(0)
        itxn.Payment(receiver=self.provider.value, amount=amount, fee=0).submit()
        return amount
