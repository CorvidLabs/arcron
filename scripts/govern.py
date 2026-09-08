"""Create a keeper deployment, replace its programs, or give up the ability to.

Two of these cannot be undone, and they are not the same two.

    poetry run python -m scripts.govern status --network testnet --app-id N
    poetry run python -m scripts.govern update --network testnet --app-id N
    poetry run python -m scripts.govern freeze --network testnet --app-id N
    poetry run python -m scripts.govern create --network mainnet --expect-creator ADDR

`create` here is the unsigned-multisig variant, kept for if a wallet ever ships
multisig signing. The MainNet ceremony is `scripts/deploy.py`
(`fledge run deploy-mainnet`); see docs/deploying.md.

`create` writes an unsigned application-create for a multisig to sign. Every
field it sets is permanent: the creator, the state schema, and the number of
extra program pages. `update` replaces code and nothing else, so none of them
has a way back.

`update` compiles this tree and replaces the deployed programs with it. It
works only while the app is unfrozen and only for the account that created it.

`freeze` gives that up permanently. Nothing sets it back, and after it the only
call that could restore an update path is an update, which is refused. Read
`docs/releases.md` for when this is supposed to happen: freezing is the rc
gate, not something to do early.

Under a multisig these write a file instead of sending, and the holders sign
it wherever their keys are:

    govern show --file F --app-id N     read it before signing, always
    govern sign --file F --app-id N     refuses on any of several grounds
    govern submit --file F --app-id N

`show` describes what the file does, including every permanent field when it
is a create. `sign` refuses a file that is not an app call, names a different
app, is for another network, spends from an account that is not the configured
multisig, rekeys or closes the sender, carries a fee above the ceiling, or
carries programs that are not the ones this tree compiles to. Then it asks for
the sending account to be typed in full, with no flag to skip it: a signature
produced without a human reading the description is the thing a multisig
exists to prevent.

Whatever signs, the fee is never the node's to set. Every transaction built
here is flat at the network minimum (`bounded_params`), and a node whose
advice is above `MAX_SIGNABLE_FEE` is refused before anything is signed, with
no flag to override it on a path that signs in process.
"""

import argparse
import base64
import hashlib
import logging
import pathlib
import subprocess
import sys

import algokit_utils
from algosdk import constants, transaction

from scripts import multisig as ms, network as net
from scripts.registry_health import read_escrowed, read_solvency
from scripts.verify_build import _digest, _programs, _spec, rebuild

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# A fee is spent whether or not the transaction accomplishes anything, so an
# inflated one is a way to drain the account it is signed from without ever
# looking like theft. Ten times the minimum leaves room for real congestion.
MAX_SIGNABLE_FEE = 10_000


class FeeRefused(RuntimeError):
    """A node's fee advice was above the ceiling, and nothing was signed.

    Its own class so `main` can catch the one refusal that comes from the node
    rather than from the operator, and print it as a refusal instead of a
    traceback, without swallowing anything else.
    """


def bounded_params(algod) -> transaction.SuggestedParams:
    """Suggested params with the fee pinned to the network minimum, or a refusal.

    What bounds each path in this repository that signs (issue #250 F14; the
    inventory was taken 2026-09-08, corrected three times in review the same
    day, and `tests/test_govern.py` now greps `scripts/` for signers and fails
    if one is named in neither list below):

    Bounded:

    * `scripts/deploy.py`: the create, and the 0.1 ALGO floor payment after
      it. Both through this function.
    * `scripts/govern.py`: `update` and `freeze` when a single key signs, and
      the unsigned create/update/freeze files written for a multisig, whose
      holders' `sign` then checks the fee a second time from the file. All
      through this function.
    * `scripts/seed_registry.py --commit`: step three of the MainNet ceremony.
      Two payments and the `register` call per seed, `static_fee` on all
      three from this function; `register` issues no inner transaction.
    * `scripts/keeper_topup.py --send`: the funding payment and the `top_up`
      call, `static_fee` on both, from this function.
    * `scripts/keeper_sweep.py`: the payment that moves a keeper's earnings
      out, `static_fee` from this function; a refusal is an error-level
      `sweep_refused` event and no payment, because the bot is unattended.
    * `scripts/reclaim.py`: `cancel`, bounded with `max_fee` rather than a
      flat fee because `cancel` sends inner transactions that pooling has to
      cover; the ceiling is the same number.
    * `scripts/keeper_bot.py`: `execute`, `max_fee` for the same reason
      (`KEEPER_MAX_OUTER_FEE`, default the same 10,000), a daemon rather
      than a shell.

    Every other script under `scripts/` that signs is a LocalNet instrument
    and is unbounded by design: the end-to-ends (`keeper_e2e`, `govern_e2e`,
    `multisig_e2e`, `clawback_e2e`, `subscription_demo`), the soak and the
    race (`keeper_soak`, `keeper_race`), `scenario`, `attacks`,
    `reference_boundary`, and the spikes (`spike_asa_fee`,
    `spike_hostile_target`, `spike_js_execute_resources`, `spike_multiarg`,
    `spike_quantum`, `spike_reentrancy`, `spike_resources`,
    `spike_simulate_test_button`). Every key they hold is a throwaway;
    `keeper_e2e` also accepts `--network testnet`, which is a rehearsal
    against a public node with the throwaway key from `.env.testnet`, exactly
    the account a wrong fee is allowed to cost, and it runs `keeper_bot
    --once`, which is bounded. None of them refuses MainNet itself: they take
    `--network` from `network.add_network_argument`, whose choices include
    `mainnet`. What keeps them off it is `network.load_network`:
    `ARCRON_ALLOW_MAINNET=1`, which nothing in this repository sets, and the
    refusal of a mnemonic written into `.env.mainnet`, so running one there
    is a deliberate export of both the flag and the creator key into the same
    shell, the two acts the ceremony itself requires. A rehearsal script is
    not made safer against that by a fee bound; it is made safer by not being
    run there, which `docs/design/mainnet-rollout.md` says.

    Until 2026-09-08 everything in the bounded list except `execute` took
    `algod.suggested_params()` exactly as the node handed them over, and
    `MAX_SIGNABLE_FEE` applied only to a file a multisig holder was about to
    sign (`multisig.refusals`), which is to say to the one path where a human
    also reads the fee. The number a node
    returns in `fee` is *per byte*, and algosdk and algokit's composer both
    multiply it by the transaction's size unless the params are flat. A keeper
    create is about 2,400 bytes, measured with `estimate_size()` on this
    tree's programs (2,406 on 2026-09-08), so a node advising 10,000 per byte
    would have had about 24 ALGO paid from the creator's account, and the only
    symptom would have been the balance afterwards.

    None of the transactions that come through here needs fee pooling.
    `update()` and `freeze()` each write state and send nothing; a create runs
    `__init__`, which does the same; `register` and `top_up` move payments
    into a box; a payment is a payment. Each has no inner transaction to cover, so the
    network minimum is the right fee, and a higher suggestion is advice this
    repository has no reason to take. The fee is therefore set, flat, to the
    node's `min_fee`, and both figures the node sent are checked against the
    ceiling: a minimum above it means the node is describing a network these
    scripts do not know, and a per-byte figure above it is above the ceiling
    for any transaction at all, since none is shorter than a byte. Either one
    means stop and look, not pay. A per-byte figure below the ceiling that
    would still have cost real money (say 100, or about 0.24 ALGO on a create)
    is not paid either, because the fee is pinned rather than merely capped.

    There is deliberately no `--allow-high-fee` on these paths. `sign` has one
    because a holder sees the fee printed in the description before deciding;
    here the transaction is built and signed in one process, and nobody reads
    a fee before it is paid. If the network minimum ever genuinely rises past
    the ceiling, the constant changes in a commit anyone can read.

    The node-trust boundary, said once: a ceremony trusts the node for the
    genesis id check and for the read-back, and for nothing that costs money.
    A node's fee advice is not authorization to spend. The operator is trusted
    for the `.env` that names the node, and the node is trusted to describe
    the chain, which is a claim the read-back can catch it lying about; a fee
    is spent before anything can be checked, which is why it is not the
    node's to set.
    """
    params = algod.suggested_params()
    # algosdk leaves `min_fee` None when a fake or an old node did not send
    # one; the protocol constant is then the only honest figure. It is never
    # read from `fee`, which is the per-byte suggestion and normally 0.
    minimum = int(params.min_fee) if params.min_fee is not None else constants.MIN_TXN_FEE
    per_byte = int(params.fee)
    if minimum > MAX_SIGNABLE_FEE or per_byte > MAX_SIGNABLE_FEE:
        raise FeeRefused(
            f"the node suggests a fee of {per_byte} microAlgos per byte with a minimum "
            f"of {minimum}, and this repository signs nothing above {MAX_SIGNABLE_FEE}. "
            "A fee is spent whether or not the transaction does anything, and a node's "
            "fee advice is not authorization to spend from this account. There is no "
            "flag to override this on a path that signs in process; if the network "
            "minimum has genuinely moved, change MAX_SIGNABLE_FEE in a commit, and if "
            "it has not, find out what node ALGOD_SERVER is pointing at."
        )
    params.fee = minimum
    params.flat_fee = True
    return params


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

    # The contract charges every box its exact minimum balance and charges
    # nobody for the 0.1 ALGO the app account itself needs, so the boxes and
    # the ledger can disagree by that much for as long as nobody funds it.
    # While they do, the last executions and the last `cancel` fail at the
    # ledger with the box still saying they are payable. Measured on
    # LocalNet, 2026-09-01. `create` cannot fund an address that does not
    # exist yet, so the check belongs here, where somebody looks afterwards.
    # `read_solvency` refuses to guess the ledger's floor rather than assuming
    # the 100,000 µALGO minimum, because guessing low inflates spendable and
    # hides exactly the shortfall this line exists to show. A status command
    # should say that it cannot answer, not answer wrongly, and not fall over
    # with a traceback on the way.
    try:
        solvency = read_solvency(algod, app_id, read_escrowed(algod, app_id))
    except ValueError as refusal:
        logger.warning(f"  escrow    cannot be judged: {refusal}")
        return 0
    logger.info(f"  escrow    {solvency.escrowed / 1e6:.3f} ALGO owed, "
                f"{solvency.spendable / 1e6:.3f} ALGO spendable")
    if solvency.shortfall:
        # Anyone can fix this, with an ordinary payment and no key of the
        # creator's, which is worth saying: the person reading this is often
        # not the person holding the multisig.
        logger.warning(f"            SHORT BY {solvency.shortfall:,} uALGO. Until somebody "
                       "sends the app account at least")
        logger.warning("            that much, some upkeeps cannot be executed or cancelled.")
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

    # Bounded on both branches. Under a multisig the holders' `sign` checks the
    # fee again from the file, which is the one place a human reads it; the
    # single-key branch below signs what it builds, so this is its only check.
    params = bounded_params(algod)
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


def _create_shape(spec: dict, approval: bytes, clear: bytes) -> dict:
    """What a create fixes forever, derived from the build rather than typed.

    `create` writes these into the transaction and `sign` compares the file
    against them. One function so the writer and the check cannot drift apart:
    a second derivation that agrees today is a second derivation to keep
    agreeing.
    """
    schema = spec.get("state", {}).get("schema", {})
    g, l = schema.get("global", {}), schema.get("local", {})
    return {
        "extra pages": (len(approval) + len(clear) - 1) // PROGRAM_PAGE,
        "global uints": int(g.get("ints", 0)),
        "global byte slices": int(g.get("bytes", 0)),
        "local uints": int(l.get("ints", 0)),
        "local byte slices": int(l.get("bytes", 0)),
    }


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
        # cwd matters: run from anywhere else and this reports on a different
        # repository, or none, and a dirty tree looks clean. `verify_build`
        # already passes it; this did not.
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=pathlib.Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
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
    extra_pages = _create_shape(spec, approval, clear)["extra pages"]

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
        # Flat, at the minimum. The holders' `sign` would refuse a fee above
        # the ceiling anyway, but a file that carries one is a file somebody
        # has to explain, and the node's per-byte advice is not what a create
        # should cost.
        sp=bounded_params(algod),
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
    # Fetched before the operator is asked to type anything, so a node whose
    # fee advice is refused is refused before the confirmation, not after it.
    params = bounded_params(algod)
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
            sender=ms.address(), sp=params, index=app_id,
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
    # The app client computes a fee of its own from the node's params, so it
    # is told the fee instead: `static_fee` is algokit's spelling of a flat
    # fee, and `freeze()` sends no inner transaction for pooling to cover.
    client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method="freeze",
            static_fee=algokit_utils.AlgoAmount(micro_algo=params.fee),
        )
    )
    if _frozen(algod, app_id) != 1:
        logger.error("freeze did not take. Investigate before announcing anything.")
        return 1
    logger.info(f"Frozen. App {app_id} is now permanently {digest}.")
    return 0


def _spec_json():
    import json

    return algokit_utils.Arc56Contract.from_json(json.dumps(_spec("keeper")))


def _refuse(args, verb: str) -> bool:
    """Print every reason not to act on this file. True means do not.

    Collected rather than short-circuited so a holder sees all of what is
    wrong at once, and phrased as refusals so the default is inaction.
    """
    # Recompute from this tree so a file carrying programs is checked rather
    # than merely described. Skipped when the file carries none, and when the
    # signer explicitly opted out of rebuilding.
    expected_digest = None
    expected_create = None
    carried = ms.carried_programs(args.file)
    if carried is not None:
        if args.no_rebuild:
            # Skipping the comparison must announce itself. Silence here looks
            # exactly like a comparison that passed, which is the reasoning
            # already written down a few lines away for an unconfigured
            # machine and not applied to the field it was written for.
            logger.error(
                "  REFUSING: --no-rebuild was passed and this file carries programs, so "
                "nothing compared them against this tree. That is the only check standing "
                "between a holder and hostile bytecode. Drop the flag, or check out the "
                "commit this was built from and drop the flag."
            )
            return True
        rebuild()
        spec = _spec("keeper")
        programs = _programs(spec)
        expected_digest = _digest(*programs)
        expected_create = _create_shape(spec, *programs)

    reasons = ms.refusals(
        args.file,
        app_id=args.app_id,
        genesis_ids=net.genesis_ids(args.network),
        expected_address=ms.address() if ms.configured() else None,
        expected_digest=expected_digest,
        expected_create=expected_create,
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
    parser.add_argument("--app-id", type=int, default=None, help="the keeper app to act on; 0 means a create")
    parser.add_argument(
        "--expect-creator",
        help="create: the multisig address you intend to be the creator, typed out in full",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="create: allow an uncommitted tree")
    parser.add_argument("--no-rebuild", action="store_true", help="update only: trust the built artifacts. Refused on sign of a file carrying programs")
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
    try:
        return _dispatch(args, algorand)
    except FeeRefused as refusal:
        # The one refusal that comes from the node rather than the operator.
        # Nothing has been signed when it is raised, so it is a refusal and
        # not a failure, and it is printed as one.
        logger.error(f"Refusing: {refusal}")
        return 1


def _dispatch(args, algorand) -> int:
    if args.command == "create":
        if not args.expect_creator:
            logger.error(
                "create needs --expect-creator: the multisig address you intend to own this "
                "app, typed out in full. It is checked against the configured multisig, "
                "because an app's creator cannot be changed afterwards."
            )
            return 1
        return create(algorand, args.expect_creator, args.yes, args.allow_dirty, args.out)
    if args.command in ("update", "freeze", "status", "sign", "submit", "show") and args.app_id is None:
        logger.error(
            f"{args.command} needs --app-id. It has no default: 0 means \"this is a create\", "
            "and a forgotten flag must not be able to look like the one transaction that "
            "cannot be undone. Pass --app-id 0 deliberately when signing a create."
        )
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
            # No --yes escape here. `freeze` has one because the coordinator
            # runs it after the holders have already signed; this is the
            # holders' own step, and a signature produced without a human
            # reading the description is the failure the multisig exists to
            # prevent. A holder who scripts past this is not a second signer.
            expected = str(ms.blob_address(args.file))
            answer = input("  Type the full sending account to sign: ").strip()
            if answer != expected:
                logger.info("Not signed. The account typed did not match the file's sender.")
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
