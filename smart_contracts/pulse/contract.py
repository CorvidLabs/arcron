# pyright: reportMissingModuleSource=false
from algopy import ARC4Contract, Global, GlobalState, String, UInt64, arc4
from algopy.arc4 import abimethod

# The most one call may add. Reaching the uint64 ceiling at this rate needs
# more calls than the chain will ever carry, so `tick` cannot be made to
# overflow by anyone willing to pay for it.
MAX_BEATS_PER_TICK = 1_000_000


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
        # Bound the increment. Unbounded, one call could set the counter near
        # the uint64 ceiling, after which every `tick` overflows and panics.
        # The AVM panics rather than saturating, so the panic fails the inner
        # call, which fails the whole execution, which means a keeper can never
        # service this app again. That is a permanent wedge of the demo target
        # for the price of one transaction, and on a contract with no update
        # path there is no way back.
        assert beats <= MAX_BEATS_PER_TICK, "Too many beats for one tick"
        self.beats.value += beats
        self.last_beat_round.value = Global.round
        self.last_note.value = note.native
        return self.beats.value
