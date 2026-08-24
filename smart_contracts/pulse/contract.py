# pyright: reportMissingModuleSource=false
from algopy import ARC4Contract, Global, GlobalState, UInt64
from algopy.arc4 import abimethod


class Pulse(ARC4Contract):
    """Demo upkeep target: a public heartbeat counter.

    Designed to be driven by the Keeper contract: `tick` takes no arguments
    beyond its method selector, so a registered upkeep can call it on a
    schedule. Permissionless by design — it is a demo, not a gate.
    """

    def __init__(self) -> None:
        self.beats = GlobalState(UInt64(0))
        self.last_beat_round = GlobalState(UInt64(0))

    @abimethod()
    def tick(self) -> UInt64:
        self.beats.value += 1
        self.last_beat_round.value = Global.round
        return self.beats.value
