# pyright: reportMissingModuleSource=false
"""A target app that deliberately reaches for resources it was not handed.

Archon's `execute` submits an inner app call with no foreign arrays, and the
docs record that as a v1 limitation without establishing what it forbids in
practice. Each method here touches exactly one kind of resource, so a failure
identifies the rule rather than a tangle of them.

Experimental. This exists to answer issue #24 and to keep the answer
reproducible; nothing in the keeper network depends on it.
"""

from algopy import (
    ARC4Contract,
    Account,
    Application,
    Asset,
    Global,
    GlobalState,
    OnCompleteAction,
    String,
    Txn,
    UInt64,
    arc4,
    itxn,
    op,
)
from algopy.arc4 import abimethod


class ResourceProbe(ARC4Contract):
    """Reaches for an account, an asset and an app that no argument names."""

    def __init__(self) -> None:
        # The resources to reach for, fixed at configuration time so that a
        # probe call carries nothing but its own selector — exactly the shape
        # Archon can send.
        self.subject = GlobalState(Account())
        self.subject_asset = GlobalState(UInt64(0))
        self.subject_app = GlobalState(UInt64(0))
        # Evidence a probe ran, for the cases where success is silent.
        self.probes_run = GlobalState(UInt64(0))
        self.last_reading = GlobalState(UInt64(0))
        # What `absorb` was handed, so a multi-arg call can be checked for
        # having delivered every argument rather than merely succeeding.
        self.last_number = GlobalState(UInt64(0))
        self.last_text = GlobalState(String(""))
        # Who the target sees as its caller, which is not who sent the
        # transaction once Archon is in the middle.
        self.last_caller = GlobalState(Account())
        # A keeper app and one of its upkeeps, for the re-entrancy probe.
        self.keeper_app = GlobalState(UInt64(0))
        self.keeper_upkeep = GlobalState(UInt64(0))

    @abimethod()
    def configure(
        self, subject: arc4.Address, asset: UInt64, app: UInt64
    ) -> None:
        """Point the probes at an account, an asset and an app."""
        self.subject.value = subject.native
        self.subject_asset.value = asset
        self.subject_app.value = app

    @abimethod()
    def opt_in_to_asset(self) -> None:
        """Hold the asset, so a transfer probe fails on availability alone."""
        itxn.AssetTransfer(
            xfer_asset=self.subject_asset.value,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()

    @abimethod()
    def probe_payment(self) -> UInt64:
        """Inner payment to an address named nowhere in the call."""
        itxn.Payment(receiver=self.subject.value, amount=0).submit()
        self.probes_run.value += 1
        return self.probes_run.value

    @abimethod()
    def probe_asset_transfer(self) -> UInt64:
        """Inner asset transfer to that same address."""
        itxn.AssetTransfer(
            xfer_asset=self.subject_asset.value,
            asset_receiver=self.subject.value,
            asset_amount=0,
        ).submit()
        self.probes_run.value += 1
        return self.probes_run.value

    @abimethod()
    def probe_read_balance(self) -> UInt64:
        """Read another account's ALGO balance."""
        self.last_reading.value = self.subject.value.balance
        self.probes_run.value += 1
        return self.last_reading.value

    @abimethod()
    def probe_read_holding(self) -> UInt64:
        """Read another account's holding of an asset."""
        asset = Asset(self.subject_asset.value)
        self.last_reading.value = asset.balance(self.subject.value)
        self.probes_run.value += 1
        return self.last_reading.value

    @abimethod()
    def report_budget(self) -> UInt64:
        """Record the opcode budget available to this call.

        Called directly it reports what any app call gets; called through an
        Archon upkeep it reports what a *target* gets, which is the number an
        integrator actually has to design against.
        """
        self.last_reading.value = Global.opcode_budget()
        self.probes_run.value += 1
        return self.last_reading.value

    @abimethod()
    def absorb(self, number: UInt64, text: arc4.String) -> UInt64:
        """A hook with arguments of its own — the shape Archon cannot call.

        Archon stores one blob and sends it as one app arg, and an ARC-4 method
        with arguments needs the selector and each argument in an app arg of
        its own. So this method is unreachable through an upkeep today.
        `scripts/spike_multiarg.py` uses it to measure what a multi-arg call
        shape would cost, and records both arguments so that a call which
        loses one is distinguishable from a call that works.
        """
        # Read the budget first, so this is comparable with `report_budget`:
        # both report what the target was handed, not what it has left.
        self.last_reading.value = Global.opcode_budget()
        self.last_number.value = number
        self.last_text.value = text.native
        self.probes_run.value += 1
        return self.last_reading.value

    @abimethod()
    def report_caller(self) -> arc4.Address:
        """Record who the target sees as its caller.

        Decides whether a target can pay the keeper itself: an Archon-executed
        call arrives as an inner transaction, and an inner transaction's sender
        is the app that submitted it. Measured rather than assumed, because a
        whole class of design depends on it.
        """
        self.last_caller.value = Txn.sender
        self.probes_run.value += 1
        return arc4.Address(Txn.sender)

    @abimethod()
    def configure_reentry(self, keeper_app: UInt64, upkeep_id: UInt64) -> None:
        """Point `reenter` at a keeper app and one of its upkeeps."""
        self.keeper_app.value = keeper_app
        self.keeper_upkeep.value = upkeep_id

    @abimethod()
    def reenter(self) -> UInt64:
        """Call the keeper's `execute` back, from inside its own execution.

        Archon writes an upkeep's state before submitting the inner call, so a
        re-entrant execution has to satisfy the schedule afresh. Whether that
        is enough to stop one — and who a nested execution pays, given the
        sender it sees is this app rather than the keeper — is measured in
        `scripts/spike_reentrancy.py` rather than argued about.

        Re-enters once and only once: unconditional recursion would just hit
        the AVM's depth limit and tell us nothing.
        """
        self.probes_run.value += 1
        if self.probes_run.value > 1:
            return self.probes_run.value
        itxn.ApplicationCall(
            app_id=Application(self.keeper_app.value),
            app_args=(
                arc4.arc4_signature("execute(uint64)uint64"),
                op.itob(self.keeper_upkeep.value),
            ),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        return self.probes_run.value

    @abimethod()
    def probe_app_call(self) -> UInt64:
        """Call a third app that no argument names."""
        itxn.ApplicationCall(
            app_id=Application(self.subject_app.value),
            app_args=(arc4.arc4_signature("tick()uint64"),),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        self.probes_run.value += 1
        return self.probes_run.value
