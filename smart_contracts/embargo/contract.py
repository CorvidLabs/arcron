# pyright: reportMissingModuleSource=false
"""A timed release: content that becomes official at a round nobody controls.

What this guarantees, precisely:

* it **cannot be published early** — `publish` rejects before the release round;
* it **cannot be stopped, delayed or retracted** — once scheduled there is no
  method that alters the content, moves the round or cancels the release, not
  even for the author, and publication is permissionless and paid, so the
  author's cooperation is not required;
* it **needs nothing awake** at the release moment.

What it does *not* guarantee, and no contract on a public chain can: secrecy
before the release. Box contents are readable by anyone from the moment they
are written. This schedules an **unstoppable, timestamped publication event**,
not a sealed envelope. For content that must stay unreadable until the moment
it opens, store a hash commitment here and keep the payload off-chain — and
note that revealing it later needs someone to act, which is exactly the thing
a keeper network cannot do for you.
"""

from algopy import (
    ARC4Contract,
    Box,
    Bytes,
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    op,
)
from algopy.arc4 import abimethod

# Box name for the released content.
CONTENT_KEY = b"content"
# Box minimum balance, less the content: 2,500 µALGO per box plus 400 per byte
# of name and value. The name b"content" is 7 bytes and the value is the
# content itself, so a box costs BOX_MBR_FIXED + 400 * len(content) µALGO.
# (Spelled out rather than computed: Puya evaluates module level literally.)
BOX_MBR_FIXED = 2_500 + 400 * 7
# Content bounds: enough for a statement or any CID, bounded so the MBR a
# scheduler must cover stays predictable.
MAX_CONTENT = 2_048


class Published(arc4.Struct):
    """Emitted the moment the embargo lifts, for indexers and dashboards."""

    release_round: arc4.UInt64
    published_round: arc4.UInt64
    publisher: arc4.Address


class Embargo(ARC4Contract):
    """One scheduled release per instance."""

    def __init__(self) -> None:
        self.author = GlobalState(Global.creator_address)
        self.release_round = GlobalState(UInt64(0))
        self.published_round = GlobalState(UInt64(0))
        self.content_length = GlobalState(UInt64(0))

    @abimethod()
    def schedule(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        content: arc4.DynamicBytes,
        release_round: UInt64,
    ) -> UInt64:
        """Commit content to a release round. Returns that round.

        Callable once. After this there is no way back: no method changes the
        content, moves the round, or cancels — which is the entire point.
        """
        # The author is the creator, fixed when the app was made. Creation and
        # scheduling are separate transactions in every path there is, so
        # leaving this open lets a stranger front-run the author's own first
        # call, take authorship of the instance, and bury it behind a round
        # that never arrives. Scheduling runs once, so there is no second try.
        assert Txn.sender == Global.creator_address, "Only the creator can schedule"
        assert self.release_round.value == 0, "Already scheduled"
        assert release_round > Global.round, "Release round is in the past"
        size = content.native.length
        assert UInt64(0) < size <= MAX_CONTENT, "Content size out of bounds"

        required_mbr = BOX_MBR_FIXED + 400 * size
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        # A rekey hands control of the sender's account to whoever the group
        # names, and a close sweeps it empty to whoever the group names.
        # Both harm only the sender, so the contract loses nothing by
        # refusing them. The exposure is a front end putting either into a
        # group a user signs without reading it closely.
        assert mbr_payment.rekey_to == Global.zero_address, "MBR payment must not rekey"
        assert (
            mbr_payment.close_remainder_to == Global.zero_address
        ), "MBR payment must not close"
        assert mbr_payment.amount >= required_mbr, "MBR payment too small"

        box = Box(Bytes, key=CONTENT_KEY)
        box.value = content.native
        self.author.value = Txn.sender
        self.release_round.value = release_round
        self.content_length.value = size
        return release_round

    @abimethod()
    def publish(self) -> UInt64:
        """Lift the embargo. Permissionless, and callable only once.

        Zero-argument by design: this is the shape an Arcron upkeep can call,
        so a keeper anywhere in the world can be the one to fire it and be paid
        for doing so.
        """
        assert self.release_round.value > 0, "Nothing scheduled"
        assert self.published_round.value == 0, "Already published"
        assert Global.round >= self.release_round.value, "Embargo has not lifted"

        self.published_round.value = Global.round
        arc4.emit(
            Published(
                release_round=arc4.UInt64(self.release_round.value),
                published_round=arc4.UInt64(Global.round),
                publisher=arc4.Address(Txn.sender),
            )
        )
        return Global.round

    @abimethod(readonly=True)
    def is_published(self) -> bool:
        """Whether the embargo has lifted yet."""
        return self.published_round.value > 0

    @abimethod(readonly=True)
    def rounds_remaining(self) -> UInt64:
        """Rounds until the release, or zero once it is due."""
        if Global.round >= self.release_round.value:
            return UInt64(0)
        return self.release_round.value - Global.round
