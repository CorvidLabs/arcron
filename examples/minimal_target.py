"""The smallest contract Archon can drive. Copy this and start editing.

Everything an integration needs and nothing it does not:

* one NoOp method taking no arguments of its own, so a keeper can call it with
  just its selector;
* authorization to the keeper app, so nobody else can drive your schedule;
* a no-op path that returns rather than fails, because the hook is called on
  every cadence whether or not there is work to do.

The full reasoning is in docs/integrating.md.
"""

from algopy import (  # pyright: ignore[reportMissingModuleSource]
    ARC4Contract,
    Application,
    Global,
    GlobalState,
    Txn,
    UInt64,
)
from algopy.arc4 import abimethod  # pyright: ignore[reportMissingModuleSource]


class MinimalTarget(ARC4Contract):
    """Does a small amount of work, on a schedule, for whoever pays."""

    def __init__(self) -> None:
        # The keeper app allowed to drive this contract. Set once, at creation.
        self.keeper_app = GlobalState(UInt64(0))
        self.work_done = GlobalState(UInt64(0))
        self.last_run_round = GlobalState(UInt64(0))
        self.pending = GlobalState(UInt64(0))

    @abimethod()
    def set_keeper(self, keeper_app: UInt64) -> None:
        """Name the keeper app whose calls this contract will accept."""
        assert Txn.sender == Global.creator_address, "Only the creator can set the keeper"
        assert self.keeper_app.value == 0, "Keeper already set"
        self.keeper_app.value = keeper_app

    @abimethod()
    def request_work(self) -> UInt64:
        """Something for the scheduled call to find. Stands in for real state."""
        self.pending.value += 1
        return self.pending.value

    @abimethod()
    def run(self) -> UInt64:
        """The hook. Zero arguments, so Archon can call it.

        Returns what it did, which is often nothing — and nothing is fine.
        """
        # Only the keeper app may drive the schedule. Archon's inner call comes
        # from the keeper application's account, so this is the check to make.
        # Leave it out to be permissionless like the Pulse demo; see the guide
        # for when that is the right call.
        assert (
            Txn.sender == Application(self.keeper_app.value).address
        ), "Only the keeper app may run this"

        # The no-op path: cheap, and a return rather than an assert. A hook
        # that *fails* trips keeper backoff and stops being serviced at all.
        if self.pending.value == 0:
            return UInt64(0)

        done: UInt64 = self.pending.value
        self.pending.value = UInt64(0)
        self.work_done.value += done
        self.last_run_round.value = Global.round
        return done
