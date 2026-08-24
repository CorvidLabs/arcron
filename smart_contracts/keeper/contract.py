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
# Minimum ALGO reward per execution (µALGO). A keeper pays ~3,000 µALGO in
# transaction fees per execution, so this floor keeps executions profitable.
MIN_UPKEEP_FEE = 4_000
# Maximum size of the stored call data (first app arg), in bytes.
MAX_CALL_DATA = 1_024
# Box minimum balance, less the call data: 2,500 µALGO per box plus 400 per
# byte of name and value. The name is 9 bytes (b"u" + itob(id)) and an encoded
# Upkeep is 84 bytes plus the call data — an 82-byte head, then a 2-byte
# length prefix on the dynamic call_data. A box therefore costs
# BOX_MBR_FIXED + 400 * len(call_data) µALGO.
BOX_MBR_FIXED = 2_500 + 400 * 93


class Upkeep(arc4.Struct):
    creator: arc4.Address
    target_app: arc4.UInt64
    call_data: arc4.DynamicBytes
    interval_rounds: arc4.UInt64
    next_execution_round: arc4.UInt64
    fee_per_execution: arc4.UInt64
    balance: arc4.UInt64
    times_executed: arc4.UInt64


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
    ) -> UInt64:
        """Register an upkeep; returns its id."""
        assert interval_rounds >= MIN_INTERVAL_ROUNDS, "Interval below minimum"
        assert fee_per_execution >= MIN_UPKEEP_FEE, "Fee below minimum"
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
        assert (
            funding_payment.amount >= fee_per_execution
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
        new_balance: UInt64 = upkeep.balance.native + funding_payment.amount
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
            upkeep.balance.native
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
        fee: UInt64 = upkeep.fee_per_execution.native
        assert Global.round >= upkeep.next_execution_round.native, "Not due"
        assert upkeep.balance.native >= fee, "Insufficient funding"

        next_due: UInt64 = (
            upkeep.next_execution_round.native + upkeep.interval_rounds.native
        )
        new_balance: UInt64 = upkeep.balance.native - fee
        times: UInt64 = upkeep.times_executed.native + 1
        box.value = upkeep._replace(
            next_execution_round=arc4.UInt64(next_due),
            balance=arc4.UInt64(new_balance),
            times_executed=arc4.UInt64(times),
            call_data=upkeep.call_data.copy(),
        )

        itxn.ApplicationCall(
            app_id=upkeep.target_app.native,
            app_args=(upkeep.call_data.native,),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        itxn.Payment(receiver=Txn.sender, amount=fee).submit()
        return next_due
