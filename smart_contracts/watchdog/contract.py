# pyright: reportMissingModuleSource=false
"""A feed that notices when its own data stops arriving.

Detecting that data stopped requires someone to be watching, and that someone
cannot be the data provider: a provider that goes down has no incentive to
announce it and usually no ability to. Arcron supplies a watcher whose payment
does not depend on the provider's cooperation.

This contract only ever compares rounds. **It never looks at the value**, so it
cannot be fed a wrong price — which makes it an honest demonstration of how
Arcron composes with data systems without pretending Arcron can supply data.

Recovery policy — the issue asks for one, with reasons. **The flag clears
automatically on the next update**, and every episode is recorded:

* The flag answers a factual question — "has an update landed within the
  threshold?" — which has a correct answer at every moment. A sticky flag
  answers a different question, "did it ever go quiet", and consumers who care
  about that can read `stale_episodes` and `last_recovery_round`.
* One-way flagging needs somebody with authority to clear it. That is either
  the reporter — the party whose outage caused it, which is no safer — or an
  admin, which reintroduces exactly the operator this design exists to remove.

So the contract records; the consumer decides. A cautious consumer can refuse
to act for N rounds after `last_recovery_round` without anyone's permission.
"""

from algopy import (
    ARC4Contract,
    Account,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
)
from algopy.arc4 import abimethod

# A threshold below this cannot be distinguished from ordinary keeper lateness:
# Arcron's minimum cadence is 10 rounds, so a feed must be allowed to be at
# least a couple of sweeps late before anyone calls it stale.
MIN_THRESHOLD_ROUNDS = 30


class WentStale(arc4.Struct):
    """Emitted when a feed is first observed to have gone quiet."""

    flagged_round: arc4.UInt64
    last_update_round: arc4.UInt64
    rounds_silent: arc4.UInt64
    episode: arc4.UInt64


class Recovered(arc4.Struct):
    """Emitted when data starts arriving again."""

    recovered_round: arc4.UInt64
    silent_for: arc4.UInt64
    episode: arc4.UInt64


class Watchdog(ARC4Contract):
    """A value, the round it arrived, and whether anyone has noticed silence."""

    def __init__(self) -> None:
        self.reporter = GlobalState(Account())
        self.threshold_rounds = GlobalState(UInt64(0))
        self.value = GlobalState(UInt64(0))
        self.updated_round = GlobalState(UInt64(0))
        self.stale = GlobalState(UInt64(0))
        self.stale_since = GlobalState(UInt64(0))
        self.stale_episodes = GlobalState(UInt64(0))
        self.last_recovery_round = GlobalState(UInt64(0))
        self.checks = GlobalState(UInt64(0))

    @abimethod()
    def configure(self, reporter: arc4.Address, threshold_rounds: UInt64) -> UInt64:
        """Name the reporter and how long silence may last. Creator only, once."""
        assert Txn.sender == Global.creator_address, "Only the creator can configure"
        assert self.threshold_rounds.value == 0, "Already configured"
        assert threshold_rounds >= MIN_THRESHOLD_ROUNDS, "Threshold below minimum"

        self.reporter.value = reporter.native
        self.threshold_rounds.value = threshold_rounds
        # Start the clock, so a feed that never reports is caught too.
        self.updated_round.value = Global.round
        return threshold_rounds

    @abimethod()
    def update(self, value: UInt64) -> UInt64:
        """Report a value. Reporter only. Returns the round it landed in."""
        assert Txn.sender == self.reporter.value, "Only the reporter can update"
        assert self.threshold_rounds.value > 0, "Not configured"

        if self.stale.value == 1:
            silent_for: UInt64 = Global.round - self.stale_since.value
            self.stale.value = UInt64(0)
            self.last_recovery_round.value = Global.round
            arc4.emit(
                Recovered(
                    recovered_round=arc4.UInt64(Global.round),
                    silent_for=arc4.UInt64(silent_for),
                    episode=arc4.UInt64(self.stale_episodes.value),
                )
            )

        self.value.value = value
        self.updated_round.value = Global.round
        return Global.round

    @abimethod()
    def check_freshness(self) -> UInt64:
        """Flag the feed if it has gone quiet. Zero arguments — Arcron's shape.

        Returns the round it flagged in, or 0 for the ordinary case where the
        feed is fine or already flagged. Never fails: a failing target would
        trip keeper backoff and leave nobody watching, which is the one
        outcome this contract exists to prevent.
        """
        self.checks.value += 1
        if self.threshold_rounds.value == 0 or self.stale.value == 1:
            return UInt64(0)

        silent: UInt64 = Global.round - self.updated_round.value
        if silent <= self.threshold_rounds.value:
            return UInt64(0)

        self.stale.value = UInt64(1)
        self.stale_since.value = Global.round
        self.stale_episodes.value += 1
        arc4.emit(
            WentStale(
                flagged_round=arc4.UInt64(Global.round),
                last_update_round=arc4.UInt64(self.updated_round.value),
                rounds_silent=arc4.UInt64(silent),
                episode=arc4.UInt64(self.stale_episodes.value),
            )
        )
        return Global.round

    @abimethod(readonly=True)
    def is_stale(self) -> bool:
        """Whether the last check found the feed quiet.

        This is the flag as of the last sweep. A consumer that can read
        `rounds_since_update` should also do that arithmetic itself; the flag's
        value is that it is recorded on-chain by someone with no stake in
        hiding it, and that it emits an event a monitor can alert on.
        """
        return self.stale.value == 1

    @abimethod(readonly=True)
    def rounds_since_update(self) -> UInt64:
        return Global.round - self.updated_round.value

    @abimethod(readonly=True)
    def reading(self) -> UInt64:
        """The reported value. The watchdog never inspects it."""
        return self.value.value
