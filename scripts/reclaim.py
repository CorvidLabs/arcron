"""Cancel every upkeep this account created on an app, and take the escrow back.

A superseded deployment still holds real money: the escrow left in each upkeep,
plus the box minimum balance the app account is locked into while the box
exists. Both come back only by cancelling, and only the creator of an upkeep
can cancel it, so this reclaims exactly the ones we own and leaves anyone
else's alone.

Cancelling is also the only way to stop paying attention to an old app. An
upkeep on a superseded deployment is not harmless: a keeper watching that app
will keep executing it, so it keeps spending escrow on calls to a target
nobody is looking at any more.

Run:  poetry run python -m scripts.reclaim --network testnet --app-id N
      poetry run python -m scripts.reclaim --network testnet --app-id N --commit
"""

import argparse
import base64
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, KeeperClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# What one box costs the app account while it exists: the per-box constant plus
# four hundred microalgo for every byte of name and value. Cancelling frees it.
BOX_BYTE_COST = 400
BOX_FLAT_COST = 2_500


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True, help="the keeper app to drain")
    parser.add_argument(
        "--commit", action="store_true", help="actually cancel; otherwise price it and stop"
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")

    client = KeeperClient(
        algorand=algorand, app_id=args.app_id,
        default_sender=deployer.address, default_signer=deployer.signer,
    )

    boxes = algod.application_boxes(args.app_id).get("boxes", [])
    if not boxes:
        logger.info(f"app {args.app_id} has no upkeeps. Nothing to reclaim.")
        return

    logger.info("")
    logger.info(f"{'upkeep':>24} {'target':>10} {'runs':>5} {'escrow':>10} {'box MBR':>10}")
    found = []
    escrow_total = 0
    mbr_total = 0
    for box in boxes:
        name = base64.b64decode(box["name"])
        if name[:1] != b"u":
            continue
        upkeep_id = int.from_bytes(name[1:9], "big")
        raw = base64.b64decode(algod.application_box_by_name(args.app_id, name)["value"])
        upkeep = keeper_bot._decode_upkeep(upkeep_id, raw)
        mbr = BOX_FLAT_COST + BOX_BYTE_COST * (len(name) + len(raw))
        found.append((upkeep_id, upkeep, mbr))
        escrow_total += upkeep.balance
        mbr_total += mbr
        logger.info(
            f"{upkeep_id:>24} {upkeep.target_app:>10} {upkeep.times_executed:>5} "
            f"{upkeep.balance/1e6:>9.6f}A {mbr/1e6:>9.6f}A"
        )
    logger.info(
        f"{'':>24} {'':>10} {'':>5} {escrow_total/1e6:>9.6f}A {mbr_total/1e6:>9.6f}A"
    )
    logger.info("")
    logger.info(f"{(escrow_total + mbr_total)/1e6:.6f} ALGO comes back if every cancel succeeds.")

    if not args.commit:
        logger.info("Priced only. Pass --commit to cancel.")
        return

    logger.info("")
    reclaimed = 0
    skipped = []
    for upkeep_id, upkeep, mbr in found:
        try:
            response = client.send.cancel(
                args=CancelArgs(upkeep_id=upkeep_id),
                # cancel refunds by inner payment, and an upkeep holding an ASA
                # bonus sends a second inner transfer. The group has to carry
                # the fee for both, since the app account pays neither.
                params=algokit_utils.CommonAppCallParams(
                    extra_fee=algokit_utils.AlgoAmount(
                        micro_algo=2_000 if upkeep.asset_balance else 1_000
                    )
                ),
            )
        except Exception as error:
            reason = str(error).splitlines()[0]
            skipped.append((upkeep_id, reason))
            logger.warning(f"  {upkeep_id}: skipped, {reason}")
            continue
        refund = response.abi_return or 0
        reclaimed += refund
        logger.info(f"  {upkeep_id}: cancelled, {refund/1e6:.6f} ALGO refunded")

    info = algod.account_info(client.app_address)
    logger.info("")
    logger.info(f"Refunded to {deployer.address[:8]}…: {reclaimed/1e6:.6f} ALGO")
    logger.info(f"App account now: {info['amount']/1e6:.6f} ALGO, min-balance {info['min-balance']/1e6:.6f}")
    for upkeep_id, reason in skipped:
        # Only the creator can cancel, so someone else's upkeep is the expected
        # reason. Anything else is a real failure and should not read like one.
        expected = "Only the creator can cancel" in reason
        note = "not ours" if expected else "FAILED"
        logger.info(f"  {upkeep_id}: {note}, {reason}")


if __name__ == "__main__":
    main()
