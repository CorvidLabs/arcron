"""`--upkeep` is what makes reclaim safe to point at the live app.

Without it the script cancels every upkeep the account created, which is right
for draining a superseded deployment and wrong for 769891898, where the same
deployer owns upkeeps it wants to keep. These tests pin the selection, because
getting it wrong destroys escrow that cannot be un-destroyed.
"""

import pytest

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


# --- what a cancel is signed under -------------------------------------------
#
# #250 F14: this script signs in-process with a key it holds, and signed whatever
# fee the node's advice produced. The bound has to be there before the signature.


def _upkeep(asset_balance: int):
    from dataclasses import replace

    from scripts.keeper_bot import _decode_upkeep
    from tests.test_keeper_bot import LIVE_BOX_HEX

    return replace(_decode_upkeep(1, bytes.fromhex(LIVE_BOX_HEX)), asset_balance=asset_balance)


def test_a_cancel_is_bounded_above_the_inner_budget_it_needs() -> None:
    from scripts.govern import MAX_SIGNABLE_FEE

    params = reclaim.cancel_params(_upkeep(asset_balance=0))
    assert params.extra_fee.micro_algo == 1_000, "one inner payment: the refund"
    assert params.max_fee.micro_algo == MAX_SIGNABLE_FEE + 1_000
    assert params.static_fee is None, "a ceiling, not a replacement for the node's advice"


def test_an_asa_bonus_widens_the_budget_and_the_bound_together() -> None:
    from scripts.govern import MAX_SIGNABLE_FEE

    params = reclaim.cancel_params(_upkeep(asset_balance=750_000))
    assert params.extra_fee.micro_algo == 2_000, "the refund and the bonus transfer"
    assert params.max_fee.micro_algo == MAX_SIGNABLE_FEE + 2_000


def test_the_bound_refuses_an_inflated_fee_before_anything_is_signed() -> None:
    """The composer is what enforces `max_fee`, so prove it does: a node
    advising a fee ten times the minimum per byte, and nothing is built."""
    import algokit_utils
    from algosdk import transaction
    from algokit_utils.transactions.transaction_composer import TransactionComposer
    from algosdk.account import generate_account

    _, sender = generate_account()
    params = reclaim.cancel_params(_upkeep(asset_balance=0))
    inflated = transaction.SuggestedParams(
        fee=12_000, first=1, last=1_000, gh="ALXYc8IX90hlq7olIdloOUZjWfbnA3Ix1N5vLn81zI8=",
        flat_fee=True, min_fee=1_000,
    )
    composer = TransactionComposer(
        algod=None,  # type: ignore[arg-type]
        get_signer=lambda _: None,  # type: ignore[arg-type,return-value]
        get_suggested_params=lambda: inflated,
    )
    composer.add_app_call(
        algokit_utils.AppCallParams(
            app_id=769891898, sender=sender, on_complete=transaction.OnComplete.NoOpOC,
            extra_fee=params.extra_fee, max_fee=params.max_fee,
        )
    )
    with pytest.raises(ValueError, match="greater than max_fee"):
        composer.build_transactions()
