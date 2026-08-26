# pyright: reportMissingModuleSource=false
"""A pot that pays a random ticket holder on a schedule, run by nobody.

The scheduled call does **accounting only**. `draw` locks a prize, snapshots
the ticket count and fixes a future beacon round; it moves no money, calls no
other app, and touches nothing it cannot reach. That is what makes it callable
by a bare Arcron upkeep.

Everything that needs a resource happens in a transaction somebody sends for
themselves:

* `resolve` inner-calls the randomness beacon, so its caller attaches the
  beacon reference, which a scheduled call could not do, because an Arcron inner call
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
# beacon answers for a past round only, so at the moment a draw opens the
# winner cannot be known by anyone, including whoever opened it.
BEACON_DELAY = 8
# How long after `commit_round` the beacon still answers. The Algorand
# Foundation beacon retains roughly 1,512 rounds, so a draw nobody resolves
# inside that window can never be resolved: `must_get` panics outside it. Held
# short of the real retention so `abandon` cannot race a `resolve` that would
# still have worked.
BEACON_WINDOW = 1_000
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
    """Emitted when a draw opens, before anyone can know the outcome."""

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
        # Nothing may have been staked on the old denomination. Without this,
        # a pot filled with ALGO could be re-pointed at a worthless asset and
        # the ALGO would have no payout path on a contract that cannot be
        # updated or deleted.
        assert self.pot.value == 0, "Configure before the pot is funded"
        assert self.tickets.value == 0, "Configure before anyone enters"
        self.beacon_app.value = beacon_app
        self.gate_creator.value = gate_creator.native
        self.prize_asset.value = prize_asset

    @abimethod()
    def opt_in_prize_asset(self, prize: Asset, mbr_payment: gtxn.PaymentTransaction) -> UInt64:
        """Let the app hold the prize asset. Anyone may pay for it, once.

        An account must opt in before it can receive an asset, and holding one
        costs 100,000 microAlgos of minimum balance permanently. That is not
        the app's to find, so whoever wants the draw running provides it.

        This is also where the asset is checked, because it is the first call
        that has the asset as an available resource and so the first that can
        read its parameters. A prize whose issuer kept clawback can be emptied
        out of the app account at any time while `pot` goes on claiming the
        tokens are there; one whose issuer kept freeze can be made unclaimable
        forever. Either strands the pot on a contract that cannot be updated,
        so a draw that has not been checked cannot be funded: `deposit_asset`
        requires the opt-in, and the opt-in requires this.
        """
        asset = self.prize_asset.value
        assert asset > 0, "Prize is ALGO"
        assert prize.id == asset, "Wrong asset"
        assert prize.clawback == Global.zero_address, "Prize asset has a clawback address"
        assert prize.freeze == Global.zero_address, "Prize asset has a freeze address"
        # Manager too: a manager can set clawback and freeze back again, so
        # checking only the other two would be checking a promise rather than
        # a property.
        assert prize.manager == Global.zero_address, "Prize asset has a manager address"
        # default_frozen is fixed at creation and no address can ever change
        # it, so an asset that starts frozen with no freeze address to thaw it
        # can be received but never sent. The pot would take a prize in and
        # hold it forever, which is the exact failure these checks exist for.
        assert not prize.default_frozen, "Prize asset is frozen by default"
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
            fee=0,
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
        assert self.beacon_app.value > 0, "Not configured"
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
            # A project usually mints its prize token from the same account as
            # its collection, which would make holding the prize a ticket.
            assert gate_asset.id != self.prize_asset.value, "The prize is not a ticket"

        index = self.tickets.value
        Box(Account, key=op.concat(TICKET_PREFIX, op.itob(index))).value = Txn.sender
        self.tickets.value = index + 1
        return index

    @abimethod()
    def deposit(self, payment: gtxn.PaymentTransaction) -> UInt64:
        """Add ALGO to the pot. Anyone, any amount. Returns the new pot."""
        assert self.beacon_app.value > 0, "Not configured"
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
        """Open a draw. Zero arguments, which is what Arcron calls.

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

        # An asset draw reserves nothing from the pot, so the ALGO for the
        # winner's allocation box has to already be in the app account. If it
        # is not, `resolve` would fail on minimum balance with the draw open,
        # and a draw that cannot be resolved can never be reopened. Decline to
        # open one instead: returning 0 is the no-op path a keeper expects,
        # and the pot stays where it is until somebody funds the account.
        if self.prize_asset.value != 0:
            available = (
                Global.current_application_address.balance
                - Global.current_application_address.min_balance
            )
            if available < ALLOCATION_MBR:
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
        assert (
            Global.round <= self.commit_round.value + BEACON_WINDOW
        ), "Beacon window has closed; abandon the draw"

        randomness = self._beacon_value(self.commit_round.value)
        winning_ticket = op.extract_uint64(randomness, 0) % self.tickets_snapshot.value
        winner = Box(
            Account, key=op.concat(TICKET_PREFIX, op.itob(winning_ticket))
        ).value

        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, winner.bytes))
        if allocation:
            allocation.value += self.prize.value
            # This winner already has an unclaimed prize, so the reservation
            # made at draw time is not needed. Hand it back only if there was
            # one: an asset draw reserves nothing from the pot, and crediting
            # an ALGO constant to a pot counted in token units would invent
            # tokens the contract does not hold. The same conditional as
            # `draw` and `claim`, and the one place it was missed.
            if self.prize_asset.value == 0:
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
    def claim(self, gate_asset: Asset) -> UInt64:
        """Pull your prize. Only the winner can, and only for themselves.

        `gate_asset` is the token you are still claiming membership with. It
        is ignored on an ungated draw, and it is checked the same way `enter`
        checks it on a gated one, because the gate has to be asked twice.

        A ticket is a box that never expires, and `enter` only ever asked
        whether the buyer held a collection token at that moment. One token
        walked through ten accounts therefore bought ten permanent tickets and
        diluted every honest holder. Asking again here does not un-buy those
        tickets, but only the account actually holding the token now can
        collect on one, so the other nine stop being worth anything.

        The cost is a real rule, and it should be stated as one rather than
        discovered: **you must still hold a token from the collection when you
        collect.** A winner who sells between the draw and the claim forfeits,
        and that is the same answer whether they sold innocently or to a buyer
        who was never entitled to the draw at all.
        """
        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, Txn.sender.bytes))
        assert allocation, "Nothing allocated to you"

        gate = self.gate_creator.value
        if gate != Global.zero_address:
            assert Txn.sender.is_opted_in(gate_asset), "Hold a token from the collection"
            assert gate_asset.balance(Txn.sender) > 0, "Hold a token from the collection"
            assert gate_asset.creator == gate, "That asset is not from the collection"

        amount = allocation.value
        del allocation.value
        asset = self.prize_asset.value
        if asset == 0:
            # Deleting the box releases its minimum balance back to the pot,
            # where it pays for the next winner's allocation.
            self.pot.value += ALLOCATION_MBR
            itxn.Payment(receiver=Txn.sender, amount=amount, fee=0).submit()
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
                fee=0,
            ).submit()
        return amount

    @abimethod()
    def abandon(self) -> UInt64:
        """Reopen a draw whose beacon window closed. Permissionless.

        Without this a single unresolved draw is fatal. `draw` refuses to open
        another while one is open, `resolve` cannot answer once the beacon has
        forgotten the round, and the contract cannot be updated or deleted, so
        the whole pot would sit locked in `prize` forever.

        Nobody can profit by calling it. The prize returns to the pot intact
        and the next draw commits to a fresh round, so abandoning is only ever
        available once the outcome has become unknowable to everyone.
        """
        assert self.draw_open.value == 1, "No draw is open"
        assert (
            Global.round > self.commit_round.value + BEACON_WINDOW
        ), "The beacon can still answer; resolve it"
        # `draw` took a reservation out of an ALGO pot for a winner's
        # allocation box. No box was created, so it comes back with the prize;
        # otherwise every abandoned draw would strand one box's worth.
        reserve: UInt64 = (
            UInt64(ALLOCATION_MBR) if self.prize_asset.value == 0 else UInt64(0)
        )
        returned: UInt64 = self.prize.value + reserve
        self.pot.value += returned
        self.prize.value = UInt64(0)
        self.draw_open.value = UInt64(0)
        return returned

    @abimethod(readonly=True)
    def allocation_of(self, who: arc4.Address) -> UInt64:
        """What `who` can claim right now."""
        allocation = Box(UInt64, key=op.concat(ALLOCATION_PREFIX, who.bytes))
        return allocation.value if allocation else UInt64(0)

    @subroutine
    def _beacon_value(self, round_number: UInt64) -> Bytes:
        """The VRF output for a past round, straight from the beacon."""
        result = itxn.ApplicationCall(
            fee=0,
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
