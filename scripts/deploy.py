"""Create a keeper deployment, and check everything permanent about it first.

    poetry run python -m scripts.deploy --network localnet
    poetry run python -m scripts.deploy --network testnet
    ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.deploy --network mainnet

An application create fixes four things forever: the creator, the number of
extra program pages, the global state schema and the local state schema.
`update` replaces code and nothing else, so none of them has a way back. This
script exists to make each of them a checked fact before anything is signed,
rather than a log line after, and to read every one of them back from the
chain once the app exists.

In order:

1. Rebuild from source, so what is deployed is this tree and not an artifact.
2. Refuse a dirty tree, so the digest corresponds to a commit anyone can check
   out. On MainNet, refuse an untagged commit too: the release row in
   `docs/releases.md` names a tag, and a create is what earns one.
3. On MainNet, refuse any creator but `corvid.algo`, and refuse if the creator
   mnemonic has been written into `.env.mainnet`. It is exported into the
   ceremony's shell and never lands on disk. `scripts.network.load_network`
   enforces the same rule for every script that reaches MainNet, so the
   refusal here is the ceremony saying it in its own list rather than the only
   place it is said.
4. Refuse if this creator already has a keeper on this network. An earlier
   version of this script used algokit's deploy, which finds an existing app
   through the indexer and quietly creates a second one when the indexer is
   behind. MainNet gets exactly one; a second is a mistake with a permanent
   minimum balance attached, so there it cannot be overridden at all.
5. Print every permanent field, derived from the build and not typed, and make
   the operator type the creator address back. On MainNet there is no flag to
   skip that.
6. Simulate the create, then send the same signed bytes.
7. Fund the app account's 0.1 ALGO floor, then read the creator, extra pages,
   schema, programs and `frozen` back from the chain and compare each to what
   was promised. A mismatch at that point is shouted, because the app exists.

`--with-pulse` creates the demo target the same way: directly, checked against
its own spec, refused if the creator already has one, read back afterwards. No
indexer is consulted for either app.

`smart_contracts/keeper/deploy_config.py` is the algokit path and serves the
LocalNet end-to-end; it refuses MainNet outright now that this exists.
`govern create` is the unsigned multisig variant, kept for if a wallet ever
ships multisig signing.

A new deployment starts **unfrozen**: its creator can still replace the
programs. That is deliberate while nobody depends on it, and it is given up
with `scripts/govern.py freeze` before anybody is asked to.
"""

from __future__ import annotations

import argparse
import base64
import logging
import pathlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import algokit_utils
from algosdk import logic, transaction
from algosdk.v2client.models import SimulateRequest, SimulateRequestTransactionGroup

from scripts import multisig as ms, network as net
from scripts.govern import PROGRAM_PAGE, _create_shape, _deployed, _frozen
from scripts.registry_health import read_solvency
from scripts.verify_build import REPO, _digest, _programs, _spec, rebuild

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# What an app account needs before it can hold anything at all.
BASE_MBR = 100_000

#: A global-state key each contract writes in `__init__`. An app whose global
#: state carries it is that contract whatever its bytecode, which is how a
#: creator's existing deployments are recognised without the indexer.
MARKERS: dict[str, bytes] = {"keeper": b"next_upkeep_id", "pulse": b"beats"}
KEEPER_MARKER = MARKERS["keeper"]

#: Contracts that carry the `frozen` flag, and so are expected to read 0 on a
#: fresh create. Pulse has no governance and no such key.
GOVERNED = frozenset({"keeper"})

#: Rounds to wait for a create to confirm before giving up on the read-back.
CONFIRMATION_ROUNDS = 6

#: The check every MainNet script applies; here so the ceremony can list it
#: among its own refusals and a test can exercise the wiring.
mnemonic_written_to = net.mnemonic_written_to


class Unconfirmed(RuntimeError):
    """A create was sent and its confirmation did not arrive in time.

    Carries the transaction id, because the app may well exist: a slow public
    node is the usual reason, and the operator's next move is to look rather
    than to run the create again.
    """

    def __init__(self, txid: str, contract: str) -> None:
        super().__init__(txid)
        self.txid = txid
        self.contract = contract


@dataclass(frozen=True)
class TreeState:
    """What git says about the tree the programs were compiled from."""

    commit: str
    tag: str | None
    dirty: bool


def tree_state(repo: pathlib.Path = REPO) -> TreeState:
    """Commit, exact tag if any, and whether anything is uncommitted.

    `cwd` is passed on every call: run from anywhere else and git reports on a
    different repository, or none, and a dirty tree looks clean.
    """

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

    commit = git("rev-parse", "HEAD").stdout.strip()
    tagged = git("describe", "--tags", "--exact-match", "HEAD")
    tag = tagged.stdout.strip() if tagged.returncode == 0 and tagged.stdout.strip() else None
    dirty = bool(git("status", "--porcelain").stdout.strip())
    return TreeState(commit=commit, tag=tag, dirty=dirty)


@dataclass(frozen=True)
class Plan:
    """Everything a create fixes forever, resolved before anything is sent."""

    network: str
    genesis: str
    creator: str
    approval: bytes
    clear: bytes
    shape: dict
    tree: TreeState
    contract: str = "keeper"

    @property
    def digest(self) -> str:
        return _digest(self.approval, self.clear)

    @property
    def extra_pages(self) -> int:
        return int(self.shape["extra pages"])

    @property
    def marker(self) -> bytes:
        return MARKERS[self.contract]

    @property
    def has_freeze(self) -> bool:
        return self.contract in GOVERNED

    @property
    def global_schema(self) -> transaction.StateSchema:
        return transaction.StateSchema(
            self.shape["global uints"], self.shape["global byte slices"]
        )

    @property
    def local_schema(self) -> transaction.StateSchema:
        return transaction.StateSchema(
            self.shape["local uints"], self.shape["local byte slices"]
        )


def plan_for(
    network: str, genesis: str, creator: str, tree: TreeState, contract: str = "keeper"
) -> Plan:
    """Derive the plan from the committed build, the same way `govern create` does."""
    spec = _spec(contract)
    approval, clear = _programs(spec)
    return Plan(
        network=network,
        genesis=genesis,
        creator=creator,
        approval=approval,
        clear=clear,
        shape=_create_shape(spec, approval, clear),
        tree=tree,
        contract=contract,
    )


def _global_keys(app: dict) -> set[bytes]:
    keys: set[bytes] = set()
    for entry in app.get("params", {}).get("global-state", []) or []:
        try:
            keys.add(base64.b64decode(entry["key"]))
        except Exception:  # a malformed entry is not a marker
            continue
    return keys


def find_keepers(
    algod, creator: str, digest: str | None = None, marker: bytes = KEEPER_MARKER
) -> list[int]:
    """App ids this creator has already made that carry `marker`, or these programs.

    Read from algod's view of the account rather than from the indexer, so a
    create that confirmed seconds ago is already counted. An app counts if its
    global state carries the marker, or if its programs are the ones this tree
    builds; the second catches an app whose `__init__` has not been observed
    for any reason. Named for the keeper, which is what it was written for;
    `marker` makes it serve Pulse too.
    """
    info = algod.account_info(creator)
    found: list[int] = []
    for app in info.get("created-apps", []) or []:
        params = app.get("params", {})
        is_match = marker in _global_keys(app)
        if not is_match and digest is not None:
            try:
                live = _digest(
                    base64.b64decode(params["approval-program"]),
                    base64.b64decode(params["clear-state-program"]),
                )
                is_match = live == digest
            except Exception:
                is_match = False
        if is_match:
            found.append(int(app["id"]))
    return sorted(found)


def refusals(
    plan: Plan,
    *,
    existing_keepers: list[int],
    allow_dirty: bool,
    allow_another: bool,
    mnemonic_on_disk: bool,
) -> list[str]:
    """Every reason not to create, so the operator sees all of them at once.

    Pure, like `multisig.refusals`: the chain reads happen before, the
    decisions happen here, and a test can exercise each decision without a
    node. The MainNet rules have no override flag on purpose; what they guard
    is permanent.
    """
    reasons: list[str] = []
    mainnet = plan.network == net.MAINNET

    if plan.tree.dirty and not allow_dirty:
        reasons.append(
            "the working tree has uncommitted changes, so the digest below would not "
            "correspond to any commit anyone can check out. Commit and tag first, or "
            "pass --allow-dirty if you truly mean to (never on MainNet)."
        )
    if mainnet and plan.tree.dirty:
        reasons.append("MainNet is never created from a dirty tree, --allow-dirty or not.")
    if mainnet and plan.tree.tag is None:
        reasons.append(
            f"MainNet is created from a tag, and {plan.tree.commit[:7]} has none. "
            "docs/releases.md records a tag per stage; `git tag mainnet-1` on the "
            "commit that produced this bytecode, then run this again."
        )
    if mainnet:
        try:
            net.require_mainnet_creator(plan.creator)
        except RuntimeError as refusal:
            reasons.append(str(refusal))
    if mainnet and mnemonic_on_disk:
        reasons.append(
            "DEPLOYER_MNEMONIC is written in .env.mainnet. The creator key is exported "
            "into the ceremony's shell (read -rs DEPLOYER_MNEMONIC; export it) and never "
            "kept in a file, because that file stays on the machine that later runs "
            "`health`. Remove the line and run this again."
        )
    if existing_keepers:
        ids = ", ".join(str(i) for i in existing_keepers)
        if mainnet:
            reasons.append(
                f"{plan.creator[:8]}… has already created {plan.contract} app(s) {ids} on "
                f"MainNet. There is one MainNet {plan.contract}. If this is a replacement, "
                "that is a migration and not a create: see docs/security.md, 'If a bug is "
                "found'."
            )
        elif not allow_another:
            reasons.append(
                f"{plan.creator[:8]}… has already created {plan.contract} app(s) {ids} on "
                f"{plan.network}. Pass --another to create a second one on purpose, "
                "which a rehearsal may well want."
            )
    return reasons


def describe(plan: Plan) -> list[str]:
    """Every permanent field, plus what proves it, as lines to print.

    Listed rather than trusted: printing a value asks somebody to compare it,
    and the read-back in `postflight` is that comparison done by the script.
    """
    tree = plan.tree
    tag = tree.tag or "(untagged)"
    dirty = " (DIRTY)" if tree.dirty else ""
    return [
        f"This {plan.contract} create is permanent in every field below.",
        f"  network       {plan.network} ({plan.genesis})",
        f"  creator       {plan.creator}",
        f"  programs      {len(plan.approval)} + {len(plan.clear)} bytes",
        f"  combined      sha256 {plan.digest}",
        f"  commit        {tree.commit}{dirty}",
        f"  tag           {tag}",
        f"  extra pages   {plan.extra_pages}  "
        f"(capacity {PROGRAM_PAGE * (1 + plan.extra_pages)} bytes)",
        f"  global state  {plan.shape['global uints']} uints, "
        f"{plan.shape['global byte slices']} byte slices",
        f"  local state   {plan.shape['local uints']} uints, "
        f"{plan.shape['local byte slices']} byte slices",
        "  The creator pays 0.1 ALGO of minimum balance per program page, forever.",
        "  None of these can be changed later. `update` replaces code, nothing else.",
    ]


def confirm(plan: Plan, assume_yes: bool, ask: Callable[[str], str] | None = None) -> bool:
    """Make the operator type the creator address back.

    `--yes` is honoured on LocalNet and TestNet, where a rehearsal may run
    unattended. On MainNet it is ignored rather than refused, so a script that
    passes it everywhere still stops here for the one create that matters.
    """
    if assume_yes and plan.network != net.MAINNET:
        return True
    # Resolved at call time rather than bound as a default, so a test (or a
    # wrapper) that replaces `input` actually replaces it.
    answer = (ask or input)("  Type the creator address to continue: ").strip()
    return answer == plan.creator


def build_create(plan: Plan, params) -> transaction.ApplicationCreateTxn:
    """The transaction, from the same shape that was described and will be checked."""
    return transaction.ApplicationCreateTxn(
        sender=plan.creator,
        sp=params,
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=plan.approval,
        clear_program=plan.clear,
        global_schema=plan.global_schema,
        local_schema=plan.local_schema,
        extra_pages=plan.extra_pages,
    )


def simulate(algod, signed: transaction.SignedTransaction) -> None:
    """Ask the node whether the create would succeed, before it is sent.

    A schema too small for `__init__` or a program the node will not assemble
    fails here for the price of a request, instead of failing on chain for the
    price of a fee, or, worse, succeeding with a shape nobody meant.
    """
    request = SimulateRequest(
        txn_groups=[SimulateRequestTransactionGroup(txns=[signed])],
    )
    response = algod.simulate_transactions(request)
    group = (response.get("txn-groups") or [{}])[0]
    failure = group.get("failure-message")
    if failure:
        raise RuntimeError(f"the create would fail: {failure}")


def create(algod, deployer, plan: Plan) -> tuple[int, str]:
    """Sign, simulate, send, confirm. Returns the new app id and the txid.

    Raises `RuntimeError` from `simulate` before anything is sent, and
    `Unconfirmed` if the send went out and the confirmation did not come back
    within `CONFIRMATION_ROUNDS`: on a slow public node that is a create that
    probably landed, and the caller must say so rather than let a traceback
    imply nothing happened.
    """
    params = algod.suggested_params()
    signed = build_create(plan, params).sign(deployer.private_key)
    simulate(algod, signed)
    txid = algod.send_transaction(signed)
    try:
        confirmed = transaction.wait_for_confirmation(algod, txid, CONFIRMATION_ROUNDS)
    except Exception as error:  # timeout, or a node that stopped answering
        raise Unconfirmed(txid, plan.contract) from error
    return int(confirmed["application-index"]), txid


def postflight(algod, app_id: int, plan: Plan) -> list[str]:
    """Read every permanent field back from the chain and name each mismatch.

    Empty means the app on chain is exactly the one that was described. Any
    line here is serious: the app already exists and nothing about it can be
    changed except its code.
    """
    info = algod.application_info(app_id)
    params = info["params"]
    mismatches: list[str] = []

    creator = params.get("creator")
    if creator != plan.creator:
        mismatches.append(f"creator is {creator}, expected {plan.creator}")

    pages = int(params.get("extra-program-pages", 0) or 0)
    if pages != plan.extra_pages:
        mismatches.append(f"extra program pages is {pages}, expected {plan.extra_pages}")

    g = params.get("global-state-schema", {}) or {}
    l = params.get("local-state-schema", {}) or {}
    have_global = (int(g.get("num-uint", 0)), int(g.get("num-byte-slice", 0)))
    have_local = (int(l.get("num-uint", 0)), int(l.get("num-byte-slice", 0)))
    want_global = (plan.shape["global uints"], plan.shape["global byte slices"])
    want_local = (plan.shape["local uints"], plan.shape["local byte slices"])
    if have_global != want_global:
        mismatches.append(f"global schema is {have_global}, expected {want_global}")
    if have_local != want_local:
        mismatches.append(f"local schema is {have_local}, expected {want_local}")

    live_approval, live_clear = _deployed(algod, app_id)
    if _digest(live_approval, live_clear) != plan.digest:
        mismatches.append("the deployed programs are not the ones this tree builds")

    if plan.has_freeze:
        frozen = _frozen(algod, app_id)
        if frozen != 0:
            mismatches.append(f"frozen is {frozen}, expected 0 on a fresh deployment")

    return mismatches


def fund_floor(algorand, deployer, app_address: str) -> int:
    """Bring the app account to the 0.1 ALGO it needs to hold anything; returns what was sent."""
    algod = algorand.client.algod
    amount = int(algod.account_info(app_address)["amount"])
    if amount >= BASE_MBR:
        return 0
    short = BASE_MBR - amount
    logger.info(f"Funding the app account with {short:,} µALGO of base minimum balance")
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=short),
        )
    )
    return short


def _shout_mismatches(app_id: int, contract: str, mismatches: list[str]) -> None:
    logger.error(f"{contract.upper()} APP {app_id} EXISTS AND IS NOT WHAT WAS DESCRIBED:")
    for mismatch in mismatches:
        logger.error(f"  - {mismatch}")
    logger.error("Do not use it. Do not put its id anywhere. See docs/security.md.")


def _unconfirmed(error: Unconfirmed, network: str) -> None:
    logger.error(
        f"The {error.contract} create was SENT as {error.txid} and did not confirm within "
        f"{CONFIRMATION_ROUNDS} rounds. It may well have landed. Do not run this again "
        "until you know."
    )
    logger.error(
        f"  Look it up: the creator's account on an explorer, or "
        f"`govern status --network {network} --app-id <id>` once the id is known. "
        "The app account floor has NOT been funded and nothing has been read back."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    net.add_network_argument(parser)
    parser.add_argument("--with-pulse", action="store_true", help="also deploy the demo target")
    parser.add_argument("--no-rebuild", action="store_true", help="trust the built artifacts")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="create from a tree with uncommitted changes (refused on MainNet regardless)",
    )
    parser.add_argument(
        "--another",
        action="store_true",
        help="create even though this creator already has one here (never on MainNet)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed confirmation on LocalNet and TestNet; MainNet always asks",
    )
    args = parser.parse_args(argv)

    if ms.configured():
        # A multisig cannot sign in process, so creating from one is a separate
        # flow: `govern create` is it. Do not point anyone at
        # `scripts/multisig_e2e.py`, which this comment used to: that script
        # generates three throwaway keys and drops them when it exits, so on a
        # real network it makes an app whose creator nobody holds.
        logger.error(
            f"A multisig is configured ({ms.describe()}), and this command signs "
            "in process. Use `poetry run python -m scripts.govern create` instead; "
            "see docs/deploying.md."
        )
        return 1

    if not args.no_rebuild:
        rebuild()

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")
    genesis = algod.suggested_params().gen
    tree = tree_state()
    plan = plan_for(args.network, genesis, deployer.address, tree)

    for line in describe(plan):
        logger.info(line)

    mnemonic_on_disk = mnemonic_written_to(REPO / f".env.{args.network}")
    reasons = refusals(
        plan,
        existing_keepers=find_keepers(algod, plan.creator, plan.digest),
        allow_dirty=args.allow_dirty,
        allow_another=args.another,
        mnemonic_on_disk=mnemonic_on_disk,
    )
    pulse_plan = None
    if args.with_pulse:
        pulse_plan = plan_for(args.network, genesis, deployer.address, tree, contract="pulse")
        reasons += refusals(
            pulse_plan,
            existing_keepers=find_keepers(
                algod, plan.creator, pulse_plan.digest, marker=pulse_plan.marker
            ),
            allow_dirty=args.allow_dirty,
            allow_another=args.another,
            mnemonic_on_disk=mnemonic_on_disk,
        )
    if reasons:
        logger.error("Refusing to create:")
        for reason in dict.fromkeys(reasons):  # each once, in order
            logger.error(f"  - {reason}")
        return 1

    if not confirm(plan, args.yes):
        logger.info("Not created.")
        return 1

    try:
        app_id, txid = create(algod, deployer, plan)
    except Unconfirmed as error:
        _unconfirmed(error, args.network)
        return 1
    except RuntimeError as refusal:
        logger.error(f"Refusing to create: {refusal}")
        return 1
    app_address = logic.get_application_address(app_id)
    logger.info(f"Created keeper app {app_id} in {txid}")

    fund_floor(algorand, deployer, app_address)

    mismatches = postflight(algod, app_id, plan)
    if mismatches:
        _shout_mismatches(app_id, "keeper", mismatches)
        return 1

    solvency = read_solvency(algod, app_id, 0)

    pulse_id = None
    if pulse_plan is not None:
        try:
            pulse_id, pulse_txid = create(algod, deployer, pulse_plan)
        except Unconfirmed as error:
            _unconfirmed(error, args.network)
            logger.error(f"The keeper, app {app_id}, is created, funded and verified regardless.")
            return 1
        except RuntimeError as refusal:
            logger.error(f"Refusing to create Pulse: {refusal}")
            logger.error(f"The keeper, app {app_id}, is created, funded and verified regardless.")
            return 1
        logger.info(f"Created pulse app {pulse_id} in {pulse_txid}")
        pulse_mismatches = postflight(algod, pulse_id, pulse_plan)
        if pulse_mismatches:
            _shout_mismatches(pulse_id, "pulse", pulse_mismatches)
            return 1

    logger.info("")
    logger.info(f"Keeper app {app_id} on {args.network}")
    logger.info(f"  address   {app_address}")
    logger.info(f"  creator   {plan.creator}")
    logger.info(f"  approval  {len(plan.approval)} bytes")
    logger.info(f"  sha256    {plan.digest}")
    logger.info(f"  commit    {plan.tree.commit}  tag {plan.tree.tag or '(untagged)'}")
    logger.info(f"  pages     1 + {plan.extra_pages} extra")
    logger.info("  frozen    0 (the creator can still update)")
    logger.info(f"  escrow    0 ALGO owed, {solvency.spendable / 1e6:.3f} ALGO spendable")
    if pulse_id:
        logger.info(f"  pulse     {pulse_id}")
    logger.info("")
    logger.info("Verified: creator, pages, schema and programs read back as described.")
    if args.network == net.MAINNET:
        logger.info("Record the id and sha256 privately. Do not put the id in the tree,")
        logger.info("the console or a post until the deployment is frozen.")
    else:
        logger.info("Record the app id and sha256 in docs/releases.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
