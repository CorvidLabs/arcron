# pyright: reportMissingModuleSource=false
"""A target app built to answer one question: can algod `simulate` predict
what a real Arcron `execute` will do, before an upkeep box exists?

Experimental. This exists for `scripts/spike_simulate_test_button.py` and the
console's proposed "Test" button on the registration form; nothing in the
keeper network depends on it.

Each method isolates one thing the Test button needs to get right:

* `works` — a control target with no requirements. Should pass everywhere.
* `keeper_only` — asserts `Txn.sender == Application(keeper_app).address`,
  exactly the check `docs/integrating.md` tells integrators to write. Passing
  this under simulate, with a sender nobody can sign for, is the crux of
  question 1 and 3.
* `always_reverts` — fails unconditionally. A Test button that reports this as
  passing is unconditionally dishonest.
* `needs_six` / `needs_seven` — read the ALGO balance of six and seven
  accounts named nowhere in the call, the way `resource_probe` does for one.
  Arcron's own `execute` spends 2 of the 8 available resource slots (the
  upkeep box and the target app itself; see `docs/arcron.md`), leaving 6 for
  the target. A target needing 6 fits; a target needing 7 does not — but only
  inside a *real* Arcron execution. Simulated as a standalone top-level call
  (which is the only shape reachable before a box exists), neither Arcron's
  box nor the target app costs a slot, so both may look like they fit. That
  gap is exactly the false positive this spike hunts for.
* `burns_budget` — spins until the opcode budget for this call is almost
  gone, then does one more costly op. Fails on a real chain regardless of
  path; exists to check whether `extra_opcode_budget` lets a simulated call
  claim a budget the real one will never have.
"""

from algopy import (
    ARC4Contract,
    Account,
    Application,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    op,
    urange,
)
from algopy.arc4 import abimethod


class SimProbe(ARC4Contract):
    """A target whose every method exists to be simulated and then really run."""

    def __init__(self) -> None:
        self.keeper_app = GlobalState(UInt64(0))
        self.calls = GlobalState(UInt64(0))
        self.s0 = GlobalState(Account())
        self.s1 = GlobalState(Account())
        self.s2 = GlobalState(Account())
        self.s3 = GlobalState(Account())
        self.s4 = GlobalState(Account())
        self.s5 = GlobalState(Account())
        self.s6 = GlobalState(Account())

    @abimethod()
    def configure(self, keeper_app: UInt64) -> None:
        """Name the keeper app `keeper_only` will check the sender against."""
        self.keeper_app.value = keeper_app

    @abimethod()
    def configure_subjects(
        self,
        s0: arc4.Address,
        s1: arc4.Address,
        s2: arc4.Address,
        s3: arc4.Address,
        s4: arc4.Address,
        s5: arc4.Address,
        s6: arc4.Address,
    ) -> None:
        """Name the seven accounts `needs_six`/`needs_seven` reach for."""
        self.s0.value = s0.native
        self.s1.value = s1.native
        self.s2.value = s2.native
        self.s3.value = s3.native
        self.s4.value = s4.native
        self.s5.value = s5.native
        self.s6.value = s6.native

    @abimethod()
    def works(self) -> UInt64:
        """No requirements at all. Should pass everywhere, always."""
        self.calls.value += 1
        return self.calls.value

    @abimethod()
    def keeper_only(self) -> UInt64:
        """Passes only when the caller is the keeper app's own account."""
        assert (
            Txn.sender == Application(self.keeper_app.value).address
        ), "Only the keeper app"
        self.calls.value += 1
        return self.calls.value

    @abimethod()
    def always_reverts(self) -> UInt64:
        """Fails unconditionally, on every path, every time."""
        assert False, "deliberate revert"

    @abimethod()
    def needs_six(self) -> UInt64:
        """Reads six accounts named nowhere in the call. Fits Arcron's budget."""
        total = UInt64(0)
        total += self.s0.value.balance
        total += self.s1.value.balance
        total += self.s2.value.balance
        total += self.s3.value.balance
        total += self.s4.value.balance
        total += self.s5.value.balance
        self.calls.value += 1
        return total

    @abimethod()
    def needs_seven(self) -> UInt64:
        """Reads seven accounts. Does not fit what Arcron leaves a target."""
        total = UInt64(0)
        total += self.s0.value.balance
        total += self.s1.value.balance
        total += self.s2.value.balance
        total += self.s3.value.balance
        total += self.s4.value.balance
        total += self.s5.value.balance
        total += self.s6.value.balance
        self.calls.value += 1
        return total

    @abimethod()
    def burns_budget(self) -> UInt64:
        """Does a fixed, large number of costly ops -- deliberately more than
        the ~1,250 a target gets when called through a real Arcron execution
        (`docs/integrating.md`), so it fails everywhere a real chain would run
        it. Exists to check whether a simulated call can be handed a budget no
        real execution will ever grant it (`extra_opcode_budget`).
        """
        digest = op.bzero(32)
        for i in urange(100):
            digest = op.sha256(op.itob(i) + digest)
        self.calls.value += 1
        assert digest.length == 32, "unreachable, keeps digest live"
        return self.calls.value
