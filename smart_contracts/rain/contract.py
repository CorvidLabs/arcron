# pyright: reportMissingModuleSource=false
"""A pot that pays a random ticket holder on a schedule, run by nobody.

The scheduled call does **accounting only**. `draw` locks a prize, snapshots
the ticket count and fixes a future beacon round; it moves no money, calls no
other app, and touches nothing it cannot reach. That is what makes it callable
by a bare Arcron upkeep.

Everything that needs a resource happens in a transaction somebody sends for
themselves:

* `resolve` inner-calls the randomness beacon, so its caller attaches the
  beacon reference — a scheduled call could not, because an Arcron inner call
  reaches only what the keeper's transaction makes available (measured in
  `docs/arcron.md`).
* `claim` pays the winner, who is the sender, and is therefore always
  available.

Pull, not push, for both money and resources. A push payout to a closed
account would fail the whole execution and stall the schedule for everyone;
so would a beacon that happened to be unreachable.
"""

from algopy import (
    ARC4Contract,
    Account,
    Application,
    Asset,
    Box,
    Bytes,
    Global,
    GlobalState,
    OnCompleteAction,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
    subroutine,
)
from algopy.arc4 import abimethod

# Rounds between opening a draw and the beacon value being readable. The
# beacon answers for a past round only, so the winner cannot be known — by
# anyone, including whoever opened the draw — at the moment it opens.
BEACON_DELAY = 8
# One box per ticket: b"t" + itob(index) -> the holder's 32-byte address.
TICKET_PREFIX = b"t"
# One box per unclaimed prize: b"a" + address -> uint64 µALGO.
ALLOCATION_PREFIX = b"a"
# 2,500 per box + 400 per byte of name and value.
# Ticket: 9-byte name, 32-byte value.
TICKET_MBR = 2_500 + 400 * 41
# Allocation: 33-byte name, 8-byte value.
ALLOCATION_MBR = 2_500 + 400 * 41
# What holding one asset costs an account, permanently.
ASSET_OPT_IN_MBR = 100_000


class Drawn(arc4.Struct):
    """Emitted when a draw opens — before anyone can know the outcome."""

    draw_id: arc4.UInt64
    commit_round: arc4.UInt64
    prize: arc4.UInt64
    tickets: arc4.UInt64


class Resolved(arc4.Struct):
    """Emitted when the beacon has spoken."""

    draw_id: arc4.UInt64
    winner: arc4.Address
    prize: arc4.UInt64
    winning_ticket: arc4.UInt64


class Rain(ARC4Contract):
    """Tickets in, a scheduled draw, a pulled prize."""

    def __init__(self) -> None:
        self.beacon_app = GlobalState(UInt64(0))
        self.pot = GlobalState(UInt64(0))
        self.tickets = GlobalState(UInt64(0))
        self.draw_id = GlobalState(UInt64(0))
        self.draw_open = GlobalState(UInt64(0))
        self.commit_round = GlobalState(UInt64(0))
        self.prize = GlobalState(UInt64(0))
        self.tickets_snapshot = GlobalState(UInt64(0))
        self.draws_resolved = GlobalState(UInt64(0))
        self.last_winner = GlobalState(Account())
        # Zero address: anyone may enter. Set: only holders of an asset this
        # account created, which is how an NFT collection gates a draw. A
        # collection on Algorand is many assets rather than one, so the check
        # has to be on who minted them.
        self.gate_creator = GlobalState(Account())
        # Zero: the pot and the prize are ALGO. Set: both are this asset.
        self.prize_asset = GlobalState(UInt64(0))

    @abimethod()
    def configure(
        self, beacon_app: UInt64, gate_creator: arc4.Address, prize_asset: UInt64
    ) -> None:
        """Point at the beacon, and decide who may enter and what they win.

        Creator only, once. The beacon differs per network, and LocalNet has
        none, so tests point this at a stub implementing the same interface.

        `gate_creator` zero leaves entry open to anyone. Set to a collection's
        minting account, only holders of something it created may enter.

        `prize_asset` zero keeps the pot and the prize in ALGO. Set, both are
        that asset, and the app must opt in before it can be funded.
        """
        assert Txn.sender == Global.creator_address, "Only the creator can configure"
        assert self.beacon_app.value == 0, "Already configured"
        assert beacon_app > 0, "Beacon app id required"
        self.beacon_app.value = beacon_app
        self.gate_creator.value = gate_creator.native
        self.prize_asset.value = prize_asset

    @abimethod()
    def opt_in_prize_asset(self, mbr_payment: gtxn.PaymentTransaction) -> UInt64:
        """Let the app hold the prize asset. Anyone may pay for it, once.

        An account must opt in before it can receive an asset, and holding one
        costs 100,000 microAlgos of minimum balance permanently. That is not
        the app's to find, so whoever wants the draw running provides it.
        """
        asset = self.prize_asset.value
        assert asset > 0, "Prize is ALGO"
        assert not Global.current_application_address.is_opted_in(
            Asset(asset)
        ), "Already opted in"
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= ASSET_OPT_IN_MBR, "MBR payment too small"
        itxn.AssetTransfer(
            xfer_asset=Asset(asset),
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()
        return asset

    @abimethod()
    def enter(self, mbr_payment: gtxn.PaymentTransaction, gate_asset: Asset) -> UInt64:
        """Buy one ticket for the sender. Returns its index.

        Tickets persist across draws: buying once enters every future draw.
        Buying twice doubles the holder's odds and costs them two MBRs, which
        is the honest version of "one entry per person" on a chain where
        identity is free.
        """
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= TICKET_MBR, "MBR payment too small"

        # The entrant supplies the asset they are claiming membership with,
        # and sends this transaction, so the reference is available. A
        # scheduled call could not do this, which is why the gate lives here
        # and not in `draw`.
        gate = self.gate_creator.value
        if gate != Global.zero_address:
            assert Txn.sender.is_opted_in(gate_asset), "Hold a token from the collection"
            assert gate_asset.balance(Txn.sender) > 0, "Hold a token from the collection"
            assert gate_asset.creator == gate, "That asset is not from the collection"

        index = self.tickets.value
        Box(Account, key=op.concat(TICKET_PREFIX, op.itob(index))).value = Txn.sender
        self.tickets.value = index + 1
        return index

    @abimethod()
    def deposit(self, payment: gtxn.PaymentTransaction) -> UInt64:
        """Add ALGO to the pot. Anyone, any amount. Returns the new pot."""
        assert self.prize_asset.value == 0, "This draw pays an asset; use deposit_asset"
        assert (
            payment.receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert payment.amount > 0, "Amount must be positive"
        self.pot.value += payment.amount
        return self.pot.value

    @abimethod()
    def deposit_asset(self, transfer: gtxn.AssetTransferTransaction) -> UInt64:
        """Add the prize asset to the pot. Anyone, any amount.

        Refilling is deliberately open. A draw that only its creator can fund
        stops the day they lose interest, and the whole point is a schedule
        that does not depend on anyone in particular.
        """
        asset = self.prize_asset.value
        assert asset > 0, "This draw pays ALGO; use deposit"
        assert (
            transfer.asset_receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert transfer.xfer_asset.id == asset, "Wrong asset"
        assert transfer.asset_amount > 0, "Amount must be positive"
        self.pot.value += transfer.asset_amount
        return self.pot.value

    @abimethod()
    def draw(self) -> UInt64:
        """Open a draw. Zero arguments — this is what Arcron calls.

        A no-op returning 0 when there is nothing to draw for, because a
        scheduled call that fails would trip keeper backoff and stop the whole
        demo. Being called on a quiet week must be uneventful, not an error.
        """
        # An asset pot is counted in token units, so the ALGO the allocation
        # box costs cannot be taken out of it. Only an ALGO pot can pay for
        # its own bookkeeping.
        reserve: UInt64 = (
            UInt64(ALLOCATION_MBR) if self.prize_asset.value == 0 else UInt64(0)
        )
        if (
            self.draw_open.value == 1
            or self.tickets.value == 0
            or self.pot.value <= reserve
        ):
            return UInt64(0)

        # Reserve the winner's allocation box, so resolving can never fail for
        # want of minimum balance. For an ALGO pot that comes out of the pot
        # and returns to it when the prize is claimed. For an asset pot it
        # comes from the app account, which is also where it goes back to.
        prize: UInt64 = self.pot.value - reserve
        self.pot.value = UInt64(0)
        self.prize.value = prize
        self.tickets_snapshot.value = self.tickets.value
        self.commit_round.value = Global.round + BEACON_DELAY
        self.draw_id.value += 1
        self.draw_open.value = UInt64(1)

        arc4.emit(
            Drawn(
                draw_id=arc4.UInt64(self.draw_id.value),
                commit_round=arc4.UInt64(self.commit_round.value),
                prize=arc4.UInt64(prize),
                tickets=arc4.UInt64(self.tickets_snapshot.value),
            )
        )
        return self.draw_id.value

    @abimethod()
    def resolve(self) -> arc4.Address:
        """Ask the beacon who won. Permissionless.

        Sent by a participant rather than a keeper, because reading the beacon
        means an inner call to it and only the sender of the outer transaction
        can make that app available.
        """
        assert self.draw_open.value == 1, "No draw is open"
        assert Global.round > self.commit_round.value, "Beacon round has not passed"

        randomness = self._beacon_value(self.commit_round.value)
        winning_ticket = op.extract_uint64(randomness, 0) % self.tickets_snapshot.value
        winner = Box(
            Account, key=op.concat(TICKET_PREFIX, op.itob(winning_ticket))
        ).value

        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, winner.bytes))
        if allocation:
            # This winner already has an unclaimed prize, so the reservation
            # made at draw time is not needed; hand it back to the pot.
            allocation.value += self.prize.value
            self.pot.value += ALLOCATION_MBR
        else:
            allocation.value = self.prize.value

        self.last_winner.value = winner
        self.draws_resolved.value += 1
        self.draw_open.value = UInt64(0)

        arc4.emit(
            Resolved(
                draw_id=arc4.UInt64(self.draw_id.value),
                winner=arc4.Address(winner),
                prize=arc4.UInt64(self.prize.value),
                winning_ticket=arc4.UInt64(winning_ticket),
            )
        )
        return arc4.Address(winner)

    @abimethod()
    def claim(self) -> UInt64:
        """Pull your prize. Only the winner can, and only for themselves."""
        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, Txn.sender.bytes))
        assert allocation, "Nothing allocated to you"
        amount = allocation.value
        del allocation.value
        asset = self.prize_asset.value
        if asset == 0:
            # Deleting the box releases its minimum balance back to the pot,
            # where it pays for the next winner's allocation.
            self.pot.value += ALLOCATION_MBR
            itxn.Payment(receiver=Txn.sender, amount=amount).submit()
        else:
            # The freed minimum balance is ALGO and the pot is not, so it
            # cannot be recycled into it. It stays in the app account, which is
            # where the next allocation box's minimum balance comes from
            # anyway, so nothing is stranded.
            assert Txn.sender.is_opted_in(Asset(asset)), "Opt in to the prize asset first"
            itxn.AssetTransfer(
                xfer_asset=Asset(asset),
                asset_receiver=Txn.sender,
                asset_amount=amount,
            ).submit()
        return amount

    @abimethod(readonly=True)
    def allocation_of(self, who: arc4.Address) -> UInt64:
        """What `who` can claim right now."""
        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, who.bytes))
        return allocation.value if allocation else UInt64(0)

    @subroutine
    def _beacon_value(self, round_number: UInt64) -> Bytes:
        """The VRF output for a past round, straight from the beacon."""
        result = itxn.ApplicationCall(
            app_id=Application(self.beacon_app.value),
            app_args=(
                arc4.arc4_signature("must_get(uint64,byte[])byte[]"),
                op.itob(round_number),
                # An empty user_data argument, ARC-4 encoded.
                op.bzero(2),
            ),
            on_completion=OnCompleteAction.NoOp,
        ).submit()
        # ARC-4 return: 4-byte log prefix, then a byte[] as uint16 length + data.
        return op.extract(result.last_log, 6, 32)
