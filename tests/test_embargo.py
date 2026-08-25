"""The embargo contract's one-way property.

The whole value of a timed release is what *cannot* happen: it cannot be
published early, and once scheduled the author cannot change their mind. Both
are asserted here; the "a real keeper publishes it on time" half needs a real
AVM and lives in scripts/embargo_demo.py.
"""

import json
import pathlib
from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.embargo.contract import BOX_MBR_FIXED, CONTENT_KEY, MAX_CONTENT, Embargo

CONTENT = b"The board voted 7-2 to approve the merger."
RELEASE_ROUND = 2_000


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def embargo(context: AlgopyTestContext) -> Embargo:
    context.ledger.patch_global_fields(round=UInt64(1_000))
    return Embargo()


def _schedule(
    context: AlgopyTestContext,
    embargo: Embargo,
    *,
    content: bytes = CONTENT,
    release_round: int = RELEASE_ROUND,
    mbr: int | None = None,
) -> int:
    app_address = context.ledger.get_app(embargo).address
    if mbr is None:
        mbr = BOX_MBR_FIXED + 400 * len(content)
    payment = context.any.txn.payment(receiver=app_address, amount=mbr)
    return embargo.schedule(payment, arc4.DynamicBytes(content), UInt64(release_round))



def test_only_the_creator_can_schedule(context: AlgopyTestContext) -> None:
    """A stranger must not be able to take authorship of a fresh instance.

    Creating the app and scheduling into it are separate transactions in every
    path there is, so an open `schedule` leaves a window where anyone watching
    the mempool can commit their own content first. `schedule` runs once, so
    winning that race is permanent: the real author loses the instance and the
    box MBR they were about to spend, and the hijacker can pick a release
    round that never arrives.
    """
    contract = Embargo()
    stranger = context.any.account()
    payment = context.any.txn.payment(
        receiver=context.ledger.get_app(contract).address, amount=BOX_MBR_FIXED + 4_000
    )
    with context.txn.create_group(active_txn_overrides={"sender": stranger}):
        with pytest.raises(Exception, match="Only the creator can schedule"):
            contract.schedule(payment, arc4.DynamicBytes(b"theirs"), UInt64(RELEASE_ROUND))

def test_schedule_stores_the_content_and_the_round(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    assert _schedule(context, embargo) == RELEASE_ROUND
    assert embargo.release_round.value == RELEASE_ROUND
    assert embargo.content_length.value == len(CONTENT)
    assert context.ledger.get_box(embargo, CONTENT_KEY) == CONTENT


def test_charges_the_real_box_mbr(context: AlgopyTestContext, embargo: Embargo) -> None:
    """The MBR collected must cover what the box actually costs the app.

    Derived from the stored box rather than restating the formula — the same
    regression the keeper contract had, where an undercharge left the app
    unable to meet its own minimum balance.
    """
    _schedule(context, embargo)
    stored = context.ledger.get_box(embargo, CONTENT_KEY)
    actual_mbr = 2_500 + 400 * (len(CONTENT_KEY) + len(stored))
    assert BOX_MBR_FIXED + 400 * len(CONTENT) == actual_mbr


def test_rejects_an_mbr_payment_one_microalgo_short(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    short = BOX_MBR_FIXED + 400 * len(CONTENT) - 1
    with pytest.raises(AssertionError, match="MBR payment too small"):
        _schedule(context, embargo, mbr=short)


def test_rejects_a_release_round_in_the_past(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    with pytest.raises(AssertionError, match="Release round is in the past"):
        _schedule(context, embargo, release_round=999)


@pytest.mark.parametrize("content", [b"", b"x" * (MAX_CONTENT + 1)])
def test_rejects_content_outside_the_bounds(
    context: AlgopyTestContext, embargo: Embargo, content: bytes
) -> None:
    with pytest.raises(AssertionError, match="Content size out of bounds"):
        _schedule(context, embargo, content=content)


def test_cannot_be_rescheduled(context: AlgopyTestContext, embargo: Embargo) -> None:
    # There is no second chance and no method to change one's mind.
    _schedule(context, embargo)
    with pytest.raises(AssertionError, match="Already scheduled"):
        _schedule(context, embargo, content=b"Actually, never mind.")


def test_cannot_publish_before_the_release_round(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    _schedule(context, embargo)
    for round_number in (1_000, RELEASE_ROUND - 1):
        context.ledger.patch_global_fields(round=UInt64(round_number))
        with pytest.raises(AssertionError, match="Embargo has not lifted"):
            embargo.publish()
    assert embargo.published_round.value == 0


def test_publishes_at_exactly_the_release_round(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    _schedule(context, embargo)
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND))

    assert embargo.publish() == RELEASE_ROUND
    assert embargo.published_round.value == RELEASE_ROUND
    assert embargo.is_published() is True
    # The content was always readable; publication is the event, not the reveal.
    assert context.ledger.get_box(embargo, CONTENT_KEY) == CONTENT


def test_publishing_late_still_works(context: AlgopyTestContext, embargo: Embargo) -> None:
    # Nobody watching at the exact round does not mean the release is lost.
    _schedule(context, embargo)
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND + 10_000))
    assert embargo.publish() == RELEASE_ROUND + 10_000


def test_cannot_publish_twice(context: AlgopyTestContext, embargo: Embargo) -> None:
    _schedule(context, embargo)
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND))
    embargo.publish()
    with pytest.raises(AssertionError, match="Already published"):
        embargo.publish()


def test_publishing_nothing_is_rejected(context: AlgopyTestContext, embargo: Embargo) -> None:
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND))
    with pytest.raises(AssertionError, match="Nothing scheduled"):
        embargo.publish()


def test_rounds_remaining_counts_down_then_stops(
    context: AlgopyTestContext, embargo: Embargo
) -> None:
    _schedule(context, embargo)
    assert embargo.rounds_remaining() == RELEASE_ROUND - 1_000
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND - 1))
    assert embargo.rounds_remaining() == 1
    context.ledger.patch_global_fields(round=UInt64(RELEASE_ROUND + 500))
    assert embargo.rounds_remaining() == 0


def test_the_author_has_no_lever_at_all(context: AlgopyTestContext, embargo: Embargo) -> None:
    """The author's powers after scheduling, enumerated: none.

    Asserted against the compiled ABI rather than the Python object, because
    that is the surface an author actually has. If a method is ever added that
    lets the author alter, delay or cancel a scheduled release, this fails and
    the demo's central claim has to be withdrawn with it.
    """
    spec = json.loads(
        pathlib.Path("smart_contracts/artifacts/embargo/Embargo.arc56.json").read_text()
    )
    assert {method["name"] for method in spec["methods"]} == {
        "schedule",
        "publish",
        "is_published",
        "rounds_remaining",
    }

    _schedule(context, embargo)
    # The only method that mutates anything after scheduling is publish, and
    # the author can neither bring it forward nor prevent it.
    with pytest.raises(AssertionError, match="Embargo has not lifted"):
        embargo.publish()
