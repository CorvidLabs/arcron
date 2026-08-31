"""`--upkeep` is what makes reclaim safe to point at the live app.

Without it the script cancels every upkeep the account created, which is right
for draining a superseded deployment and wrong for 769891898, where the same
deployer owns upkeeps it wants to keep. These tests pin the selection, because
getting it wrong destroys escrow that cannot be un-destroyed.
"""

from scripts import reclaim


def _row(upkeep_id: int) -> tuple[int, object, int]:
    return (upkeep_id, object(), 100)


def test_no_request_keeps_every_row() -> None:
    decoded = [_row(19), _row(79), _row(91)]
    kept, missing = reclaim.select(decoded, None)
    assert kept == decoded
    assert missing == []


def test_empty_request_is_not_a_request() -> None:
    # argparse gives None when --upkeep is absent, never []. Treat both as
    # "no filter" so an empty list can never mean "cancel nothing" silently.
    decoded = [_row(19), _row(79)]
    kept, missing = reclaim.select(decoded, [])
    assert kept == decoded
    assert missing == []


def test_request_keeps_only_the_named_ids() -> None:
    decoded = [_row(19), _row(79), _row(91)]
    kept, missing = reclaim.select(decoded, [79])
    assert [row[0] for row in kept] == [79]
    assert missing == []


def test_request_keeps_box_order_not_argument_order() -> None:
    decoded = [_row(19), _row(79), _row(91)]
    kept, _ = reclaim.select(decoded, [91, 19])
    assert [row[0] for row in kept] == [19, 91]


def test_unknown_id_is_reported_not_ignored() -> None:
    decoded = [_row(19), _row(79)]
    kept, missing = reclaim.select(decoded, [79, 12345])
    assert [row[0] for row in kept] == [79]
    assert missing == [12345]


def test_all_ids_unknown_selects_nothing() -> None:
    decoded = [_row(19), _row(79)]
    kept, missing = reclaim.select(decoded, [404])
    assert kept == []
    assert missing == [404]


def test_duplicate_ids_do_not_duplicate_the_cancel() -> None:
    # Cancelling twice would fail the second time, but the priced total would
    # already have double-counted the refund. One row per box.
    decoded = [_row(19), _row(79)]
    kept, missing = reclaim.select(decoded, [79, 79])
    assert [row[0] for row in kept] == [79]
    assert missing == []
