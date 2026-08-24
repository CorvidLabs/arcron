from collections.abc import Iterator

import pytest
from algopy import UInt64, arc4
from algopy_testing import AlgopyTestContext, algopy_testing_context

from smart_contracts.corvid_vault.contract import (
    BOOTSTRAP_MBR,
    MAX_MESSAGE_SIZE,
    MESSAGE_FEE,
    CorvidVault,
)


@pytest.fixture()
def context() -> Iterator[AlgopyTestContext]:
    with algopy_testing_context() as ctx:
        yield ctx


@pytest.fixture()
def contract(context: AlgopyTestContext) -> CorvidVault:
    return CorvidVault()


def _bootstrap(context: AlgopyTestContext, contract: CorvidVault):
    """Bootstrap the vault with a fresh mock CORVID asset."""
    asset = context.any.asset()
    app_address = context.ledger.get_app(contract).address
    mbr_payment = context.any.txn.payment(
        receiver=app_address,
        amount=BOOTSTRAP_MBR,
    )
    contract.bootstrap(mbr_payment, asset)
    return asset


def _opt_in(context: AlgopyTestContext, contract: CorvidVault) -> None:
    contract.opt_in_to_application()


def _deposit(
    context: AlgopyTestContext,
    contract: CorvidVault,
    asset,
    amount: int,
) -> int:
    app_address = context.ledger.get_app(contract).address
    axfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=app_address,
        asset_amount=amount,
    )
    return contract.deposit(axfer)


# ---- relay (operator) helpers ----


def _counter_key(recipient_bytes: bytes) -> bytes:
    return b"i" + recipient_bytes


def _message_key(recipient_bytes: bytes, index: int) -> bytes:
    return b"m" + recipient_bytes + index.to_bytes(8, "big")


def _required_mbr(size: int, first_message: bool) -> int:
    mbr = 2_500 + 400 * (41 + size)
    if first_message:
        mbr += 2_500 + 400 * 41
    return mbr


def _stake(context: AlgopyTestContext, contract: CorvidVault, asset) -> None:
    """Opt in and deposit, making the default sender a staked member."""
    _opt_in(context, contract)
    _deposit(context, contract, asset, 1_000_000)


def _post(
    context: AlgopyTestContext,
    contract: CorvidVault,
    asset,
    recipient: arc4.Address,
    ciphertext: bytes,
    *,
    fee: int = MESSAGE_FEE,
    fee_asset=None,
    mbr: int | None = None,
) -> int:
    app_address = context.ledger.get_app(contract).address
    if mbr is None:
        first = not context.ledger.box_exists(
            contract, _counter_key(recipient.bytes.value)
        )
        mbr = _required_mbr(len(ciphertext), first)
    mbr_payment = context.any.txn.payment(receiver=app_address, amount=mbr)
    fee_payment = context.any.txn.asset_transfer(
        xfer_asset=fee_asset or asset,
        asset_receiver=app_address,
        asset_amount=fee,
    )
    return contract.post(
        mbr_payment, fee_payment, recipient, arc4.DynamicBytes(ciphertext)
    )


def test_bootstrap(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)

    assert contract.asset_id.value == asset.id
    # App opted itself into the ASA via inner transaction.
    opt_in_itxn = context.txn.last_group.itxn_groups[-1][0]
    assert opt_in_itxn.asset_amount == 0
    assert opt_in_itxn.asset_receiver == context.ledger.get_app(contract).address


def test_bootstrap_rejects_second_call(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    _bootstrap(context, contract)

    with pytest.raises(AssertionError, match="Already bootstrapped"):
        _bootstrap(context, contract)


def test_bootstrap_rejects_underfunding(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = context.any.asset()
    app_address = context.ledger.get_app(contract).address
    mbr_payment = context.any.txn.payment(
        receiver=app_address,
        amount=BOOTSTRAP_MBR - 1,
    )
    with pytest.raises(AssertionError, match="Payment must cover the app MBR"):
        contract.bootstrap(mbr_payment, asset)


def test_deposit(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)

    balance = _deposit(context, contract, asset, 1_000)
    assert balance == 1_000
    balance = _deposit(context, contract, asset, 500)
    assert balance == 1_500
    assert contract.balance[context.default_sender] == 1_500


def test_deposit_rejects_wrong_asset(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    _bootstrap(context, contract)
    _opt_in(context, contract)
    wrong_asset = context.any.asset()

    with pytest.raises(AssertionError, match="Wrong asset"):
        _deposit(context, contract, wrong_asset, 1_000)


def test_deposit_rejects_wrong_receiver(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)

    axfer = context.any.txn.asset_transfer(
        xfer_asset=asset,
        asset_receiver=context.any.account(),
        asset_amount=1_000,
    )
    with pytest.raises(AssertionError, match="Deposit must go to the app account"):
        contract.deposit(axfer)


def test_withdraw(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)
    _deposit(context, contract, asset, 1_000)

    remaining = contract.withdraw(UInt64(400))

    assert remaining == 600
    assert contract.balance[context.default_sender] == 600

    # App sent CORVID back to the caller via inner transaction.
    payout_itxn = context.txn.last_group.itxn_groups[-1][0]
    assert payout_itxn.xfer_asset == asset
    assert payout_itxn.asset_amount == 400
    assert payout_itxn.asset_receiver == context.default_sender


def test_withdraw_rejects_overdraw(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)
    _deposit(context, contract, asset, 1_000)

    with pytest.raises(AssertionError, match="Insufficient balance"):
        contract.withdraw(UInt64(1_001))


def test_vault_balance(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)

    # Not opted in: defaults to 0.
    assert contract.vault_balance() == 0

    _opt_in(context, contract)
    _deposit(context, contract, asset, 2_000)
    assert contract.vault_balance() == 2_000


# ---- relay (operator) tests ----


def test_post_as_member(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)
    recipient = context.any.account()

    ciphertext = b"sealed-envelope-bytes"
    index = _post(context, contract, asset, arc4.Address(recipient), ciphertext)

    assert index == 0
    key = _message_key(recipient.bytes.value, 0)
    assert context.ledger.box_exists(contract, key)
    assert context.ledger.get_box(contract, key) == ciphertext
    assert context.ledger.get_box(
        contract, _counter_key(recipient.bytes.value)
    ) == (1).to_bytes(8, "big")
    assert contract.message_count(arc4.Address(recipient)) == 1

    # Second message to the same recipient lands at index 1.
    index = _post(context, contract, asset, arc4.Address(recipient), b"second")
    assert index == 1
    assert contract.message_count(arc4.Address(recipient)) == 2


def test_post_rejects_non_member(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)  # opted in but zero stake

    with pytest.raises(AssertionError, match="Only staked members can post"):
        _post(context, contract, asset, arc4.Address(context.any.account()), b"hi")


def test_post_rejects_low_fee(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)

    with pytest.raises(AssertionError, match="Fee below minimum"):
        _post(
            context,
            contract,
            asset,
            arc4.Address(context.any.account()),
            b"hi",
            fee=MESSAGE_FEE - 1,
        )


def test_post_rejects_wrong_fee_asset(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)
    other_asset = context.any.asset()

    with pytest.raises(AssertionError, match="Fee must be paid in the vault asset"):
        _post(
            context,
            contract,
            asset,
            arc4.Address(context.any.account()),
            b"hi",
            fee_asset=other_asset,
        )


def test_post_rejects_low_mbr(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)
    recipient = context.any.account()
    ciphertext = b"hi"

    with pytest.raises(AssertionError, match="MBR payment too small"):
        _post(
            context,
            contract,
            asset,
            arc4.Address(recipient),
            ciphertext,
            mbr=_required_mbr(len(ciphertext), first_message=True) - 1,
        )


def test_post_rejects_oversize(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)

    with pytest.raises(AssertionError, match="Ciphertext size out of bounds"):
        _post(
            context,
            contract,
            asset,
            arc4.Address(context.any.account()),
            b"x" * (MAX_MESSAGE_SIZE + 1),
        )


def test_delete_message(context: AlgopyTestContext, contract: CorvidVault) -> None:
    asset = _bootstrap(context, contract)
    _stake(context, contract, asset)

    # Post to the caller's own inbox so the same sender can delete it.
    me = arc4.Address(context.default_sender)
    _post(context, contract, asset, me, b"for-myself")
    key = _message_key(context.default_sender.bytes.value, 0)
    assert context.ledger.box_exists(contract, key)

    contract.delete_message(UInt64(0))
    assert not context.ledger.box_exists(contract, key)

    with pytest.raises(AssertionError, match="Message not found"):
        contract.delete_message(UInt64(0))


def test_message_count_defaults_zero(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    _bootstrap(context, contract)
    assert contract.message_count(arc4.Address(context.any.account())) == 0


def test_total_staked_tracking(
    context: AlgopyTestContext, contract: CorvidVault
) -> None:
    asset = _bootstrap(context, contract)
    _opt_in(context, contract)

    assert contract.total_staked.value == 0
    _deposit(context, contract, asset, 1_000)
    assert contract.total_staked.value == 1_000
    contract.withdraw(UInt64(400))
    assert contract.total_staked.value == 600
