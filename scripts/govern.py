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
import subprocess
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
        logger.info("  Each holder: govern show --file <that> --app-id N   (read it first)")
        logger.info("               SIGNER_MNEMONIC=... govern sign --file <that> --app-id N")
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


# Program pages are 2,048 bytes and are shared by approval and clear. Extra
# pages are create-only: they cannot be added later by `update`, and they
# cannot be removed either, so asking for one too many costs the creator
# 100,000 microAlgos of minimum balance for as long as the app exists.
PROGRAM_PAGE = 2_048


def create(algorand, expect_creator: str, assume_yes: bool, allow_dirty: bool,
           out: 'pathlib.Path | None' = None) -> int:
    """Write an unsigned create for the multisig to sign.

    Everything an `ApplicationCreateTxn` sets is permanent. The creator cannot
    be changed, the schema cannot be resized, and extra pages can be neither
    added nor removed. There is no update path back from any of them, because
    `update` replaces code and nothing else.

    Before this existed the only worked create in the repository was
    `scripts/multisig_e2e.py`, which generates three throwaway keys, funds
    them, and drops them when the process exits. Run against MainNet it would
    produce an app whose creator nobody holds, and `govern status` would go on
    reporting that the creator can still replace the programs.
    """
    algod = algorand.client.algod

    if not ms.configured():
        logger.error(
            "Refusing: no multisig is configured, and a single-key creator is the "
            "admin-key problem this command exists to avoid. An app's creator cannot "
            "be changed afterwards. Set ARCRON_MULTISIG_ADDRESSES and "
            "ARCRON_MULTISIG_THRESHOLD."
        )
        return 1
    if ms.address() != expect_creator:
        logger.error(
            f"Refusing: the configured multisig is {ms.address()}, not the "
            f"{expect_creator} you named. Member order is part of the address, so the "
            "same keys in a different order are a different account."
        )
        return 1

    if not allow_dirty:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True
        ).stdout.strip()
        if dirty:
            logger.error(
                "Refusing: the working tree has uncommitted changes, so the digest below "
                "would not correspond to any commit anyone can check out. Commit and tag "
                "first, or pass --allow-dirty if you truly mean to."
            )
            return 1

    rebuild()
    spec = _spec("keeper")
    approval, clear = _programs(spec)

    # Read the schema rather than typing it: too small fails the create when
    # __init__ writes its second global, and too large costs the creator
    # minimum balance forever for state that is never used.
    schema = spec.get("state", {}).get("schema", {})
    g, l = schema.get("global", {}), schema.get("local", {})
    extra_pages = (len(approval) + len(clear) - 1) // PROGRAM_PAGE

    logger.info("This create is permanent in every field below.")
    logger.info(f"  creator       {ms.address()}")
    logger.info(f"  threshold     {ms.threshold()} of {len(ms.signers())}")
    for index, member in enumerate(ms.signers(), start=1):
        logger.info(f"    member {index}     {member}")
    logger.info("  Member order is part of the address. A permutation is a different account.")
    logger.info(f"  programs      {len(approval)} + {len(clear)} bytes")
    logger.info(f"  combined      sha256 {_digest(approval, clear)}")
    logger.info(f"  extra pages   {extra_pages}  (capacity {PROGRAM_PAGE * (1 + extra_pages)} bytes)")
    logger.info(f"  global state  {g.get('ints', 0)} uints, {g.get('bytes', 0)} byte slices")
    logger.info(f"  local state   {l.get('ints', 0)} uints, {l.get('bytes', 0)} byte slices")
    logger.info("  None of these can be changed later. `update` replaces code, nothing else.")

    if not assume_yes:
        answer = input(f"  Type the creator address to continue: ").strip()
        if answer != ms.address():
            logger.info("Not created.")
            return 1

    unsigned = transaction.ApplicationCreateTxn(
        sender=ms.address(),
        sp=algod.suggested_params(),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=approval,
        clear_program=clear,
        global_schema=transaction.StateSchema(g.get("ints", 0), g.get("bytes", 0)),
        local_schema=transaction.StateSchema(l.get("ints", 0), l.get("bytes", 0)),
        extra_pages=extra_pages,
    )
    target = out or pathlib.Path("arcron-create.json")
    ms.export_unsigned(unsigned, target)
    logger.info(f"Wrote {target} for {ms.describe()} to sign.")
    logger.info("  A create carries app id 0, so sign it with --app-id 0.")
    logger.info("  Each holder: govern show --file <that> --app-id 0")
    logger.info("               SIGNER_MNEMONIC=... govern sign --file <that> --app-id 0")
    logger.info("  Then anyone: govern submit --file <that> --app-id 0")
    logger.info("  Afterwards:  fund the app account's base minimum balance, then")
    logger.info("               verify_build against the new app id before anything else.")
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


# A fee is spent whether or not the transaction accomplishes anything, so an
# inflated one is a way to drain the account it is signed from without ever
# looking like theft. Ten times the minimum leaves room for real congestion.
MAX_SIGNABLE_FEE = 10_000


def _refuse(args, verb: str) -> bool:
    """Print every reason not to act on this file. True means do not.

    Collected rather than short-circuited so a holder sees all of what is
    wrong at once, and phrased as refusals so the default is inaction.
    """
    reasons = ms.refusals(
        args.file,
        app_id=args.app_id,
        genesis_ids=net.genesis_ids(args.network),
        expected_address=ms.address() if ms.configured() else None,
        max_fee=MAX_SIGNABLE_FEE if not args.allow_high_fee else 2**63,
        allow_account_txn=args.account_txn,
        allow_rekey=args.i_mean_to_rekey,
    )
    for reason in reasons:
        logger.error(f"  REFUSING: {reason}")
    if reasons:
        logger.error(f"Not {verb}ing. Check where this file came from before overriding anything.")
    return bool(reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=("status", "create", "update", "freeze", "show", "sign", "submit")
    )
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=0, help="the keeper app to act on")
    parser.add_argument(
        "--expect-creator",
        help="create: the multisig address you intend to be the creator, typed out in full",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="create: allow an uncommitted tree")
    parser.add_argument("--no-rebuild", action="store_true", help="update: trust the built artifacts")
    parser.add_argument("--yes", action="store_true", help="freeze: skip the confirmation")
    parser.add_argument(
        "--account-txn", action="store_true",
        help="sign/submit: allow a transaction that is not an application call",
    )
    parser.add_argument(
        "--i-mean-to-rekey", action="store_true",
        help="sign/submit: allow a transaction that rekeys or closes the sender",
    )
    parser.add_argument(
        "--allow-high-fee", action="store_true",
        help=f"sign/submit: allow a fee above {MAX_SIGNABLE_FEE} microAlgos",
    )
    parser.add_argument(
        "--out", type=pathlib.Path,
        help="update/freeze under a multisig: where to write the unsigned transaction",
    )
    parser.add_argument(
        "--file", type=pathlib.Path, help="sign/submit: the transaction file to act on"
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    if args.command == "create":
        if not args.expect_creator:
            logger.error(
                "create needs --expect-creator: the multisig address you intend to own this "
                "app, typed out in full. It is checked against the configured multisig, "
                "because an app's creator cannot be changed afterwards."
            )
            return 1
        return create(algorand, args.expect_creator, args.yes, args.allow_dirty, args.out)
    if args.command in ("update", "freeze", "status") and not args.app_id:
        logger.error(f"{args.command} needs --app-id")
        return 1
    if args.command == "status":
        return status(algorand, args.app_id)
    if args.command == "show":
        if args.file is None:
            logger.error("show needs --file")
            return 1
        for line in ms.describe_transaction(args.file):
            logger.info(f"  {line}")
        # Read from the blob, not from this machine's environment. The
        # environment is whatever the person running `show` has configured,
        # and a file that spends from a different account entirely would
        # otherwise be described using your threshold and your member list.
        logger.info(f"  spends from   {ms.blob_address(args.file)}")
        logger.info(f"  signatures    {ms.collected(args.file)} of {ms.blob_threshold(args.file)}")
        for index, member in enumerate(ms.blob_signers(args.file), start=1):
            logger.info(f"    member {index}     {member}")
        if ms.configured() and ms.blob_address(args.file) != ms.address():
            logger.warning(
                f"  !! This is NOT your configured multisig ({ms.address()}). "
                "Signing it binds an account you were not asked about."
            )
        return 0
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
            logger.info("About to sign:")
            for line in ms.describe_transaction(args.file):
                logger.info(f"  {line}")
            if _refuse(args, "sign"):
                return 1
            # A signature is the irreversible half: submitting is mechanical
            # once enough exist. Freeze already asks for the app id to be
            # typed back, and signing deserves the same pause, because a
            # holder who runs this from a script is not a second signer.
            if not args.yes:
                expected = str(ms.blob_address(args.file))
                answer = input(f"  Type the sending account ({expected[:8]}...) to sign: ").strip()
                if not expected.startswith(answer) or len(answer) < 8:
                    logger.info("Not signed.")
                    return 1
            have = ms.sign(args.file, secret)
            logger.info(f"Signed. {have} of {ms.blob_threshold(args.file)} collected.")
            return 0
        if _refuse(args, "submit"):
            return 1
        txid = ms.submit(algorand.client.algod, args.file)
        logger.info(f"Submitted {txid}")
        return 0
    if args.command == "update":
        return update(algorand, args.app_id, args.no_rebuild, args.out)
    return freeze(algorand, args.app_id, args.yes, args.out)


if __name__ == "__main__":
    sys.exit(main())
