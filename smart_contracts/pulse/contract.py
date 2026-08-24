# pyright: reportMissingModuleSource=false
from algopy import ARC4Contract, Global, GlobalState, String, UInt64, arc4
from algopy.arc4 import abimethod


class Pulse(ARC4Contract):
    """Demo upkeep target: a public heartbeat counter.

    Designed to be driven by the Keeper contract. `tick` takes no arguments
    beyond its method selector, which is the only shape Arcron could call
    before #8; `tick_with` takes real arguments, which is the shape it can
    call now. Permissionless by design — it is a demo, not a gate.
    """

    def __init__(self) -> None:
        self.beats = GlobalState(UInt64(0))
        self.last_beat_round = GlobalState(UInt64(0))
        self.last_note = GlobalState(String(""))

    @abimethod()
    def tick(self) -> UInt64:
        self.beats.value += 1
        self.last_beat_round.value = Global.round
        return self.beats.value

    @abimethod()
    def tick_with(self, beats: UInt64, note: arc4.String) -> UInt64:
        """A hook with arguments of its own; returns the new count.

        Unreachable through an upkeep before #8, because an ARC-4 method needs
        its selector and each argument in an app arg of its own and Arcron
        could only send one.
        """
        self.beats.value += beats
        self.last_beat_round.value = Global.round
        self.last_note.value = note.native
        return self.beats.value
