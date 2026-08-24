# pyright: reportMissingModuleSource=false
from algopy import (
    ARC4Contract,
    Application,
    Box,
    Global,
    GlobalState,
    OnCompleteAction,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
)
from algopy.arc4 import abimethod

# Minimum spacing between executions of one upkeep, in rounds.
MIN_INTERVAL_ROUNDS = 10
# Maximum spacing, in rounds — about 90 years at Algorand's block time, so it
# forbids nothing anyone wants. It exists to make the escalation multiply
# provably safe without appealing to how old the chain is: see MAX_UPKEEP_FEE.
MAX_INTERVAL_ROUNDS = 1_000_000_000
# Minimum ALGO reward per execution (µALGO). A keeper pays ~3,000 µALGO in
# transaction fees per execution, so this floor keeps executions profitable.
MIN_UPKEEP_FEE = 4_000
# Ceiling on both the base fee and the escalation cap (µALGO). Nothing needs a
# thousand ALGO per execution.
#
# It also bounds the only multiply in the contract. `execute` computes
# `(fee_cap - fee_per_execution) * excess`, where `excess` is at most
# `interval_rounds` — so with both factors capped at a billion the product is
# at most 1e18, comfortably inside a uint64's 1.8e19. Without the interval
# bound the only thing holding that product down would be `excess <=
# Global.round`, which is true but relies on the chain never reaching ~1.8e10
# rounds. On a contract that can never be patched, "no chain lives that long"
# is not the argument to rest on.
MAX_UPKEEP_FEE = 1_000_000_000
# Maximum size of the stored call data (first app arg), in bytes.
MAX_CALL_DATA = 1_024
# Catch-up policy. Zero is today's behaviour, so an upkeep that says nothing
# means what upkeeps have always meant.
CATCH_UP = 0
SKIP_AHEAD = 1
# Box minimum balance, less the call data: 2,500 µALGO per box plus 400 per
# byte of name and value. The name is 9 bytes (b"u" + itob(id)) and an encoded
# Upkeep is 108 bytes plus the call data — a 106-byte head, then a 2-byte
# length prefix on the dynamic call_data. A box therefore costs
# BOX_MBR_FIXED + 400 * len(call_data) µALGO.
BOX_MBR_FIXED = 2_500 + 400 * 117


class Upkeep(arc4.Struct):
    creator: arc4.Address
    target_app: arc4.UInt64
    call_data: arc4.DynamicBytes
    interval_rounds: arc4.UInt64
    next_execution_round: arc4.UInt64
    fee_per_execution: arc4.UInt64
    balance: arc4.UInt64
    times_executed: arc4.UInt64
    # CATCH_UP or SKIP_AHEAD: whether a missed schedule is replayed or dropped.
    policy: arc4.UInt64
    # The most this upkeep will ever pay for one execution. Zero disables
    # escalation entirely and the fee is always `fee_per_execution`.
    fee_cap: arc4.UInt64
    # The round this upkeep last ran in — not the round it was scheduled for.
    # Escalation is measured from it, and the console and notifier read it
    # instead of deriving a value that is wrong for anything catching up.
    last_serviced_round: arc4.UInt64


class Keeper(ARC4Contract):
    """Permissionless keeper network.

    Anyone registers an upkeep: "call this app with this data every N rounds,
    paying R µALGO per execution", escrowing ALGO in the contract. Any keeper
    may execute a due upkeep; the contract performs the registered inner app
    call and pays the keeper from the escrow. No owner, no protocol rake.
    """

    def __init__(self) -> None:
        self.next_upkeep_id = GlobalState(UInt64(0))

    @abimethod()
    def register(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        funding_payment: gtxn.PaymentTransaction,
        target_app: Application,
        call_data: arc4.DynamicBytes,
        interval_rounds: UInt64,
        fee_per_execution: UInt64,
        policy: UInt64,
        fee_cap: UInt64,
    ) -> UInt64:
        """Register an upkeep; returns its id.

        `policy` is CATCH_UP or SKIP_AHEAD. `fee_cap` is the most this upkeep
        will ever pay for one execution; zero means the fee never escalates.
        """
        assert interval_rounds >= MIN_INTERVAL_ROUNDS, "Interval below minimum"
        assert interval_rounds <= MAX_INTERVAL_ROUNDS, "Interval above maximum"
        assert fee_per_execution >= MIN_UPKEEP_FEE, "Fee below minimum"
        assert fee_per_execution <= MAX_UPKEEP_FEE, "Fee above maximum"
        assert policy <= SKIP_AHEAD, "Unknown catch-up policy"
        # A cap below the base fee would mean the escalation curve runs
        # backwards; reject it rather than clamping something the creator did
        # not ask for.
        assert fee_cap == 0 or fee_cap >= fee_per_execution, "Fee cap below the fee"
        assert fee_cap <= MAX_UPKEEP_FEE, "Fee cap above maximum"
        size = call_data.native.length
        assert UInt64(0) < size <= MAX_CALL_DATA, "Call data size out of bounds"

        required_mbr = BOX_MBR_FIXED + 400 * size
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= required_mbr, "MBR payment too small"
        assert (
            funding_payment.receiver == Global.current_application_address
        ), "Funding must go to the app account"
        # "One execution" means one at the price this upkeep can actually be
        # charged. An upkeep escrowed for one run at the base fee but carrying
        # a higher cap would work until the first time it fell behind and then
        # be permanently unexecutable — its fee pinned at a cap its escrow
        # cannot reach — until someone topped it up.
        required_funding: UInt64 = fee_per_execution
        if fee_cap > fee_per_execution:
            required_funding = fee_cap
        assert (
            funding_payment.amount >= required_funding
        ), "Funding must cover at least one execution"

        upkeep_id = self.next_upkeep_id.value
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        box.value = Upkeep(
            creator=arc4.Address(Txn.sender),
            target_app=arc4.UInt64(target_app.id),
            call_data=call_data.copy(),
            interval_rounds=arc4.UInt64(interval_rounds),
            next_execution_round=arc4.UInt64(Global.round + interval_rounds),
            fee_per_execution=arc4.UInt64(fee_per_execution),
            balance=arc4.UInt64(funding_payment.amount),
            times_executed=arc4.UInt64(0),
            policy=arc4.UInt64(policy),
            fee_cap=arc4.UInt64(fee_cap),
            # Never serviced, so the first execution is measured from now and
            # arrives exactly one interval later: on time, at the base fee.
            last_serviced_round=arc4.UInt64(Global.round),
        )
        self.next_upkeep_id.value = upkeep_id + 1
        return upkeep_id

    @abimethod()
    def top_up(
        self, upkeep_id: UInt64, funding_payment: gtxn.PaymentTransaction
    ) -> UInt64:
        """Add ALGO to an upkeep's escrow; returns the new balance."""
        assert (
            funding_payment.receiver == Global.current_application_address
        ), "Funding must go to the app account"
        assert funding_payment.amount > 0, "Amount must be positive"

        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        upkeep = box.value.copy()
        new_balance: UInt64 = upkeep.balance.as_uint64() + funding_payment.amount
        box.value = upkeep._replace(
            balance=arc4.UInt64(new_balance), call_data=upkeep.call_data.copy()
        )
        return new_balance

    @abimethod()
    def cancel(self, upkeep_id: UInt64) -> UInt64:
        """Cancel an upkeep (creator only); refunds escrow and box MBR.

        Deleting the box releases its minimum balance, so the creator gets
        back the remaining escrow *and* the MBR it paid at registration —
        nothing is stranded in the app account. Returns the refunded amount.
        """
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        upkeep = box.value.copy()
        assert upkeep.creator.native == Txn.sender, "Only the creator can cancel"

        # The box MBR is released by the delete below, so it is refundable.
        refund: UInt64 = (
            upkeep.balance.as_uint64()
            + BOX_MBR_FIXED
            + 400 * upkeep.call_data.native.length
        )
        del box.value
        itxn.Payment(receiver=Txn.sender, amount=refund).submit()
        return refund

    @abimethod()
    def execute(self, upkeep_id: UInt64) -> UInt64:
        """Execute a due upkeep (permissionless); pays the caller its fee.

        Returns the round the upkeep is next due.
        """
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        upkeep = box.value.copy()
        due: UInt64 = upkeep.next_execution_round.as_uint64()
        assert Global.round >= due, "Not due"

        interval: UInt64 = upkeep.interval_rounds.as_uint64()
        base: UInt64 = upkeep.fee_per_execution.as_uint64()
        cap: UInt64 = upkeep.fee_cap.as_uint64()
        fee: UInt64 = base
        # `due > last_serviced_round` means this upkeep was on schedule the
        # last time it ran, so being late now is genuine neglect. When it is
        # false the call is a replay of a backlog, and a replay never pays
        # more than base.
        #
        # Both halves are needed. Measuring lateness from the last service is
        # what stops a burst drained in one go from paying the ceiling on
        # every replay. On its own it is not enough: under CATCH_UP a replay
        # only advances the schedule by one interval, so a keeper that waits
        # two intervals between replays is late again by its own measure, and
        # collects the ceiling every time while the backlog grows without
        # bound. Measured: 34 runs took 100% of a 400,000 µALGO escrow and
        # left the upkeep 5,400 rounds further behind than it started.
        if cap > base and due > upkeep.last_serviced_round.as_uint64():
            # Escalation exists to clear a market; once a keeper has arrived
            # the market has cleared.
            lateness: UInt64 = Global.round - upkeep.last_serviced_round.as_uint64()
            excess: UInt64 = UInt64(0)
            if lateness > interval:
                excess = lateness - interval
            if excess > interval:
                excess = interval
            # Linear from base to cap over one missed interval, then flat.
            fee = base + (cap - base) * excess // interval
            # An upkeep can only bid what it holds. Without this, an escrow
            # that has fallen below the escalated fee freezes the upkeep for
            # good — lateness only grows, so the price it cannot pay only
            # rises. Dropping back to the base fee keeps it executable by
            # anyone until the escrow is genuinely empty.
            if upkeep.balance.as_uint64() < fee:
                fee = base
        assert upkeep.balance.as_uint64() >= fee, "Insufficient funding"

        next_due: UInt64 = due + interval
        if upkeep.policy.as_uint64() == SKIP_AHEAD:
            # Snap to the first slot strictly in the future, keeping the
            # schedule's phase: a daily upkeep stays on its time of day rather
            # than drifting to whenever a keeper happened to arrive.
            missed: UInt64 = (Global.round - due) // interval
            next_due = due + (missed + 1) * interval
        new_balance: UInt64 = upkeep.balance.as_uint64() - fee
        times: UInt64 = upkeep.times_executed.as_uint64() + 1
        box.value = upkeep._replace(
            next_execution_round=arc4.UInt64(next_due),
            balance=arc4.UInt64(new_balance),
            times_executed=arc4.UInt64(times),
            last_serviced_round=arc4.UInt64(Global.round),
            call_data=upkeep.call_data.copy(),
        )

        itxn.ApplicationCall(
            app_id=upkeep.target_app.as_uint64(),
            app_args=(upkeep.call_data.native,),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        itxn.Payment(receiver=Txn.sender, amount=fee).submit()
        return next_due
