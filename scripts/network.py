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

import algokit_utils
from dotenv import load_dotenv

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
# A 2-of-3, decided 2026-08-27 in issue #79. Member order is part of the
# address: the same three keys in a different order derive a different account,
# so the order below is the order, and it is Ledger, Corvid, Gaspar.
#
# The trade, stated rather than assumed: 2-of-3 survives one lost key and needs
# two to collude. The 3-of-5 this replaces survived two losses and needed three.
# Both margins are smaller. That is the price of three people instead of five,
# and it resolves a contradiction rather than creating one: docs/security.md and
# docs/deploying.md already described a 2-of-3 while this constant and issue #79
# said 3-of-5.
MAINNET_CREATOR = "LUH77ATPWS4ZTCO7OZ3YM2DP5M2BXN53CHPFFQCFBATRFCYEB3NKTGMBNI"


def require_mainnet_multisig() -> None:
    """Refuse MainNet unless the configured signer is the 2-of-3.

    `ARCRON_ALLOW_MAINNET=1` was the entire gate, and a shell that exports it
    once turns `--network mainnet` back into an ordinary argument. The flag
    stops a typo; it does nothing about the thing that actually matters, which
    is which account signs.

    Checked here rather than in each script so it applies to every entry point
    at once, including ones written later. Read lazily to avoid a circular
    import: `scripts.multisig` imports this module.
    """
    from scripts import multisig as ms

    if not ms.configured():
        raise RuntimeError(
            "Refusing MainNet without a configured multisig. An app's creator cannot be "
            "changed after creation, so a MainNet app deployed from a single key holds "
            "an admin key over every escrow in it for as long as it exists. Set "
            "ARCRON_MULTISIG_ADDRESSES and ARCRON_MULTISIG_THRESHOLD."
        )
    if ms.address() != MAINNET_CREATOR:
        raise RuntimeError(
            f"Refusing MainNet: the configured multisig is {ms.address()}, not the "
            f"expected {MAINNET_CREATOR}. Member order is part of a multisig address, "
            "so the same keys in a different order are a different account holding "
            "nothing. Check ARCRON_MULTISIG_ADDRESSES against docs/security.md."
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
    loaded = load_dotenv(env_file)
    if network == MAINNET:
        require_mainnet_multisig()
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
            else f"copy .env.testnet.template to {env_file} and fill it in, "
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
    """Load the network's env file and return a verified AlgorandClient."""
    load_network(network)
    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never build transactions from stale
    # cached suggested params.
    algorand.set_suggested_params_cache_timeout(0)
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
