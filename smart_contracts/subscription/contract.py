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
# The keeper's own interval ceiling. Beyond it nothing can be scheduled
# anyway, and an unbounded value overflows the cadence comparison.
MAX_ROUNDS_PER_PERIOD = 1_000_000_000
# Every Algorand account must hold this much before it can send anything, and
# an app account is no exception. `withdraw` refunds a subscriber's whole
# deposit plus their box MBR, so without a floor held back separately, the
# last subscriber to leave would drop the account below its minimum and the
# refund would revert after `withdraw` had already booked it. `set_keeper`
# collects this once, up front, and never credits it to any subscriber's
# balance.
APP_BASE_MBR = 100_000


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
        # The shortest a period may be. Enforced here rather than trusted to
        # the keeper: see `charge`.
        self.min_rounds_per_period = GlobalState(UInt64(0))
        # Settled and owed to the provider, waiting to be claimed.
        self.provider_accrued = GlobalState(UInt64(0))

    # MARK: - Setup

    @abimethod(create="require")
    def create(
        self,
        provider: arc4.Address,
        price_per_period: arc4.UInt64,
        min_rounds_per_period: arc4.UInt64,
    ) -> None:
        assert price_per_period.native > 0, "Price must be positive"
        assert min_rounds_per_period.native > 0, "A period must span some rounds"
        # An unbounded cadence overflows `last_charged_round + min_rounds` and
        # freezes billing for good; a zero provider strands everything accrued.
        # Both are self-inflicted and both are unfixable after creation.
        assert min_rounds_per_period.native <= MAX_ROUNDS_PER_PERIOD, "Cadence too long"
        assert provider.native != Global.zero_address, "Provider required"
        self.provider.value = provider.native
        self.price_per_period.value = price_per_period.native
        self.min_rounds_per_period.value = min_rounds_per_period.native
        # So the first period cannot be billed the moment the app is created.
        self.last_charged_round.value = Global.round

    @abimethod()
    def set_keeper(
        self, mbr_payment: gtxn.PaymentTransaction, keeper_app: arc4.UInt64
    ) -> None:
        """Name the keeper app whose calls advance the billing period.

        Creator only, once. Also where the app account's own base minimum
        balance is funded. `create` cannot take this payment itself: the app's
        address is not known until the create transaction that assigns it has
        already been confirmed, so nothing can pay it inside that same atomic
        group. This is the first call the creator makes afterward, and
        `subscribe` refuses to run before it, so the floor is always in place
        before any subscriber's money arrives.

        Every Algorand account needs this floor before it can send anything,
        and `withdraw` refunds a subscriber's whole deposit plus their box MBR
        by inner payment, so without it the last subscriber to leave could
        not: the payment would drop the account below its minimum and revert,
        after `withdraw` had already booked the refund. The payment is held
        aside here and never credited to any subscriber's balance, so every
        subscriber can still take back every microalgo they put in.
        """
        assert Txn.sender == Global.creator_address, "Only the creator can set the keeper"
        assert self.keeper_app.value == 0, "Keeper already set"
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.sender == Txn.sender, "MBR payment must come from the caller"
        # A rekey hands control of the sender's account to whoever the group
        # names, and a close sweeps it empty to whoever the group names.
        # Both harm only the sender, so the contract loses nothing by
        # refusing them. The exposure is a front end putting either into a
        # group a user signs without reading it closely.
        assert mbr_payment.rekey_to == Global.zero_address, "MBR payment must not rekey"
        assert (
            mbr_payment.close_remainder_to == Global.zero_address
        ), "MBR payment must not close"
        assert mbr_payment.amount >= APP_BASE_MBR, "MBR payment too small"
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
        # The sender check authenticates the messenger, not the schedule.
        # Registering an upkeep is permissionless, so anyone can point one at
        # this method on the shortest interval the keeper allows and pay for
        # it themselves. Unenforced, a provider could fast-forward billing for
        # about two minimum fees per fabricated period and settle a
        # subscriber's whole balance to itself.
        #
        # An honest schedule never trips this: the keeper calls after the
        # interval, so the assert cannot fire, and the hook keeps its
        # never-fails property for the cadence it was registered with.
        if Global.round < self.last_charged_round.value + self.min_rounds_per_period.value:
            # Too soon: no period has elapsed, so there is nothing to bill.
            #
            # This returns rather than rejecting, which is the rule this
            # repository's own integration guide gives for a hook with no work
            # to do, and which an earlier version of this check broke. Under
            # CATCH_UP a keeper draining a backlog replays immediately, so an
            # assert here would fail the whole execute, trip keeper backoff,
            # and stop billing altogether. Refusing to advance is enough: a
            # griefer still pays the fee and still moves nothing.
            return self.period.value
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
        assert self.keeper_app.value > 0, "Not configured"
        assert deposit.receiver == Global.current_application_address, "Pay this app"
        # A rekey hands control of the sender's account to whoever the group
        # names, and a close sweeps it empty to whoever the group names.
        # Both harm only the sender, so the contract loses nothing by
        # refusing them. The exposure is a front end putting either into a
        # group a user signs without reading it closely.
        assert deposit.rekey_to == Global.zero_address, "Deposit must not rekey"
        assert (
            deposit.close_remainder_to == Global.zero_address
        ), "Deposit must not close"
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

        # Take the smaller count first and multiply once. Computing
        # `periods * price` up front overflows for a large price, and the AVM
        # panics on overflow rather than saturating, which would leave the box
        # undeletable on a contract that cannot be updated.
        affordable: UInt64 = record.balance.native // self.price_per_period.value
        # Only credit periods actually paid for, so a partial payment does not
        # forgive the rest: the subscriber still owes from where they stopped.
        periods_paid: UInt64 = periods if periods <= affordable else affordable

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

        # Settle as far as the balance reaches, rather than demanding the
        # subscriber be fully caught up. Requiring that trapped anyone who ran
        # out: they could never afford the periods they owed, so they could
        # never satisfy the check, so their box could never be deleted and its
        # minimum balance was stranded for good. Lapsing is a state, not a
        # punishment, and leaving is how you stop owing more.
        periods: UInt64 = self.period.value - record.paid_through_period.native
        if periods > 0:
            affordable: UInt64 = record.balance.native // self.price_per_period.value
            periods_paid: UInt64 = periods if periods <= affordable else affordable
            settled: UInt64 = periods_paid * self.price_per_period.value
            self.provider_accrued.value += settled
            record = Subscriber(
                balance=arc4.UInt64(record.balance.native - settled),
                paid_through_period=arc4.UInt64(
                    record.paid_through_period.native + periods_paid
                ),
            )

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
