"""Network selection for the Arcron scripts.

Every script picks its network with `--network` (or `ARCRON_NETWORK`), which
loads the matching `.env.<network>` file *before* algokit-utils reads the
environment, then verifies the node it reached really is that network.

LocalNet needs no secrets: accounts come from KMD, funded by the LocalNet
dispenser. TestNet needs `.env.testnet` with `DEPLOYER_MNEMONIC`.
"""

import argparse
import logging
import os
import pathlib
import re

import algokit_utils
from dotenv import load_dotenv

from scripts import node_retry

logger = logging.getLogger(__name__)

LOCALNET = "localnet"
TESTNET = "testnet"
MAINNET = "mainnet"
NETWORKS = (LOCALNET, TESTNET, MAINNET)

# Genesis ids a node may report for each network. AlgoKit LocalNet reports
# "dockernet-v1"; the older sandbox reported "sandnet-v1".
_GENESIS_IDS = {
    LOCALNET: ("dockernet-v1", "sandnet-v1", "devnet-v1"),
    TESTNET: ("testnet-v1.0",),
    MAINNET: ("mainnet-v1.0",),
}


# The Algorand Foundation's randomness beacon, per network. Recorded once
# here because the id was already written out in four other places, and a
# fifth copy is how the number quietly becomes wrong somewhere. Any contract
# naming a different beacon is one whose deployer chose who wins.
FOUNDATION_BEACON = {
    TESTNET: 600_011_887,
    MAINNET: 1_615_566_206,
}


#: Seconds per round, measured per network rather than assumed.
#:
#: Algorand's nominal block time is 2.8, and this repository used it in three
#: places while `docs/why.md` used 2.66 from a 45 second sample. Measured over
#: 1,000,000 rounds, about 31 days, on 2026-08-28:
#:
#:     TestNet  2.695    MainNet  2.752
#:
#: They genuinely differ, so one constant is wrong for one of them, and 2.8 is
#: about 4% slow for both. On a daily cadence that compounds into an hour.
#:
#: LocalNet keeps the nominal figure because dev mode has no block time at all:
#: a block is produced per transaction, so elapsed wall clock says nothing.
#: `is_dev_mode` is how a caller finds out; this is only so a cadence can still
#: be printed as a duration.
ROUND_SECONDS: dict[str, float] = {
    LOCALNET: 2.8,
    TESTNET: 2.695,
    MAINNET: 2.752,
}


def seconds_per_round(network: str) -> float:
    """How long a round takes on this network.

    A measured default, not a measurement. Anything reporting a schedule over a
    long horizon should measure the chain it is talking to; this is the honest
    starting point, and it is per network because the networks differ.
    """
    return ROUND_SECONDS.get(network, ROUND_SECONDS[MAINNET])


def genesis_ids(network: str) -> tuple[str, ...]:
    """Every genesis id that counts as this network.

    LocalNet answers to several depending on how it was started, which is why
    this is a tuple rather than a single string.
    """
    return _GENESIS_IDS[network]


def default_network() -> str:
    """The network to use when no flag is given (`ARCRON_NETWORK`, else TestNet)."""
    return os.environ.get("ARCRON_NETWORK", TESTNET)


def add_network_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--network",
        choices=NETWORKS,
        default=default_network(),
        help="network to talk to (default: %(default)s; env ARCRON_NETWORK)",
    )


# The account the MainNet deployment is created from, and therefore the only
# account that can ever replace its programs. An app's creator is fixed at
# creation, so a MainNet app made from anything else is the admin-key problem
# permanently, with no way back.
#
# A single account, `corvid.algo`, decided 2026-08-29. This replaces the 2-of-3
# from issue #79, and the reason is not convenience.
#
# Wallets do not sign for multisig. Tested on TestNet against a real member
# account, asked directly through Pera's own SDK with `msig` metadata and with
# use-wallet's filter bypassed, Pera answers "multisig signing is not
# supported". ARC-1 defines that field, use-wallet exports the type and
# implements none of it, and Pera's SDK declares it and refuses it. So a 2-of-3
# means every governance action is a mnemonic pasted into a shell by three
# people, including one whose key lives on a hardware device precisely so that
# never has to happen.
#
# The trade, stated rather than assumed. A single key can replace the programs
# governing every upkeep's escrow while `frozen == 0`. That is a real loss, and
# `docs/deploying.md` said a single mnemonic on a single machine is the wrong
# home for such a key. What makes it defensible is that `freeze` is one way and
# retires the key permanently: a single-key deployment frozen early is a smaller
# exposure than a 2-of-3 left upgradeable because signing is too painful to
# actually do. The commitment that goes with this decision is to freeze before
# anyone who is not us escrows, and in any case before the deployment is
# announced (docs/design/mainnet-rollout.md); the console discloses
# upgradeable status on every page until then.
#
# `scripts/multisig.py` is kept and still works. If a wallet ever ships multisig
# signing, this is one constant away from going back.
MAINNET_CREATOR = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"


def require_mainnet_creator(signer_address: str | None = None) -> None:
    """Refuse MainNet unless the signer is the account MainNet is deployed from.

    `ARCRON_ALLOW_MAINNET=1` was the entire gate, and a shell that exports it
    once turns `--network mainnet` back into an ordinary argument. The flag
    stops a typo; it does nothing about the thing that actually matters, which
    is which account signs.

    This used to require a multisig. It now requires the *right account*, which
    is the part that was ever load-bearing: a MainNet app's creator is fixed at
    creation, so deploying from the wrong key is unfixable whether that key is
    one account or three. Whether the creator is a multisig is a separate
    decision, recorded above `MAINNET_CREATOR`.

    A multisig still satisfies this, so nothing has to change if one is ever
    configured again: `scripts.multisig` derives an address like any other, and
    if it equals `MAINNET_CREATOR` this passes. Read lazily to avoid a circular
    import, since that module imports this one.

    Checked here rather than in each script so it applies to every entry point
    at once, including ones written later.
    """
    if signer_address is None:
        from scripts import multisig as ms

        signer_address = ms.address() if ms.configured() else None

    if signer_address is None:
        raise RuntimeError(
            "Refusing MainNet without knowing which account will sign. An app's "
            "creator cannot be changed after creation, so deploying from the wrong "
            "key holds an admin key over every escrow in the app for as long as it "
            f"exists. MainNet must be created by {MAINNET_CREATOR}."
        )
    if signer_address != MAINNET_CREATOR:
        raise RuntimeError(
            f"Refusing MainNet: the configured signer is {signer_address}, not the "
            f"expected {MAINNET_CREATOR}. A creator is fixed at creation, so this is "
            "the one mistake with no way back. Check docs/deploying.md."
        )


#: The old name, kept so nothing that imports it breaks while docs catch up.
require_mainnet_multisig = require_mainnet_creator


#: The shape of a line that puts the creator key on disk. Tolerates the forms
#: python-dotenv accepts (spaces around `=`, `export`, quotes, a BOM), because
#: what matters is whether dotenv would load it, not how it was typed.
_MNEMONIC_LINE = re.compile(r"^[\s\ufeff]*(?:export\s+)?DEPLOYER_MNEMONIC\s*=\s*\S")


def mnemonic_written_to(env_file: "pathlib.Path | str") -> bool:
    """Whether a creator mnemonic sits in an env file.

    Reads the file for the shape of the line only; the value is never logged.
    `.env.mainnet` is meant to carry the node and the app id, both of which are
    public, so the file can stay on the machine that runs `health`. The
    mnemonic is exported for a ceremony and is gone when the shell is.
    """
    path = pathlib.Path(env_file)
    if not path.exists():
        return False
    return any(_MNEMONIC_LINE.match(line) for line in path.read_text().splitlines())


def refuse_algokit_create_on_mainnet(genesis: str, contract: str) -> None:
    """Keep the algokit deploy path off MainNet, now that a checked one exists.

    `smart_contracts/<contract>/deploy_config.py` is `factory.deploy(...)` with
    `AppendApp`: it finds an existing app through the indexer and creates a
    second one when the indexer is behind, checks none of the fields a create
    fixes forever, and asks nobody. It is reachable from `algokit project
    deploy`, `python -m smart_contracts deploy` and the soak script, none of
    which pass through `scripts/deploy.py`. So it refuses MainNet by genesis
    id, which is what the node says rather than what an argument claims.
    """
    if genesis in _GENESIS_IDS[MAINNET]:
        raise RuntimeError(
            f"Refusing to create {contract} on MainNet from the algokit deploy path. "
            "It cannot check what a create fixes forever and can create a second app "
            "when the indexer is behind. MainNet is created by `fledge run "
            "deploy-mainnet` (scripts/deploy.py); see docs/deploying.md."
        )


def load_network(network: str) -> str:
    """Load `.env.<network>`; returns the network name.

    Exported environment variables win over the file, as dotenv normally
    behaves — `assert_network` catches the case where that points the script
    at the wrong chain. A deployment that configures everything through the
    environment (a container, a systemd unit) needs no file at all.
    """
    if network not in NETWORKS:
        raise ValueError(f"Unknown network {network!r}; expected one of {NETWORKS}")
    if network == MAINNET and os.environ.get("ARCRON_ALLOW_MAINNET") != "1":
        # A typo in --network should not reach real money. Nothing in this repo
        # sets this, so choosing MainNet has to be a separate, deliberate act.
        raise RuntimeError(
            "Refusing to talk to MainNet unless ARCRON_ALLOW_MAINNET=1 is set. "
            "See docs/releases.md: MainNet is gated behind the rc clock, and "
            "nothing here should reach it by accident."
        )
    env_file = f".env.{network}"
    if network == MAINNET and mnemonic_written_to(env_file):
        # Before dotenv reads it, so the key never even enters this process's
        # environment. Every script reaches MainNet through here, which is what
        # makes this the one place the rule has to be said.
        raise RuntimeError(
            "Refusing MainNet: .env.mainnet carries a DEPLOYER_MNEMONIC line. That "
            "file stays on the machine that runs `health`, so the creator key is "
            "exported into the shell that needs it (read -rs DEPLOYER_MNEMONIC; export "
            "DEPLOYER_MNEMONIC) and never written there. Remove the line."
        )
    loaded = load_dotenv(env_file)
    # Creator check is *not* here. A keeper is a hot key, not `corvid.algo`.
    # Health, notifier, and `keeper_bot --network mainnet` have to connect
    # without holding the admin key. `require_mainnet_creator` belongs on
    # create (and on update/freeze that sign as DEPLOYER), which is
    # `scripts/deploy.py` and `smart_contracts/keeper/deploy_config.py`.
    if not loaded and network != LOCALNET and not os.environ.get("ALGOD_SERVER"):
        # Two audiences, and the old message only served one. From a checkout
        # the answer is a file; from a container or a systemd unit there is no
        # checkout to copy anything into, and the answer is the environment.
        # Telling an operator watching `docker compose logs` to copy a template
        # sends them looking for a directory that is not there.
        in_container = pathlib.Path("/.dockerenv").exists()
        remedy = (
            "set ALGOD_SERVER in the environment. In Docker that is "
            "deploy/keeper.env, which deploy/keeper.env.example shows in full; "
            "check it exists and that ALGOD_SERVER is not blank"
            if in_container
            else f"copy {env_file}.template to {env_file} and fill it in, "
            f"or set ALGOD_SERVER in the environment"
        )
        raise FileNotFoundError(
            f"No Algorand node configured for {network}: {env_file} is absent "
            f"and ALGOD_SERVER is unset. To fix: {remedy}."
        )
    return network


def assert_network(algod: object, network: str) -> None:
    """Fail loudly if the connected node is not the network we asked for."""
    genesis = algod.suggested_params().gen  # type: ignore[attr-defined]
    expected = _GENESIS_IDS[network]
    if genesis not in expected:
        raise RuntimeError(
            f"Asked for {network} but the node at ALGOD_SERVER reports genesis "
            f"{genesis!r} (expected one of {expected}). Check .env.{network} "
            f"and any exported ALGOD_* variables."
        )
    logger.info(f"Network: {network} ({genesis})")


def is_dev_mode(algod: object) -> bool:
    """True on a dev-mode node, where a block is only produced per transaction."""
    genesis = algod.suggested_params().gen  # type: ignore[attr-defined]
    return genesis in _GENESIS_IDS[LOCALNET]


def connect(network: str) -> "algokit_utils.AlgorandClient":
    """Load the network's env file and return a verified AlgorandClient.

    The clients this hands out retry a refused request rather than raising it;
    `scripts/node_retry.py` records what was measured and why that belongs to
    the connection rather than to each caller. Every script in this repository
    reaches a node through here, so installing it once covers all of them,
    including the unattended ones.
    """
    load_network(network)
    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never build transactions from stale
    # cached suggested params.
    algorand.set_suggested_params_cache_timeout(0)
    node_retry.install(algorand.client.algod)
    # `indexer_if_present`, not `indexer`: the latter raises when no indexer is
    # configured, which is the normal case on LocalNet, and a health check for
    # TestNet must not be what stops a LocalNet script from connecting.
    node_retry.install(algorand.client.indexer_if_present)
    # After installing, so that even the genesis check survives a refusal. It
    # is the first request any script makes, and it was one of the two that
    # threw on 2026-09-01.
    assert_network(algorand.client.algod, network)
    return algorand


def wait_for_round(
    algorand: "algokit_utils.AlgorandClient",
    target_round: int,
    poker: "algokit_utils.SigningAccount | None" = None,
) -> int:
    """Block until the chain reaches `target_round`; returns the round reached.

    On a dev-mode node no blocks are produced on their own, so `poker` sends
    zero-amount self-payments to advance the chain one round at a time.
    """
    algod = algorand.client.algod
    dev_mode = is_dev_mode(algod)
    while True:
        current = algod.status()["last-round"]
        if current >= target_round:
            return current
        if dev_mode:
            if poker is None:
                raise ValueError("A poker account is required to advance dev-mode rounds")
            algorand.send.payment(
                algokit_utils.PaymentParams(
                    sender=poker.address,
                    receiver=poker.address,
                    amount=algokit_utils.AlgoAmount(micro_algo=0),
                    note=b"arcron: advance round",
                )
            )
        else:
            logger.info(f"  round {current}, waiting for {target_round}…")
            algod.status_after_block(current + 1)
