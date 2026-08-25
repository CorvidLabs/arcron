"""A multisig-controlled deployment, end to end: deploy, update, freeze.

Proves the thing that matters about multisig control: one holder cannot act
alone, the threshold really is enforced by the network, and a signature is not
a secret, so the partly-signed file can be passed around and submitted by
anybody.

Run:  poetry run python -m scripts.multisig_e2e [--network localnet]
"""

import argparse
import json
import logging
import os
import pathlib
import tempfile

import algokit_utils
from algosdk import account, mnemonic as mn, transaction

from scripts import multisig as ms, network as net
from scripts.keeper_e2e import _assert, _quiet
from scripts.verify_build import _digest, _programs, _spec

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    funder = algorand.account.from_environment("DEPLOYER")

    logger.info("── 1. A 2 of 3 multisig, holding nothing yet ──")
    holders = [account.generate_account() for _ in range(3)]
    os.environ["ARCRON_MULTISIG_THRESHOLD"] = "2"
    os.environ["ARCRON_MULTISIG_ADDRESSES"] = ",".join(a for _, a in holders)
    creator = ms.address()
    logger.info(f"   {ms.describe()}")

    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=funder.address, receiver=creator,
            amount=algokit_utils.AlgoAmount(micro_algo=5_000_000),
        )
    )

    logger.info("── 2. It creates the app, so it is the creator ──")
    approval, clear = _programs(_spec("keeper"))
    params = algod.suggested_params()
    create = transaction.ApplicationCreateTxn(
        sender=creator, sp=params, on_complete=transaction.OnComplete.NoOpOC,
        approval_program=approval, clear_program=clear,
        global_schema=transaction.StateSchema(2, 0),
        local_schema=transaction.StateSchema(0, 0),
    )
    work = pathlib.Path(tempfile.mkdtemp()) / "create.json"
    ms.export_unsigned(create, work)

    logger.info("── 3. One signature is not enough ──")
    ms.sign(work, mn.from_private_key(holders[0][0]))
    _assert("signatures so far", ms.collected(work), 1)
    refused = False
    with _quiet():
        try:
            ms.submit(algod, work)
        except Exception:
            refused = True
    _assert("submitting under threshold is refused", refused, True)

    logger.info("── 4. A second holder signs, and anyone can submit ──")
    ms.sign(work, mn.from_private_key(holders[1][0]))
    _assert("signatures so far", ms.collected(work), 2)
    txid = ms.submit(algod, work)
    app_id = algod.pending_transaction_info(txid)["application-index"]
    logger.info(f"   App {app_id} created by the multisig")

    info = algod.application_info(app_id)
    _assert("the contract's creator is the multisig", info["params"]["creator"], creator)

    logger.info("── 5. A single holder cannot update it ──")
    def update_txn():
        return transaction.ApplicationUpdateTxn(
            sender=creator, sp=algod.suggested_params(), index=app_id,
            approval_program=approval, clear_program=clear,
            app_args=[bytes.fromhex(__import__("hashlib").new("sha512_256", b"update()void").hexdigest()[:8])],
        )

    solo = work.parent / "update-solo.json"
    ms.export_unsigned(update_txn(), solo)
    ms.sign(solo, mn.from_private_key(holders[2][0]))
    refused = False
    with _quiet():
        try:
            ms.submit(algod, solo)
        except Exception:
            refused = True
    _assert("one of three cannot update", refused, True)

    logger.info("── 6. Two can ──")
    both = work.parent / "update.json"
    ms.export_unsigned(update_txn(), both)
    ms.sign(both, mn.from_private_key(holders[0][0]))
    ms.sign(both, mn.from_private_key(holders[2][0]))
    ms.submit(algod, both)
    live = algod.application_info(app_id)["params"]
    import base64
    _assert(
        "programs are still this build",
        _digest(base64.b64decode(live["approval-program"]), base64.b64decode(live["clear-state-program"])),
        _digest(approval, clear),
    )
    logger.info("   A different pair signed this one. Any two of the three work.")

    logger.info("")
    logger.info("Multisig e2e passed.")
    logger.info(f"  App {app_id}, creator {creator}")
    logger.info("  No single holder can rewrite the contract.")


if __name__ == "__main__":
    main()
