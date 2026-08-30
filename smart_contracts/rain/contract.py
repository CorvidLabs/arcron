# pyright: reportMissingModuleSource=false
"""A hub of rains: anyone makes one, Arcron fires them.

One app, many rains, one upkeep. Each rain is a box. `draw` is the Arcron
hook: it walks a few rains, rains on the ones that are due, and returns
rather than failing when there is nothing to do. It moves no money and
makes no inner call. Holders pull `claim` for themselves.

Three ways a rain can fall, picked at create:

* SPLIT — enter once. Each fire takes `drip` from the pot and credits every
  ticket the same share. Fill a billion of a token, drip ten thousand a
  day, forget it.
* ONE — enter once. Each fire locks `drip` for one random ticket. A later
  `resolve` reads that round's block seed; `abandon` returns the lock if
  nobody does in time.
* WAVE — Discord rain. People `gm` during the interval. The next fire
  splits `drip` across up to `wave_cap` of them (the first to check in).
  Unclaimed shares from the last drop return to the pot at the next fire.

Anyone may create a rain: any NFT collection as the gate, ALGO or any ASA
as the prize, any drip, any interval. Corvid Labs uses this same hub.
"""

from typing import Literal

from algopy import (
    ARC4Contract,
    Account,
    Asset,
    Box,
    Bytes,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
    subroutine,
    urange,
)
from algopy.arc4 import abimethod

Label = arc4.StaticArray[arc4.Byte, Literal[32]]

# Rain box: b"r" + itob(id). Ticket: b"t" + itob(id) + address.
# Lottery index: b"n" + itob(id) + itob(index) -> address.
RAIN_PREFIX = b"r"
TICKET_PREFIX = b"t"
INDEX_PREFIX = b"n"

# 2,500 per box + 400 per byte of name and value.
# Rain: 9-byte name, 224-byte value.
RAIN_BOX_MBR = 2_500 + 400 * 233
# Ticket: 41-byte name, 24-byte value.
TICKET_MBR = 2_500 + 400 * 65
# Lottery index: 17-byte name, 32-byte address.
INDEX_MBR = 2_500 + 400 * 49
ASSET_OPT_IN_MBR = 100_000
# Held aside at bootstrap, never credited to any pot.
APP_BASE_MBR = 100_000

SPLIT = 0
ONE = 1
WAVE = 2

MIN_INTERVAL_ROUNDS = 10
MAX_INTERVAL_ROUNDS = 1_000_000_000
# How many rain boxes `draw` will open. Arcron spends 2 of 8 reference slots;
# a stock keeper discovers the rest by simulation. Four leaves headroom.
DRAW_SCAN = 4
# ONE: lock now, resolve against the seed of this future round.
COMMIT_DELAY = 8
# Block seed is only readable for ~1,000 past rounds. Inside that, `resolve`;
# past it, `abandon`.
SEED_WINDOW = 800


class RainRec(arc4.Struct):
    """One rain, stored at `r` + itob(id)."""

    creator: arc4.Address
    gate_creator: arc4.Address
    label: Label
    prize_asset: arc4.UInt64
    drip: arc4.UInt64
    interval_rounds: arc4.UInt64
    last_rain_round: arc4.UInt64
    pot: arc4.UInt64
    tickets: arc4.UInt64
    draw_id: arc4.UInt64
    cumulative: arc4.UInt64
    mode: arc4.UInt64
    wave_cap: arc4.UInt64
    wave_count: arc4.UInt64
    last_share: arc4.UInt64
    last_wave_id: arc4.UInt64
    wave_unclaimed: arc4.UInt64
    commit_round: arc4.UInt64
    prize_locked: arc4.UInt64


class Ticket(arc4.Struct):
    """Per (rain, account). SPLIT uses `credit` as debt; WAVE/ONE as owed."""

    credit: arc4.UInt64
    wave_id: arc4.UInt64
    settled_id: arc4.UInt64


class Rained(arc4.Struct):
    """Emitted when a rain actually falls (or a lottery locks)."""

    rain_id: arc4.UInt64
    draw_id: arc4.UInt64
    mode: arc4.UInt64
    paid: arc4.UInt64
    share: arc4.UInt64
    count: arc4.UInt64


class Rain(ARC4Contract):
    """Anyone creates a rain. Arcron fires the ones that are due."""

    def __init__(self) -> None:
        self.next_rain_id = GlobalState(UInt64(0))
        self.cursor = GlobalState(UInt64(0))
        self.bootstrapped = GlobalState(UInt64(0))

    @abimethod()
    def bootstrap(self, mbr_payment: gtxn.PaymentTransaction) -> None:
        """Fund the app account's own floor. Creator only, once."""
        assert Txn.sender == Global.creator_address, "Only the creator can bootstrap"
        assert self.bootstrapped.value == 0, "Already bootstrapped"
        self._require_mbr(mbr_payment, UInt64(APP_BASE_MBR))
        self.bootstrapped.value = UInt64(1)

    @abimethod()
    def opt_in_prize_asset(self, prize: Asset, mbr_payment: gtxn.PaymentTransaction) -> UInt64:
        """Let the hub hold an asset so rains can pay in it. Anyone, once per asset."""
        assert self.bootstrapped.value == 1, "Not bootstrapped"
        assert prize.clawback == Global.zero_address, "Prize asset has a clawback address"
        assert prize.freeze == Global.zero_address, "Prize asset has a freeze address"
        assert prize.manager == Global.zero_address, "Prize asset has a manager address"
        assert not prize.default_frozen, "Prize asset is frozen by default"
        assert not Global.current_application_address.is_opted_in(prize), "Already opted in"
        self._require_mbr(mbr_payment, UInt64(ASSET_OPT_IN_MBR))
        itxn.AssetTransfer(
            xfer_asset=prize,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
            fee=0,
        ).submit()
        return prize.id

    @abimethod()
    def create_rain(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        label: Label,
        gate_creator: arc4.Address,
        prize_asset: UInt64,
        drip: UInt64,
        interval_rounds: UInt64,
        mode: UInt64,
        wave_cap: UInt64,
    ) -> UInt64:
        """Open a rain. Returns its id. Anyone, after bootstrap.

        `gate_creator` zero leaves entry open. Set to a collection's minting
        account, only holders of something it created may enter.

        `prize_asset` zero keeps the pot in ALGO. Set, the hub must already
        be opted into that asset.

        `mode` is SPLIT (0), ONE (1), or WAVE (2). WAVE needs `wave_cap` > 0;
        the others need it 0.
        """
        assert self.bootstrapped.value == 1, "Not bootstrapped"
        assert drip > 0, "Drip must be positive"
        assert interval_rounds >= MIN_INTERVAL_ROUNDS, "Interval below minimum"
        assert interval_rounds <= MAX_INTERVAL_ROUNDS, "Interval above maximum"
        assert mode <= WAVE, "Unknown mode"
        if mode == WAVE:
            assert wave_cap > 0, "Wave cap must be positive"
        else:
            assert wave_cap == 0, "Wave cap is for WAVE rains"
        if prize_asset > 0:
            assert Global.current_application_address.is_opted_in(
                Asset(prize_asset)
            ), "Hub is not opted into the prize asset"
        self._require_mbr(mbr_payment, UInt64(RAIN_BOX_MBR))

        rain_id = self.next_rain_id.value + 1
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        box.value = RainRec(
            creator=arc4.Address(Txn.sender),
            gate_creator=gate_creator,
            label=label.copy(),
            prize_asset=arc4.UInt64(prize_asset),
            drip=arc4.UInt64(drip),
            interval_rounds=arc4.UInt64(interval_rounds),
            last_rain_round=arc4.UInt64(Global.round),
            pot=arc4.UInt64(0),
            tickets=arc4.UInt64(0),
            draw_id=arc4.UInt64(0),
            cumulative=arc4.UInt64(0),
            mode=arc4.UInt64(mode),
            wave_cap=arc4.UInt64(wave_cap),
            wave_count=arc4.UInt64(0),
            last_share=arc4.UInt64(0),
            last_wave_id=arc4.UInt64(0),
            wave_unclaimed=arc4.UInt64(0),
            commit_round=arc4.UInt64(0),
            prize_locked=arc4.UInt64(0),
        )
        self.next_rain_id.value = rain_id
        return rain_id

    @abimethod()
    def set_rain(self, rain_id: UInt64, drip: UInt64, interval_rounds: UInt64) -> None:
        """Tune the slice and the interval. The rain's creator, after it exists."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        assert rec.creator.native == Txn.sender, "Only the rain's creator can tune it"
        assert drip > 0, "Drip must be positive"
        assert interval_rounds >= MIN_INTERVAL_ROUNDS, "Interval below minimum"
        assert interval_rounds <= MAX_INTERVAL_ROUNDS, "Interval above maximum"
        box.value = rec._replace(
            drip=arc4.UInt64(drip),
            interval_rounds=arc4.UInt64(interval_rounds),
            label=rec.label.copy(),
        )

    @abimethod()
    def enter(self, mbr_payment: gtxn.PaymentTransaction, rain_id: UInt64, gate_asset: Asset) -> UInt64:
        """Take a ticket on one rain. One per account per rain.

        SPLIT/ONE: you are in every future drop. WAVE: you also check in for
        the open drop if there is still a seat. A ONE rain with a draw open
        refuses entry until `resolve` or `abandon`: the committed round's
        seed is public, and a ticket taken against it could aim the winner.
        """
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        self._require_gate(rec, gate_asset)

        needed = UInt64(TICKET_MBR)
        if rec.mode.native == ONE:
            needed = UInt64(TICKET_MBR + INDEX_MBR)
        self._require_mbr(mbr_payment, needed)

        ticket = Box(Ticket, key=self._ticket_key(rain_id, Txn.sender))
        assert not ticket, "Already entered"

        credit: UInt64 = rec.cumulative.native if rec.mode.native == SPLIT else UInt64(0)
        wave_id = UInt64(0)
        tickets: UInt64 = rec.tickets.native + 1
        wave_count: UInt64 = rec.wave_count.native

        if rec.mode.native == ONE:
            # `resolve` draws over the live count, and an open draw's seed is
            # public once `commit_round` passes. A ticket taken while the
            # prize is locked could be one of a batch sized to catch it, so
            # the count freezes at fire and reopens at resolve or abandon.
            assert rec.prize_locked.native == 0, "Draw open; enter after resolve or abandon"
            index = Box(
                arc4.Address,
                key=op.concat(INDEX_PREFIX, op.concat(op.itob(rain_id), op.itob(rec.tickets.native))),
            )
            index.value = arc4.Address(Txn.sender)
        elif rec.mode.native == WAVE:
            if rec.wave_count.native < rec.wave_cap.native:
                wave_id = rec.last_wave_id.native + 1
                wave_count = rec.wave_count.native + 1

        ticket.value = Ticket(
            credit=arc4.UInt64(credit),
            wave_id=arc4.UInt64(wave_id),
            settled_id=arc4.UInt64(0),
        )
        box.value = rec._replace(
            tickets=arc4.UInt64(tickets),
            wave_count=arc4.UInt64(wave_count),
            label=rec.label.copy(),
        )
        return tickets

    @abimethod()
    def gm(self, rain_id: UInt64, gate_asset: Asset) -> UInt64:
        """Check in for this WAVE drop. First `wave_cap` people this interval.

        Also settles any unclaimed share from the last drop you were in.
        Returns the open wave id, or 0 if you were already in or it is full.
        """
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        assert rec.mode.native == WAVE, "Not a WAVE rain"
        self._require_gate(rec, gate_asset)

        ticket_box = Box(Ticket, key=self._ticket_key(rain_id, Txn.sender))
        if not ticket_box:
            return UInt64(0)
        ticket = ticket_box.value.copy()
        rec, ticket = self._settle_wave(rec, ticket)

        open_wave: UInt64 = rec.last_wave_id.native + 1
        if ticket.wave_id.native == open_wave:
            ticket_box.value = ticket.copy()
            box.value = rec._replace(label=rec.label.copy())
            return UInt64(0)
        if rec.wave_count.native >= rec.wave_cap.native:
            ticket_box.value = ticket.copy()
            box.value = rec._replace(label=rec.label.copy())
            return UInt64(0)

        ticket = ticket._replace(wave_id=arc4.UInt64(open_wave))
        rec = rec._replace(
            wave_count=arc4.UInt64(rec.wave_count.native + 1),
            label=rec.label.copy(),
        )
        ticket_box.value = ticket.copy()
        box.value = rec.copy()
        return open_wave

    @abimethod()
    def deposit(self, payment: gtxn.PaymentTransaction, rain_id: UInt64) -> UInt64:
        """Add ALGO to a rain's pot. Anyone, any amount. Returns the new pot."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        assert rec.prize_asset.native == 0, "This rain pays an asset; use deposit_asset"
        assert (
            payment.receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert payment.rekey_to == Global.zero_address, "Deposit must not rekey"
        assert (
            payment.close_remainder_to == Global.zero_address
        ), "Deposit must not close"
        assert payment.amount > 0, "Amount must be positive"
        pot: UInt64 = rec.pot.native + payment.amount
        box.value = rec._replace(pot=arc4.UInt64(pot), label=rec.label.copy())
        return pot

    @abimethod()
    def deposit_asset(self, transfer: gtxn.AssetTransferTransaction, rain_id: UInt64) -> UInt64:
        """Add the prize asset to a rain's pot. Anyone, any amount."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        asset = rec.prize_asset.native
        assert asset > 0, "This rain pays ALGO; use deposit"
        assert (
            transfer.asset_receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert transfer.rekey_to == Global.zero_address, "Deposit must not rekey"
        assert (
            transfer.asset_close_to == Global.zero_address
        ), "Deposit must not close the asset"
        assert transfer.xfer_asset.id == asset, "Wrong asset"
        assert transfer.asset_amount > 0, "Amount must be positive"
        pot: UInt64 = rec.pot.native + transfer.asset_amount
        box.value = rec._replace(pot=arc4.UInt64(pot), label=rec.label.copy())
        return pot

    @abimethod()
    def draw(self) -> UInt64:
        """Rain on up to DRAW_SCAN due rains. Zero arguments.

        A no-op returning 0 when the hub is empty or nothing is due. A
        scheduled call that fails would trip keeper backoff; a quiet week
        must be uneventful. Fires every due rain it opens, not just the first.
        """
        n = self.next_rain_id.value
        if n == 0:
            return UInt64(0)
        start = self.cursor.value
        fired = UInt64(0)
        for step in urange(DRAW_SCAN):
            rid = (start + step) % n + 1
            fired += self._try_fire(rid)
        self.cursor.value = (start + DRAW_SCAN) % n
        return fired

    @abimethod()
    def resolve(self, rain_id: UInt64) -> UInt64:
        """Pick the ONE winner from the committed round's block seed.

        Anyone, once the committed round has passed and while its seed is
        still readable. Credits that ticket. Returns the winning index.
        """
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        assert rec.mode.native == ONE, "Not a ONE rain"
        assert rec.prize_locked.native > 0, "No draw is open"
        assert Global.round > rec.commit_round.native, "Too early"
        assert Global.round <= rec.commit_round.native + SEED_WINDOW, "Window closed; abandon"

        seed = op.Block.blk_seed(rec.commit_round.native)
        index: UInt64 = op.extract_uint64(seed, 0) % rec.tickets.native
        winner_box = Box(
            arc4.Address,
            key=op.concat(INDEX_PREFIX, op.concat(op.itob(rain_id), op.itob(index))),
        )
        assert winner_box, "Missing ticket index"
        winner = winner_box.value.copy()
        ticket_box = Box(Ticket, key=self._ticket_key(rain_id, winner.native))
        assert ticket_box, "Missing ticket"
        ticket = ticket_box.value.copy()
        ticket_box.value = ticket._replace(
            credit=arc4.UInt64(ticket.credit.native + rec.prize_locked.native),
        )
        box.value = rec._replace(
            prize_locked=arc4.UInt64(0),
            commit_round=arc4.UInt64(0),
            label=rec.label.copy(),
        )
        return index

    @abimethod()
    def abandon(self, rain_id: UInt64) -> UInt64:
        """Return a ONE lock to the pot after the seed window. Anyone."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        rec = box.value.copy()
        assert rec.mode.native == ONE, "Not a ONE rain"
        assert rec.prize_locked.native > 0, "No draw is open"
        assert Global.round > rec.commit_round.native + SEED_WINDOW, "Window still open"
        pot: UInt64 = rec.pot.native + rec.prize_locked.native
        box.value = rec._replace(
            pot=arc4.UInt64(pot),
            prize_locked=arc4.UInt64(0),
            commit_round=arc4.UInt64(0),
            label=rec.label.copy(),
        )
        return pot

    @abimethod()
    def claim(self, rain_id: UInt64, gate_asset: Asset) -> UInt64:
        """Pull the rain credited to you on this rain.

        On a gated rain you must still hold a collection token. WAVE also
        settles the last drop you checked in for. Returns 0 when there is
        nothing to collect, rather than failing.
        """
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        if not box:
            return UInt64(0)
        rec = box.value.copy()
        ticket_box = Box(Ticket, key=self._ticket_key(rain_id, Txn.sender))
        if not ticket_box:
            return UInt64(0)
        ticket = ticket_box.value.copy()

        if rec.mode.native == WAVE:
            rec, ticket = self._settle_wave(rec, ticket)

        owed: UInt64 = UInt64(0)
        if rec.mode.native == SPLIT:
            owed = rec.cumulative.native - ticket.credit.native
            if owed > 0:
                ticket = ticket._replace(credit=arc4.UInt64(rec.cumulative.native))
        else:
            owed = ticket.credit.native
            if owed > 0:
                ticket = ticket._replace(credit=arc4.UInt64(0))

        if owed == 0:
            ticket_box.value = ticket.copy()
            box.value = rec._replace(label=rec.label.copy())
            return UInt64(0)

        self._require_gate(rec, gate_asset)
        ticket_box.value = ticket.copy()
        box.value = rec._replace(label=rec.label.copy())
        self._pay(rec.prize_asset.native, owed)
        return owed

    @abimethod(readonly=True)
    def allocation_of(self, rain_id: UInt64, who: arc4.Address) -> UInt64:
        """What `who` can claim on this rain right now."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        if not box:
            return UInt64(0)
        rec = box.value.copy()
        ticket_box = Box(Ticket, key=self._ticket_key(rain_id, who.native))
        if not ticket_box:
            return UInt64(0)
        ticket = ticket_box.value.copy()
        if rec.mode.native == SPLIT:
            owed_split: UInt64 = rec.cumulative.native - ticket.credit.native
            return owed_split
        owed: UInt64 = ticket.credit.native
        if rec.mode.native == WAVE:
            if (
                ticket.wave_id.native == rec.last_wave_id.native
                and ticket.settled_id.native != rec.last_wave_id.native
            ):
                owed += rec.last_share.native
        return owed

    @abimethod(readonly=True)
    def rain_of(self, rain_id: UInt64) -> RainRec:
        """The rain box, for clients that would rather not decode it."""
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        assert box, "No such rain"
        return box.value.copy()

    @subroutine
    def _try_fire(self, rain_id: UInt64) -> UInt64:
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        if not box:
            return UInt64(0)
        rec = box.value.copy()
        if rec.last_rain_round.native != 0:
            if Global.round < rec.last_rain_round.native + rec.interval_rounds.native:
                return UInt64(0)

        mode = rec.mode.native
        if mode == SPLIT:
            return self._fire_split(rain_id, rec)
        if mode == WAVE:
            return self._fire_wave(rain_id, rec)
        return self._fire_one(rain_id, rec)

    @subroutine
    def _fire_split(self, rain_id: UInt64, rec: RainRec) -> UInt64:
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        if rec.tickets.native == 0:
            return UInt64(0)
        share = rec.drip.native // rec.tickets.native
        if share == 0:
            return UInt64(0)
        paid = share * rec.tickets.native
        if rec.pot.native < paid:
            return UInt64(0)
        draw_id = rec.draw_id.native + 1
        box.value = rec._replace(
            pot=arc4.UInt64(rec.pot.native - paid),
            cumulative=arc4.UInt64(rec.cumulative.native + share),
            draw_id=arc4.UInt64(draw_id),
            last_rain_round=arc4.UInt64(Global.round),
            label=rec.label.copy(),
        )
        arc4.emit(
            Rained(
                rain_id=arc4.UInt64(rain_id),
                draw_id=arc4.UInt64(draw_id),
                mode=arc4.UInt64(SPLIT),
                paid=arc4.UInt64(paid),
                share=arc4.UInt64(share),
                count=arc4.UInt64(rec.tickets.native),
            )
        )
        return UInt64(1)

    @subroutine
    def _fire_wave(self, rain_id: UInt64, rec: RainRec) -> UInt64:
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        pot = rec.pot.native + rec.last_share.native * rec.wave_unclaimed.native
        if rec.wave_count.native == 0:
            if rec.wave_unclaimed.native > 0:
                box.value = rec._replace(
                    pot=arc4.UInt64(pot),
                    wave_unclaimed=arc4.UInt64(0),
                    last_share=arc4.UInt64(0),
                    label=rec.label.copy(),
                )
            return UInt64(0)
        share = rec.drip.native // rec.wave_count.native
        if share == 0:
            return UInt64(0)
        paid = share * rec.wave_count.native
        if pot < paid:
            return UInt64(0)
        draw_id = rec.draw_id.native + 1
        wave_id = rec.last_wave_id.native + 1
        box.value = rec._replace(
            pot=arc4.UInt64(pot - paid),
            draw_id=arc4.UInt64(draw_id),
            last_rain_round=arc4.UInt64(Global.round),
            last_share=arc4.UInt64(share),
            last_wave_id=arc4.UInt64(wave_id),
            wave_unclaimed=arc4.UInt64(rec.wave_count.native),
            wave_count=arc4.UInt64(0),
            label=rec.label.copy(),
        )
        arc4.emit(
            Rained(
                rain_id=arc4.UInt64(rain_id),
                draw_id=arc4.UInt64(draw_id),
                mode=arc4.UInt64(WAVE),
                paid=arc4.UInt64(paid),
                share=arc4.UInt64(share),
                count=arc4.UInt64(rec.wave_count.native),
            )
        )
        return UInt64(1)

    @subroutine
    def _fire_one(self, rain_id: UInt64, rec: RainRec) -> UInt64:
        box = Box(RainRec, key=op.concat(RAIN_PREFIX, op.itob(rain_id)))
        if rec.prize_locked.native > 0:
            return UInt64(0)
        if rec.tickets.native == 0:
            return UInt64(0)
        if rec.pot.native < rec.drip.native:
            return UInt64(0)
        draw_id = rec.draw_id.native + 1
        box.value = rec._replace(
            pot=arc4.UInt64(rec.pot.native - rec.drip.native),
            prize_locked=arc4.UInt64(rec.drip.native),
            commit_round=arc4.UInt64(Global.round + COMMIT_DELAY),
            draw_id=arc4.UInt64(draw_id),
            last_rain_round=arc4.UInt64(Global.round),
            label=rec.label.copy(),
        )
        arc4.emit(
            Rained(
                rain_id=arc4.UInt64(rain_id),
                draw_id=arc4.UInt64(draw_id),
                mode=arc4.UInt64(ONE),
                paid=arc4.UInt64(rec.drip.native),
                share=arc4.UInt64(rec.drip.native),
                count=arc4.UInt64(rec.tickets.native),
            )
        )
        return UInt64(1)

    @subroutine
    def _settle_wave(self, rec: RainRec, ticket: Ticket) -> tuple[RainRec, Ticket]:
        if ticket.wave_id.native != rec.last_wave_id.native:
            return rec.copy(), ticket.copy()
        if ticket.settled_id.native == rec.last_wave_id.native:
            return rec.copy(), ticket.copy()
        unclaimed = rec.wave_unclaimed.native
        if unclaimed > 0:
            unclaimed -= 1
        return rec._replace(
            wave_unclaimed=arc4.UInt64(unclaimed),
            label=rec.label.copy(),
        ), ticket._replace(
            credit=arc4.UInt64(ticket.credit.native + rec.last_share.native),
            settled_id=rec.last_wave_id,
        )

    @subroutine
    def _require_gate(self, rec: RainRec, gate_asset: Asset) -> None:
        gate = rec.gate_creator.native
        if gate != Global.zero_address:
            assert Txn.sender.is_opted_in(gate_asset), "Hold a token from the collection"
            assert gate_asset.balance(Txn.sender) > 0, "Hold a token from the collection"
            assert gate_asset.creator == gate, "That asset is not from the collection"
            assert gate_asset.id != rec.prize_asset.native, "The prize is not a ticket"

    @subroutine
    def _require_mbr(self, payment: gtxn.PaymentTransaction, minimum: UInt64) -> None:
        assert (
            payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert payment.sender == Txn.sender, "MBR payment must come from the caller"
        assert payment.rekey_to == Global.zero_address, "MBR payment must not rekey"
        assert (
            payment.close_remainder_to == Global.zero_address
        ), "MBR payment must not close"
        assert payment.amount >= minimum, "MBR payment too small"

    @subroutine
    def _ticket_key(self, rain_id: UInt64, who: Account) -> Bytes:
        return op.concat(TICKET_PREFIX, op.concat(op.itob(rain_id), who.bytes))

    @subroutine
    def _pay(self, asset: UInt64, amount: UInt64) -> None:
        if asset == 0:
            itxn.Payment(receiver=Txn.sender, amount=amount, fee=0).submit()
        else:
            assert Txn.sender.is_opted_in(Asset(asset)), "Opt in to the prize asset first"
            itxn.AssetTransfer(
                xfer_asset=Asset(asset),
                asset_receiver=Txn.sender,
                asset_amount=amount,
                fee=0,
            ).submit()
