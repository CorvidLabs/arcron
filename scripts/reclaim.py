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

Reclaiming everything is right for a superseded app and wrong for a live one,
where the same account usually owns upkeeps it wants to keep. `--upkeep` names
the ones to cancel; without it the default is still every upkeep we created.

Run:  poetry run python -m scripts.reclaim --network testnet --app-id N
      poetry run python -m scripts.reclaim --network testnet --app-id N --upkeep 79
      poetry run python -m scripts.reclaim --network testnet --app-id N --upkeep 79 --commit
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


def select(
    decoded: list[tuple[int, keeper_bot.Upkeep, int]], requested: list[int] | None
) -> tuple[list[tuple[int, keeper_bot.Upkeep, int]], list[int]]:
    """Narrow the decoded boxes to the ids asked for, keeping box order.

    Returns the selection and any requested id no box matched, so a typo is
    reported rather than quietly cancelling nothing. No request means the
    original behaviour: every upkeep on the app, and the cancel loop drops
    the ones we did not create.
    """
    if not requested:
        return decoded, []
    wanted = set(requested)
    kept = [row for row in decoded if row[0] in wanted]
    missing = sorted(wanted - {row[0] for row in kept})
    return kept, missing


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True, help="the keeper app to drain")
    parser.add_argument(
        "--upkeep",
        type=int,
        action="append",
        metavar="ID",
        help="cancel only this upkeep id; repeat for several. Default: every one we created.",
    )
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

    decoded = []
    for box in boxes:
        name = base64.b64decode(box["name"])
        if name[:1] != b"u":
            continue
        upkeep_id = int.from_bytes(name[1:9], "big")
        raw = base64.b64decode(algod.application_box_by_name(args.app_id, name)["value"])
        upkeep = keeper_bot._decode_upkeep(upkeep_id, raw)
        mbr = BOX_FLAT_COST + BOX_BYTE_COST * (len(name) + len(raw))
        decoded.append((upkeep_id, upkeep, mbr))

    found, missing = select(decoded, args.upkeep)
    for upkeep_id in missing:
        # A live app is shared, so an id we were asked for and cannot see is
        # far more likely a typo than a race. Name it and cancel nothing.
        logger.warning(f"upkeep {upkeep_id} is not a box on app {args.app_id}; skipping it.")
    if not found:
        logger.info("Nothing selected. Nothing to reclaim.")
        return

    # Only the creator can cancel, so an upkeep somebody else created is worth
    # nothing to us however much it holds. Checked here rather than only at
    # the cancel: naming twelve upkeeps by hand used to print
    # "0.747200 ALGO comes back" and then refuse all twelve with "Only the
    # creator can cancel", because the price was computed before anyone asked
    # whose they were. A preview that overstates the refund is the one
    # direction this report must never be wrong in.
    ours = [row for row in found if row[1].creator == deployer.address]
    theirs = [row for row in found if row[1].creator != deployer.address]

    logger.info("")
    logger.info(f"{'upkeep':>24} {'target':>10} {'runs':>5} {'escrow':>10} {'box MBR':>10}")
    escrow_total = 0
    mbr_total = 0
    for upkeep_id, upkeep, mbr in ours:
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

    if theirs:
        logger.info("")
        logger.warning(
            f"{len(theirs)} of the {len(found)} selected were created by somebody else and "
            "cannot be cancelled from this account:"
        )
        by_creator: dict[str, list[int]] = {}
        for upkeep_id, upkeep, _ in theirs:
            by_creator.setdefault(upkeep.creator, []).append(upkeep_id)
        for creator, ids in sorted(by_creator.items()):
            held = sum(u.balance for _, u, _ in theirs if u.creator == creator)
            logger.warning(f"  {creator[:14]}…  {sorted(ids)}  holding {held/1e6:.6f} ALGO")
        logger.warning("  Whoever holds those keys has to cancel them.")

    if not ours:
        logger.info("")
        logger.info("Nothing here is ours to cancel.")
        return

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
