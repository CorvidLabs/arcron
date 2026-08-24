"""End-to-end smoke test for CorvidVault on LocalNet.

Deploys (idempotently), then exercises the full flow with the DEPLOYER
account: app opt-in, CORVID deposit, partial withdraw, balance checks —
followed by the operator relay: an AlgoChat-sealed envelope is posted to a
recipient inbox, read back from its box, decrypted, and deleted.

Run with LocalNet up:  poetry run python -m scripts.smoke_localnet
"""

import base64
import logging

import algokit_utils
from algosdk import account, encoding

from smart_contracts.artifacts.corvid_vault.corvid_vault_client import (
    DeleteMessageArgs,
    DepositArgs,
    MessageCountArgs,
    PostArgs,
    WithdrawArgs,
)
from smart_contracts.corvid_vault.deploy_config import deploy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEPOSIT_AMOUNT = 1_000_000  # 1 CORVID (6 decimals)
WITHDRAW_AMOUNT = 400_000  # 0.4 CORVID

# Keep in sync with contract.py (not exported via the ABI spec).
MESSAGE_FEE = 100_000

MESSAGE = "gm — sealed by AlgoChat, relayed by the vault"


def _box_mbr(size: int, first_message: bool) -> int:
    """Mirror of the box MBR formula in contract.py's post()."""
    mbr = 2_500 + 400 * (41 + size)
    if first_message:
        mbr += 2_500 + 400 * 41
    return mbr


def _message_box_name(address: str, index: int) -> bytes:
    return b"m" + encoding.decode_address(address) + index.to_bytes(8, "big")


def main() -> None:
    app_client, asset_id = deploy()
    algorand = algokit_utils.AlgorandClient.from_environment()
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    logger.info(
        f"Vault app {app_client.app_id}, CORVID ASA {asset_id}, user {deployer.address}"
    )

    # --- Vault flow -------------------------------------------------------

    # Opt into the app (skip if already opted in from a previous run).
    try:
        start_balance: int | None = app_client.state.local_state(
            deployer.address
        ).balance
    except Exception:
        start_balance = None
    if start_balance is None:
        app_client.send.opt_in.opt_in_to_application()
        start_balance = 0
        logger.info("Opted into the app")
    else:
        logger.info(f"Already opted in, balance {start_balance}")

    # Deposit CORVID via grouped asset transfer + app call.
    axfer = algorand.create_transaction.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=deployer.address,
            receiver=app_client.app_address,
            asset_id=asset_id,
            amount=DEPOSIT_AMOUNT,
        )
    )
    response = app_client.send.deposit(args=DepositArgs(axfer=axfer))
    assert response.abi_return == start_balance + DEPOSIT_AMOUNT, response.abi_return
    logger.info(f"Deposited {DEPOSIT_AMOUNT}, balance {response.abi_return}")

    # Withdraw part of it; cover the inner payout txn fee via fee pooling.
    response = app_client.send.withdraw(
        args=WithdrawArgs(amount=WITHDRAW_AMOUNT),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    expected = start_balance + DEPOSIT_AMOUNT - WITHDRAW_AMOUNT
    assert response.abi_return == expected, response.abi_return
    logger.info(f"Withdrew {WITHDRAW_AMOUNT}, balance {response.abi_return}")

    # Read-only view and raw local state must agree.
    response = app_client.send.vault_balance()
    assert response.abi_return == expected, response.abi_return
    on_chain = app_client.state.local_state(deployer.address).balance
    assert on_chain == expected, on_chain
    logger.info("Vault flow passed")

    # --- Operator relay: AlgoChat-sealed envelope round-trip --------------

    from algochat import (
        decode_envelope,
        decrypt_message,
        derive_keys_from_seed,
        encode_envelope,
        encrypt_message,
    )

    # Recipient: a fresh account; its AlgoChat identity is derived from the
    # same seed as its Algorand account, as the protocol intends.
    recipient_pk, recipient_addr = account.generate_account()
    recipient_seed = base64.b64decode(recipient_pk)[:32]
    recipient_private, recipient_public = derive_keys_from_seed(recipient_seed)

    # Sender encryption identity (locally generated for this demo; in a real
    # client it would be derived from the sender's own account seed).
    sender_pk, _ = account.generate_account()
    sender_private, sender_public = derive_keys_from_seed(
        base64.b64decode(sender_pk)[:32]
    )

    envelope = encrypt_message(
        MESSAGE, sender_private, sender_public, recipient_public
    )
    sealed = encode_envelope(envelope)
    assert len(sealed) <= 1024, len(sealed)
    logger.info(f"Sealed envelope: {len(sealed)} bytes")

    # Post: grouped MBR payment + CORVID fee + app call. No inner txns here.
    mbr_payment = algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=app_client.app_address,
            amount=algokit_utils.AlgoAmount(
                micro_algo=_box_mbr(len(sealed), first_message=True)
            ),
        )
    )
    fee_payment = algorand.create_transaction.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=deployer.address,
            receiver=app_client.app_address,
            asset_id=asset_id,
            amount=MESSAGE_FEE,
        )
    )
    response = app_client.send.post(
        args=PostArgs(
            mbr_payment=mbr_payment,
            fee_payment=fee_payment,
            recipient=recipient_addr,
            ciphertext=sealed,
        )
    )
    assert response.abi_return == 0, response.abi_return
    logger.info(f"Posted envelope to {recipient_addr[:12]}… at inbox index 0")

    # Read the inbox box straight from algod (free query, no transaction).
    box_name = _message_box_name(recipient_addr, 0)
    box = algod.application_box_by_name(app_client.app_id, box_name)
    delivered = base64.b64decode(box["value"])
    assert delivered == sealed, "box content mismatch"

    # Recipient decrypts.
    decrypted = decrypt_message(
        decode_envelope(delivered), recipient_private, recipient_public
    )
    assert decrypted is not None and decrypted.text == MESSAGE
    logger.info(f"Recipient decrypted: {decrypted.text!r}")

    # Deletion: post to the deployer's own inbox, then delete it. Indices are
    # append-only, so use the index the post returns (not always 0), and only
    # fund the counter box MBR on the inbox's first-ever message.
    count_response = app_client.send.message_count(
        args=MessageCountArgs(recipient=deployer.address)
    )
    first_message = count_response.abi_return == 0
    self_mbr = algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=app_client.app_address,
            amount=algokit_utils.AlgoAmount(
                micro_algo=_box_mbr(len(sealed), first_message=first_message)
            ),
        )
    )
    self_fee = algorand.create_transaction.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=deployer.address,
            receiver=app_client.app_address,
            asset_id=asset_id,
            amount=MESSAGE_FEE,
        )
    )
    response = app_client.send.post(
        args=PostArgs(
            mbr_payment=self_mbr,
            fee_payment=self_fee,
            recipient=deployer.address,
            ciphertext=sealed,
        )
    )
    self_index = response.abi_return
    app_client.send.delete_message(args=DeleteMessageArgs(index=self_index))
    try:
        algod.application_box_by_name(
            app_client.app_id, _message_box_name(deployer.address, self_index)
        )
        raise AssertionError("box should be deleted")
    except Exception as exc:
        assert "should be deleted" not in str(exc), exc
    logger.info("Deleted envelope from own inbox; box reclaimed")

    logger.info(f"Smoke test passed. Final vault balance: {expected} µCORVID")


if __name__ == "__main__":
    main()
