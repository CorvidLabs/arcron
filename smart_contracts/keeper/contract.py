# pyright: reportMissingModuleSource=false
from algopy import (
    ARC4Contract,
    Application,
    Asset,
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
# Minimum ALGO reward per execution (µALGO). An execution costs the keeper
# 3,000 µALGO in transaction fees — 4,000 when an ASA bonus is paid, because
# that is a third inner transaction — so at this floor a plain upkeep clears
# 1,000 and an asset upkeep exactly breaks even. That is deliberate: it makes
# the ALGO component a cost reimbursement and lets the asset be the actual
# pay, which is the point of paying in one. A creator who wants keepers who do
# not care about their token should set a fee above this floor.
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
# Maximum size of the stored argument list, in bytes — the whole ARC-4
# encoding, not one argument. The AVM's own cap on an app call's arguments is
# 2,048 bytes, so this stays conservative, and a 1,024-byte box is already a
# 409,600 µALGO deposit.
MAX_CALL_DATA = 1_024
# How many app args an execution may carry, counting the selector. Every count
# needs its own branch in `execute` and each branch is larger than the last,
# so this is what keeps the contract inside one 2,048-byte program page. Three
# covers a selector plus two ABI arguments — and any arity at all for a target
# that declares its arguments as a single struct, the trick ARC-4 itself uses
# at arg 15.
MAX_CALL_ARGS = 3
# What an app account's minimum balance rises by for each asset it can hold.
ASSET_OPT_IN_MBR = 100_000
# Catch-up policy. Zero is today's behaviour, so an upkeep that says nothing
# means what upkeeps have always meant.
CATCH_UP = 0
SKIP_AHEAD = 1
# Box minimum balance, less the argument list: 2,500 µALGO per box plus 400
# per byte of name and value. The name is 9 bytes (b"u" + itob(id)) and the
# Upkeep head is 130. Unlike a `byte[]`, a `byte[][]` carries its own length
# prefix inside the encoding, so the whole tail is `call_args.bytes`. A box
# therefore costs BOX_MBR_FIXED + 400 * len(encoded call_args) µALGO.
BOX_MBR_FIXED = 2_500 + 400 * 139


class Upkeep(arc4.Struct):
    creator: arc4.Address
    target_app: arc4.UInt64
    # Every app arg of the registered call, in order. Element 0 is whatever
    # app arg 0 should be — the ARC-4 selector for an ARC-4 target. Arcron
    # stays agnostic about what the bytes mean.
    call_args: arc4.DynamicArray[arc4.DynamicBytes]
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
    # An optional ASA bonus paid on top of the ALGO fee. Zero means ALGO only,
    # which is what every upkeep is unless its creator says otherwise — the
    # ALGO fee is never replaced, so no keeper ever needs to hold or value an
    # asset to be paid for its work.
    fee_asset: arc4.UInt64
    asset_fee: arc4.UInt64
    asset_balance: arc4.UInt64


class Keeper(ARC4Contract):
    """Permissionless keeper network.

    Anyone registers an upkeep: "call this app with this data every N rounds,
    paying R µALGO per execution", escrowing ALGO in the contract. Any keeper
    may execute a due upkeep; the contract performs the registered inner app
    call and pays the keeper from the escrow. No owner, no protocol rake.
    """

    def __init__(self) -> None:
        self.next_upkeep_id = GlobalState(UInt64(0))
        # 0 while the creator may still replace the programs, 1 once that is
        # given up for good. Global state, so anyone can read it before they
        # escrow anything: the promise is only worth what it can be checked
        # against. See `freeze`.
        self.frozen = GlobalState(UInt64(0))

    @abimethod(allow_actions=["UpdateApplication"])
    def update(self) -> None:
        """Replace the programs. Creator only, and only before `freeze`.

        This exists because being unable to fix a bug is expensive while
        nobody depends on the deployment yet. Two earlier deployments were
        abandoned rather than repaired, which stranded box minimum balance and
        made every creator cancel and re-register by hand.

        It is also a real power: while `frozen` is 0, the creator can change
        the rules after funds are escrowed, and no statement of intent removes
        that. So it is temporary by construction, readable on-chain, and given
        up before the network asks anyone to rely on it.
        """
        assert Txn.sender == Global.creator_address, "Only the creator can update"
        assert self.frozen.value == 0, "Frozen: the programs cannot be replaced"

    @abimethod()
    def freeze(self) -> None:
        """Give up the ability to update, permanently. Creator only.

        One way. Nothing sets `frozen` back to 0, and after this the only call
        that could add such a path is an update, which is now refused. From
        here the contract is exactly as immutable as one deployed with no
        update path at all, and `verify_build` proves which programs it is
        stuck with.
        """
        assert Txn.sender == Global.creator_address, "Only the creator can freeze"
        assert self.frozen.value == 0, "Already frozen"
        self.frozen.value = UInt64(1)

    @abimethod()
    def register(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        funding_payment: gtxn.PaymentTransaction,
        target_app: Application,
        call_args: arc4.DynamicArray[arc4.DynamicBytes],
        interval_rounds: UInt64,
        fee_per_execution: UInt64,
        policy: UInt64,
        fee_cap: UInt64,
        fee_asset: UInt64,
        asset_fee: UInt64,
    ) -> UInt64:
        """Register an upkeep; returns its id.

        `call_args` is every app arg of the call, in order. `policy` is
        CATCH_UP or SKIP_AHEAD. `fee_cap` is the most this upkeep will ever pay
        for one execution in ALGO; zero means the fee never escalates.
        `fee_asset` and `asset_fee` add an ASA bonus on top of the ALGO fee;
        zero means ALGO only.
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
        # A bonus of nothing is a nonsense state that pays 24 bytes of box MBR
        # for a feature it does not use; reject it rather than store it.
        assert fee_asset == 0 or asset_fee > 0, "Asset fee must be positive"
        arg_count: UInt64 = call_args.length
        # Bounded here rather than in `execute`: an argument list longer than
        # the fan-out would register happily and then fail on every execution,
        # for good, which is the same shape as the fee-cap trap.
        assert UInt64(0) < arg_count <= MAX_CALL_ARGS, "Argument count out of bounds"
        size = call_args.bytes.length
        assert size <= MAX_CALL_DATA, "Argument list too large"

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
            call_args=call_args.copy(),
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
            fee_asset=arc4.UInt64(fee_asset),
            asset_fee=arc4.UInt64(asset_fee),
            asset_balance=arc4.UInt64(0),
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
            balance=arc4.UInt64(new_balance), call_args=upkeep.call_args.copy()
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
        # The unspent bonus goes back too, which the creator can only receive
        # if they hold the asset. Checked before anything is refunded, so a
        # creator who cannot take the ASA does not lose the ALGO as well.
        bonus_asset: UInt64 = upkeep.fee_asset.as_uint64()
        # Pay out no more of the bonus than the app actually holds. The book
        # value can exceed the real holding: an ASA with a clawback address
        # can be taken back out of this account by its issuer, and a frozen
        # one cannot be sent at all. Trusting the book value would make the
        # asset transfer fail, and because it shares a transaction with the
        # ALGO refund, the refund would fail with it. The creator would lose
        # their escrow and their box minimum balance to somebody else's asset
        # settings, permanently, on a contract with no delete path.
        #
        # So the ASA is best effort and the ALGO is not.
        bonus: UInt64 = upkeep.asset_balance.as_uint64()
        if bonus > 0:
            held: UInt64 = Asset(bonus_asset).balance(Global.current_application_address)
            if held < bonus:
                bonus = held
        if bonus > 0 and not Txn.sender.is_opted_in(Asset(bonus_asset)):
            # Cancelling still returns the ALGO. Forcing an opt-in first would
            # let an asset the creator cannot hold block their own refund.
            bonus = UInt64(0)

        # The box MBR is released by the delete below, so it is refundable.
        refund: UInt64 = (
            upkeep.balance.as_uint64()
            + BOX_MBR_FIXED
            + 400 * upkeep.call_args.bytes.length
        )
        del box.value
        itxn.Payment(receiver=Txn.sender, amount=refund).submit()
        if bonus > 0:
            itxn.AssetTransfer(
                xfer_asset=Asset(bonus_asset),
                asset_receiver=Txn.sender,
                asset_amount=bonus,
            ).submit()
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

        # The ASA bonus is paid only when there is one, the escrow can cover it
        # and the keeper can receive it. A keeper that has not opted in is not
        # a failed execution — it takes the full ALGO fee and forfeits the
        # bonus, which stays in escrow for the creator. Reverting instead would
        # quietly shrink the keeper set for exactly the upkeeps paying extra.
        bonus_asset: UInt64 = upkeep.fee_asset.as_uint64()
        bonus: UInt64 = upkeep.asset_fee.as_uint64()
        asset_balance: UInt64 = upkeep.asset_balance.as_uint64()
        # As in `cancel`: the book value can exceed what the app actually
        # holds, because an ASA with a clawback address can be taken back and
        # a frozen one cannot be sent. The bonus shares a transaction with the
        # keeper's ALGO fee and with the inner call to the target, so a failed
        # asset transfer would revert the execution itself. An upkeep whose
        # bonus asset has been clawed back would stop being serviced at all,
        # and its ALGO escrow would strand.
        pays_bonus = (
            bonus_asset > 0
            and asset_balance >= bonus
            and Asset(bonus_asset).balance(Global.current_application_address) >= bonus
            and Txn.sender.is_opted_in(Asset(bonus_asset))
        )
        if pays_bonus:
            asset_balance = asset_balance - bonus

        box.value = upkeep._replace(
            next_execution_round=arc4.UInt64(next_due),
            balance=arc4.UInt64(new_balance),
            times_executed=arc4.UInt64(times),
            last_serviced_round=arc4.UInt64(Global.round),
            asset_balance=arc4.UInt64(asset_balance),
            call_args=upkeep.call_args.copy(),
        )

        # One static branch per argument count. Building the array in a loop
        # compiles but does not work: Puya models `app_args` as
        # compile-time-numbered slots and hoists the inner transaction out of
        # the loop, so only the last assignment survives. `register` bounds the
        # count, so the final branch is unreachable.
        target: UInt64 = upkeep.target_app.as_uint64()
        arg_count: UInt64 = upkeep.call_args.length
        if arg_count == 1:
            itxn.ApplicationCall(
                app_id=target,
                app_args=(upkeep.call_args[0].native,),
                on_completion=OnCompleteAction.NoOp,
            ).submit()
        elif arg_count == 2:
            itxn.ApplicationCall(
                app_id=target,
                app_args=(upkeep.call_args[0].native, upkeep.call_args[1].native,),
                on_completion=OnCompleteAction.NoOp,
            ).submit()
        elif arg_count == 3:
            itxn.ApplicationCall(
                app_id=target,
                app_args=(upkeep.call_args[0].native, upkeep.call_args[1].native, upkeep.call_args[2].native,),
                on_completion=OnCompleteAction.NoOp,
            ).submit()
        else:
            assert False, "Unsupported argument count"
        itxn.Payment(receiver=Txn.sender, amount=fee).submit()
        if pays_bonus:
            itxn.AssetTransfer(
                xfer_asset=Asset(bonus_asset),
                asset_receiver=Txn.sender,
                asset_amount=bonus,
            ).submit()
        return next_due

    @abimethod()
    def opt_in_asset(
        self, mbr_payment: gtxn.PaymentTransaction, upkeep_id: UInt64, asset: Asset
    ) -> UInt64:
        """Let the app account hold `asset`, so an upkeep can escrow a bonus.

        Permissionless, but tied to an upkeep that actually names the asset:
        an app that anyone could opt in to anything would accrete junk
        holdings for good, since there is no opt-out. The deposit is not
        refundable — reference-counting it would cost a box per asset and more
        code than the 0.1 ALGO it would ever return.
        """
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        assert (
            box.value.fee_asset.as_uint64() == asset.id
        ), "That upkeep does not use this asset"
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= ASSET_OPT_IN_MBR, "MBR payment too small"
        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()
        return UInt64(ASSET_OPT_IN_MBR)

    @abimethod()
    def top_up_asset(
        self, upkeep_id: UInt64, asset_funding: gtxn.AssetTransferTransaction
    ) -> UInt64:
        """Add ASA to an upkeep's bonus escrow; returns the new asset balance.

        Separate from `register` because an asset transfer cannot be an
        optional member of a transaction group: folding it in would make every
        ALGO-only registration carry a zero-amount transfer of an asset it
        does not use.
        """
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        upkeep = box.value.copy()
        assert (
            asset_funding.asset_receiver == Global.current_application_address
        ), "Asset funding must go to the app account"
        assert (
            asset_funding.xfer_asset.id == upkeep.fee_asset.as_uint64()
        ), "Wrong asset for this upkeep"
        new_asset_balance: UInt64 = (
            upkeep.asset_balance.as_uint64() + asset_funding.asset_amount
        )
        box.value = upkeep._replace(
            asset_balance=arc4.UInt64(new_asset_balance),
            call_args=upkeep.call_args.copy(),
        )
        return new_asset_balance
