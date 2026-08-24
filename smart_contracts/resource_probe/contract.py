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
    UInt64,
    arc4,
    itxn,
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
    def probe_app_call(self) -> UInt64:
        """Call a third app that no argument names."""
        itxn.ApplicationCall(
            app_id=Application(self.subject_app.value),
            app_args=(arc4.arc4_signature("tick()uint64"),),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        self.probes_run.value += 1
        return self.probes_run.value
