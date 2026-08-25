"""Replace a keeper deployment's programs, or give up the ability to.

Two commands, one of which cannot be undone.

    poetry run python -m scripts.govern update --network testnet --app-id N
    poetry run python -m scripts.govern freeze --network testnet --app-id N
    poetry run python -m scripts.govern status --network testnet --app-id N

`update` compiles this tree and replaces the deployed programs with it. It
works only while the app is unfrozen and only for the account that created it.

`freeze` gives that up permanently. Nothing sets it back, and after it the only
call that could restore an update path is an update, which is refused. Read
`docs/releases.md` for when this is supposed to happen: freezing is the rc
gate, not something to do early.

Both refuse to guess. `--app-id` is required, the network is checked against
the node's genesis id, and `freeze` asks for confirmation unless `--yes`.
"""

import argparse
import base64
import hashlib
import logging
import pathlib
import sys

import algokit_utils
from algosdk import transaction

from scripts import multisig as ms, network as net
from scripts.verify_build import _digest, _programs, _spec, rebuild

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _frozen(algod, app_id: int) -> int:
    """Read `frozen` from global state. Returns 0, 1, or -1 if absent."""
    info = algod.application_info(app_id)
    for entry in info["params"].get("global-state", []):
        if base64.b64decode(entry["key"]) == b"frozen":
            return int(entry["value"].get("uint", 0))
    return -1


def _deployed(algod, app_id: int) -> tuple[bytes, bytes]:
    info = algod.application_info(app_id)
    return (
        base64.b64decode(info["params"]["approval-program"]),
        base64.b64decode(info["params"]["clear-state-program"]),
    )


def status(algorand, app_id: int) -> int:
    algod = algorand.client.algod
    frozen = _frozen(algod, app_id)
    creator = algod.application_info(app_id)["params"].get("creator")
    approval, clear = _deployed(algod, app_id)
    logger.info(f"app {app_id}")
    logger.info(f"  creator   {creator}")
    logger.info(f"  approval  {len(approval):>5} bytes")
    logger.info(f"  combined  sha256 {_digest(approval, clear)}")
    if frozen < 0:
        logger.info("  frozen    absent: this app predates the freeze flag and has no update path")
    elif frozen == 1:
        logger.info("  frozen    1: the programs can never be replaced")
    else:
        logger.info("  frozen    0: the creator can still replace the programs")
        logger.info("            Anyone escrowing here is trusting that they will not.")
    return 0


def update(algorand, app_id: int, no_rebuild: bool, out: 'pathlib.Path | None' = None) -> int:
    algod = algorand.client.algod
    if _frozen(algod, app_id) != 0:
        logger.error("Refusing: this app is frozen, or has no freeze flag at all.")
        return 1

    if not no_rebuild:
        rebuild()
    spec = _spec("keeper")
    approval, clear = _programs(spec)
    live_approval, live_clear = _deployed(algod, app_id)
    if _digest(approval, clear) == _digest(live_approval, live_clear):
        logger.info("Deployed programs already match this tree. Nothing to do.")
        return 0

    logger.info(f"  deployed  sha256 {_digest(live_approval, live_clear)}  {len(live_approval)} bytes")
    logger.info(f"  this tree sha256 {_digest(approval, clear)}  {len(approval)} bytes")

    params = algod.suggested_params()
    if ms.configured():
        # No single machine should be able to rewrite a live contract, so the
        # transaction is written out for the holders to sign wherever their
        # keys are. A signature is not a secret; the file can be passed around.
        sender = ms.address()
        unsigned = transaction.ApplicationUpdateTxn(
            sender=sender, sp=params, index=app_id,
            approval_program=approval, clear_program=clear,
            app_args=[bytes.fromhex(hashlib.new("sha512_256", b"update()void").hexdigest()[:8])],
        )
        target = out or pathlib.Path(f"arcron-update-{app_id}.json")
        ms.export_unsigned(unsigned, target)
        logger.info(f"Wrote {target} for {ms.describe()} to sign.")
        logger.info("  Each holder: SIGNER_MNEMONIC=... govern sign --file <that> --app-id N")
        logger.info("  Then anyone: govern submit --file <that> --app-id N")
        return 0

    deployer = algorand.account.from_environment("DEPLOYER")
    signed = transaction.ApplicationUpdateTxn(
        sender=deployer.address,
        sp=params,
        index=app_id,
        approval_program=approval,
        clear_program=clear,
        app_args=[bytes.fromhex(hashlib.new("sha512_256", b"update()void").hexdigest()[:8])],
    ).sign(deployer.private_key)
    txid = algod.send_transaction(signed)
    transaction.wait_for_confirmation(algod, txid, 6)
    logger.info(f"Updated app {app_id} in {txid}")

    live_approval, live_clear = _deployed(algod, app_id)
    if _digest(approval, clear) != _digest(live_approval, live_clear):
        logger.error("The deployed programs do not match what was sent. Investigate.")
        return 1
    logger.info("Verified: the deployed app is now this source, byte for byte.")
    return 0


def freeze(algorand, app_id: int, assume_yes: bool, out: 'pathlib.Path | None' = None) -> int:
    algod = algorand.client.algod
    frozen = _frozen(algod, app_id)
    if frozen < 0:
        logger.error("This app has no freeze flag; there is nothing to give up.")
        return 1
    if frozen == 1:
        logger.info("Already frozen.")
        return 0

    approval, clear = _deployed(algod, app_id)
    digest = _digest(approval, clear)
    logger.info(f"About to freeze app {app_id} permanently.")
    logger.info(f"  It will be stuck with sha256 {digest} forever.")
    logger.info("  A bug in these programs could then only be answered by telling")
    logger.info("  every creator to cancel. There is no undo.")
    if not assume_yes:
        answer = input(f"  Type the app id ({app_id}) to confirm: ").strip()
        if answer != str(app_id):
            logger.info("Not frozen.")
            return 1

    if ms.configured():
        selector = bytes.fromhex(hashlib.new("sha512_256", b"freeze()void").hexdigest()[:8])
        unsigned = transaction.ApplicationCallTxn(
            sender=ms.address(), sp=algod.suggested_params(), index=app_id,
            on_complete=transaction.OnComplete.NoOpOC, app_args=[selector],
        )
        target = out or pathlib.Path(f"arcron-freeze-{app_id}.json")
        ms.export_unsigned(unsigned, target)
        logger.info(f"Wrote {target} for {ms.describe()} to sign.")
        logger.info("  This is the one that cannot be undone. Read it before signing.")
        return 0

    deployer = algorand.account.from_environment("DEPLOYER")
    client = algorand.client.get_app_client_by_id(
        app_spec=_spec_json(), app_id=app_id, default_sender=deployer.address
    )
    client.send.call(algokit_utils.AppClientMethodCallParams(method="freeze"))
    if _frozen(algod, app_id) != 1:
        logger.error("freeze did not take. Investigate before announcing anything.")
        return 1
    logger.info(f"Frozen. App {app_id} is now permanently {digest}.")
    return 0


def _spec_json():
    import json

    return algokit_utils.Arc56Contract.from_json(json.dumps(_spec("keeper")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("status", "update", "freeze", "sign", "submit"))
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True, help="the keeper app to act on")
    parser.add_argument("--no-rebuild", action="store_true", help="update: trust the built artifacts")
    parser.add_argument("--yes", action="store_true", help="freeze: skip the confirmation")
    parser.add_argument(
        "--out", type=pathlib.Path,
        help="update/freeze under a multisig: where to write the unsigned transaction",
    )
    parser.add_argument(
        "--file", type=pathlib.Path, help="sign/submit: the transaction file to act on"
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    if args.command == "status":
        return status(algorand, args.app_id)
    if args.command in ("sign", "submit"):
        if args.file is None:
            logger.error(f"{args.command} needs --file")
            return 1
        if args.command == "sign":
            import os

            secret = os.environ.get("SIGNER_MNEMONIC")
            if not secret:
                logger.error(
                    "Set SIGNER_MNEMONIC to the mnemonic of one of the signers. "
                    "It is read from the environment and never written anywhere."
                )
                return 1
            have = ms.sign(args.file, secret)
            logger.info(f"Signed. {have} of {ms.threshold()} signatures collected.")
            return 0
        txid = ms.submit(algorand.client.algod, args.file)
        logger.info(f"Submitted {txid}")
        return 0
    if args.command == "update":
        return update(algorand, args.app_id, args.no_rebuild, args.out)
    return freeze(algorand, args.app_id, args.yes, args.out)


if __name__ == "__main__":
    sys.exit(main())
