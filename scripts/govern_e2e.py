"""The governance lifecycle, on a real chain: update while unfrozen, then not.

This is the whole promise of `upgradeable until frozen`, so it is proved
against an AVM rather than a mock: the mock does not enforce OnCompletion
routing the way the network does, and the routing is the mechanism.

Run:  poetry run python -m scripts.govern_e2e [--network localnet]
"""

import argparse
import base64
import logging

import algokit_utils
from algosdk import transaction
from algosdk.abi import Method

from scripts import network as net
from scripts.govern import _deployed, _frozen
from scripts.keeper_e2e import _assert, _quiet
from smart_contracts.artifacts.keeper.keeper_client import KeeperFactory
from scripts.verify_build import _digest, _programs, _spec

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _selector(signature: str) -> bytes:
    return Method.from_signature(signature).get_selector()


def _send_update(algorand, deployer, app_id: int, approval: bytes, clear: bytes) -> None:
    """A raw ApplicationUpdateTxn, so the contract's routing is what is tested."""
    algod = algorand.client.algod
    signed = transaction.ApplicationUpdateTxn(
        sender=deployer.address,
        sp=algod.suggested_params(),
        index=app_id,
        approval_program=approval,
        clear_program=clear,
        app_args=[_selector("update()void")],
    ).sign(deployer.private_key)
    transaction.wait_for_confirmation(algod, algod.send_transaction(signed), 6)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")
    # A fresh app every run. The shared deploy config finds an existing
    # deployment and reuses it, and this test freezes what it is given, so
    # reusing would mean the second run starts frozen and fails at stage 1.
    keeper, _ = algorand.client.get_typed_app_factory(
        KeeperFactory, default_sender=deployer.address
    ).send.create.bare()
    app_id = keeper.app_id
    approval, clear = _programs(_spec("keeper"))

    logger.info("── 1. A fresh deployment is unfrozen ──")
    _assert("frozen", _frozen(algod, app_id), 0)

    logger.info("── 2. The creator can replace the programs ──")
    _send_update(algorand, deployer, app_id, approval, clear)
    live_approval, live_clear = _deployed(algod, app_id)
    # Compare digests, not the programs themselves: asserting on two kilobytes
    # of bytecode makes a failure unreadable.
    _assert("deployed digest", _digest(live_approval, live_clear), _digest(approval, clear))
    logger.info("   An update landed while unfrozen.")

    logger.info("── 3. Nobody else can ──")
    stranger = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=stranger.address,
            amount=algokit_utils.AlgoAmount(micro_algo=1_000_000),
        )
    )
    refused = False
    with _quiet():
        try:
            _send_update(algorand, stranger, app_id, approval, clear)
        except Exception:
            refused = True
    _assert("a stranger's update is refused", refused, True)

    logger.info("── 4. Freezing is one way ──")
    keeper.send.freeze()
    _assert("frozen", _frozen(algod, app_id), 1)
    refused = False
    with _quiet():
        try:
            keeper.send.freeze()
        except Exception:
            refused = True
    _assert("freezing twice is refused", refused, True)

    logger.info("── 5. And after it, the creator cannot update either ──")
    refused = False
    with _quiet():
        try:
            _send_update(algorand, deployer, app_id, approval, clear)
        except Exception:
            refused = True
    _assert("the creator's update is now refused", refused, True)

    logger.info("── 6. Delete is refused, frozen or not ──")
    # Deleting an app holding escrow would strand every microAlgo in it, so
    # there has never been a path to it. Worth proving against a real node
    # rather than reading the router: the mock does not run the generated
    # OnCompletion routing at all, so no unit test can see this.
    refused = False
    with _quiet():
        try:
            signed = transaction.ApplicationDeleteTxn(
                sender=deployer.address, sp=algod.suggested_params(), index=app_id
            ).sign(deployer.private_key)
            transaction.wait_for_confirmation(algod, algod.send_transaction(signed), 6)
        except Exception:
            refused = True
    _assert("DeleteApplication is refused", refused, True)

    logger.info("── 7. Ordinary methods cannot be reached with another OnCompletion ──")
    # Adding UpdateApplication routing for `update` must not have opened the
    # other methods to anything but NoOp.
    for name, oc in (
        ("OptIn", transaction.OnComplete.OptInOC),
        ("CloseOut", transaction.OnComplete.CloseOutOC),
    ):
        refused = False
        with _quiet():
            try:
                signed = transaction.ApplicationCallTxn(
                    sender=deployer.address, sp=algod.suggested_params(), index=app_id,
                    on_complete=oc, app_args=[_selector("execute(uint64)uint64"), (0).to_bytes(8, "big")],
                ).sign(deployer.private_key)
                transaction.wait_for_confirmation(algod, algod.send_transaction(signed), 6)
            except Exception:
                refused = True
        _assert(f"execute via {name} is refused", refused, True)

    logger.info("── 8. The registry still works, frozen ──")
    _assert("frozen app still readable", _frozen(algod, app_id), 1)
    logger.info("   Freezing removes the update path and nothing else.")

    logger.info("")
    logger.info("Governance e2e passed.")
    logger.info(f"  App {app_id} is now permanently the programs it holds.")


if __name__ == "__main__":
    main()
